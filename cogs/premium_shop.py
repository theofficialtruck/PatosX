# PatosX, a multipurpose Discord bot (moderation, economy, AI, fun)
# Copyright (C) 2025 theofficialtruck
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Premium shop: a limited-stock shop that appears in each guild's economy channel at random
times for a short while, selling boosted single-use versions of a few regular shop items.

Per guild, ``state.premium_shop_col`` holds one document (``_id`` = guild id) with the schedule
(``is_open``, ``next_open_at``, ``closes_at``), the announcement message and the live ``items``
stock. Stock is shared by the whole guild and decremented atomically, so it can run out.
"""

import random
import re
import traceback
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from core import config as cfg
from core import economyHelperFuncs as econ
from core import state
from core import xpHelperFuncs as xp

BUY_BUTTON_ID = "premium_shop:open"


# ============================================================
# Pure helpers
# ============================================================


def roll_stock(rng=random) -> list[dict]:
    """Pick a random subset of the premium items and give each a random stock within its range."""
    low, high = cfg.PREMIUM_SHOP_ITEMS_PER_OPENING
    count = min(rng.randint(low, high), len(cfg.PREMIUM_SHOP_ITEMS))
    keys = rng.sample(list(cfg.PREMIUM_SHOP_ITEMS), count)
    return [{"key": key, "stock": rng.randint(*cfg.PREMIUM_SHOP_ITEMS[key]["stock_range"])} for key in keys]


def roll_gap(rng=random) -> timedelta:
    """Random wait between one premium shop closing and the next one opening."""
    low, high = cfg.PREMIUM_SHOP_GAP_HOURS
    return timedelta(hours=rng.uniform(low, high))


def roll_open_duration(rng=random) -> timedelta:
    """Random length of a single premium shop opening."""
    low, high = cfg.PREMIUM_SHOP_OPEN_MINUTES
    return timedelta(minutes=rng.uniform(low, high))


def parse_time(raw) -> datetime | None:
    """Parse a stored ISO timestamp as an aware UTC datetime, or None when missing or malformed."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_sold_out(items: list[dict]) -> bool:
    return all(int(item.get("stock", 0)) <= 0 for item in items)


def is_open_now(doc: dict | None, now: datetime) -> bool:
    """True while the guild's premium shop is open, unsold out and not past its closing time."""
    if not doc or not doc.get("is_open"):
        return False
    closes_at = parse_time(doc.get("closes_at"))
    return closes_at is not None and now < closes_at and not is_sold_out(doc.get("items", []))


def economy_channel_id(config: dict) -> int | None:
    """First channel id configured as the guild's economy channel, or None when unrestricted."""
    value = config.get("economy_channel")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        ids = re.findall(r"\d+", value)
        return int(ids[0]) if ids else None
    if isinstance(value, list):
        ids = [int(x) for x in value if str(x).isdigit()]
        return ids[0] if ids else None
    return None


def build_embed(items: list[dict], closes_at: datetime, *, closed: bool = False) -> discord.Embed:
    """The announcement embed, showing each item's price and remaining stock."""
    if closed:
        embed = discord.Embed(
            title="💎 Premium Shop Closed",
            description="The premium shop has packed up. Watch this channel, it will be back at a random time!",
            color=discord.Color.dark_grey(),
        )
    else:
        embed = discord.Embed(
            title="💎 Premium Shop is OPEN!",
            description=(
                f"Boosted items, single use each, limited stock for the **whole server**.\n"
                f"Closes <t:{int(closes_at.timestamp())}:R>. Purchases use wallet coins only and can't be refunded."
            ),
            color=discord.Color.gold(),
        )
    for item in [] if closed else items:
        spec = cfg.PREMIUM_SHOP_ITEMS.get(item["key"])
        if spec is None:
            continue
        stock = int(item.get("stock", 0))
        stock_text = f"**{stock} left**" if stock > 0 else "**SOLD OUT**"
        embed.add_field(
            name=f"{spec['emoji']} {spec['name']} - 🪙 {spec['price']:,}",
            value=f"{spec['description']}\n{stock_text}",
            inline=False,
        )
    return embed


def build_inventory_entry(key: str):
    """The inventory entry a purchased premium item is stored as."""
    spec = cfg.PREMIUM_SHOP_ITEMS[key]
    if spec["inventory_kind"] == "dict":
        return {"_id": key, "uses_left": 1}
    return spec["inventory_key"]


# ============================================================
# Purchasing
# ============================================================


async def purchase_premium_item(guild_id, user_id, key: str) -> dict:
    """Buy one unit of a premium item. Stock is reserved atomically first, then the wallet charge and
    inventory grant happen in a single atomic update; the stock is put back if the charge fails."""
    spec = cfg.PREMIUM_SHOP_ITEMS.get(key)
    if spec is None:
        return {"ok": False, "message": "❌ That item isn't sold here."}
    guild_key = str(guild_id)
    doc = await state.premium_shop_col.find_one({"_id": guild_key})
    if not is_open_now(doc, datetime.now(timezone.utc)):
        return {"ok": False, "message": "⌛ The premium shop has closed."}
    price = int(spec["price"])
    user_data = await econ.get_user(None, guild_key, user_id)
    if int(user_data.get("wallet", 0) or 0) < price:
        return {"ok": False, "message": f"❌ You don't have enough coins. **{spec['name']}** costs {price:,} coins."}
    reserved = await state.premium_shop_col.update_one(
        {"_id": guild_key, "is_open": True, "items": {"$elemMatch": {"key": key, "stock": {"$gt": 0}}}},
        {"$inc": {"items.$.stock": -1}},
    )
    if not reserved.modified_count:
        return {"ok": False, "message": f"😢 **{spec['name']}** just sold out!"}
    paid = await state.economy_col.update_one(
        {"_id": f"{guild_key}-{user_id}", "wallet": {"$gte": price}},
        {"$inc": {"wallet": -price}, "$push": {"inventory": build_inventory_entry(key)}},
    )
    if not paid.modified_count:
        await state.premium_shop_col.update_one({"_id": guild_key, "items.key": key}, {"$inc": {"items.$.stock": 1}})
        return {"ok": False, "message": f"❌ You don't have enough coins. **{spec['name']}** costs {price:,} coins."}
    return {"ok": True, "item_name": spec["name"], "price": price, "new_wallet": int(user_data["wallet"]) - price}


# ============================================================
# Views
# ============================================================


async def refresh_message(guild: discord.Guild, doc: dict, *, closed: bool = False) -> None:
    """Render the announcement message from the current stock again. Missing message or channel is ignored."""
    channel = guild.get_channel(doc.get("channel_id") or 0)
    message_id = doc.get("message_id")
    closes_at = parse_time(doc.get("closes_at"))
    if channel is None or not message_id or closes_at is None:
        return
    embed = build_embed(doc.get("items", []), closes_at, closed=closed)
    try:
        await channel.get_partial_message(message_id).edit(embed=embed, view=None if closed else PremiumShopView())
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


class PremiumShopView(discord.ui.View):
    """Persistent view on the announcement message. It holds no per shop state (the live shop is
    looked up on click), so one instance registered at startup serves every announcement."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 Shop the Deals", style=discord.ButtonStyle.green, custom_id=BUY_BUTTON_ID)
    async def open_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        doc = await state.premium_shop_col.find_one({"_id": str(interaction.guild.id)})
        if not is_open_now(doc, datetime.now(timezone.utc)) or doc.get("message_id") != interaction.message.id:
            await interaction.response.send_message("⌛ This premium shop has closed.", ephemeral=True)
            return
        options = []
        for item in doc.get("items", []):
            spec = cfg.PREMIUM_SHOP_ITEMS.get(item["key"])
            if spec is None or int(item.get("stock", 0)) <= 0:
                continue
            options.append(
                discord.SelectOption(
                    label=f"{spec['name']} - 🪙 {spec['price']:,} ({item['stock']} left)"[:100],
                    description=spec["description"].replace("**", "")[:100],
                    value=item["key"],
                    emoji=spec["emoji"],
                )
            )
        if not options:
            await interaction.response.send_message("😢 Everything is sold out!", ephemeral=True)
            return
        user_data = await econ.get_user(None, interaction.guild.id, interaction.user.id)
        embed = discord.Embed(
            title="💎 Select a Premium Item",
            description=f"Your wallet: 🪙 {int(user_data.get('wallet', 0)):,}\n\nChoose an item below:",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(
            embed=embed, view=PremiumShopDropdown(interaction.user.id, options), ephemeral=True
        )


class PremiumShopDropdown(discord.ui.View):
    def __init__(self, user_id: int, options: list[discord.SelectOption]):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.dropdown = discord.ui.Select(placeholder="Choose an item to buy...", options=options[:25])
        self.dropdown.callback = self.dropdown_callback
        self.add_item(self.dropdown)

    async def dropdown_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can't use this dropdown!", ephemeral=True)
            return
        try:
            result = await purchase_premium_item(interaction.guild.id, interaction.user.id, self.dropdown.values[0])
            if not result["ok"]:
                await interaction.response.send_message(result["message"], ephemeral=True)
                return
            guild_id = str(interaction.guild.id)
            await xp.increment_badge_counter(guild_id, str(interaction.user.id), "shop_purchases")
            fresh_data = await econ.get_user(None, guild_id, interaction.user.id)
            await xp.check_and_award_badges(
                getattr(interaction, "channel", None), interaction.guild, interaction.user, fresh_data
            )
            embed = discord.Embed(
                title="✅ Purchase Successful!",
                description=(
                    f"You bought **{result['item_name']}**!\n\nPrice: 🪙 {result['price']:,}\n"
                    f"New Wallet: 🪙 {result['new_wallet']:,}\n\nUse `.inventory` to view your items!"
                ),
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.dropdown.disabled = True
            self.dropdown.placeholder = "Purchase completed!"
            try:
                await interaction.followup.edit_message(interaction.message.id, view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
            doc = await state.premium_shop_col.find_one({"_id": guild_id})
            if doc:
                await refresh_message(interaction.guild, doc)
        except Exception as e:
            print(f"[ERROR] PremiumShopDropdown purchase: {type(e).__name__}: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An unexpected error occurred during purchase. Please contact " + cfg.BOT_ADMIN_NAME + ".",
                ephemeral=True,
            )


# ============================================================
# Cog
# ============================================================


class PremiumShop(commands.Cog):
    """Random limited stock premium shop announcements in each guild's economy channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    async def _schedule_next(self, guild_id: str, now: datetime) -> None:
        await state.premium_shop_col.update_one(
            {"_id": guild_id},
            {"$set": {"guild": guild_id, "is_open": False, "next_open_at": (now + roll_gap()).isoformat()}},
            upsert=True,
        )

    async def _open_shop(self, guild: discord.Guild, channel: discord.abc.Messageable, now: datetime) -> None:
        guild_id = str(guild.id)
        items = roll_stock()
        closes_at = now + roll_open_duration()
        try:
            message = await channel.send(embed=build_embed(items, closes_at), view=PremiumShopView())
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[PremiumShop] Couldn't post in guild {guild_id}: {e}")
            await self._schedule_next(guild_id, now)
            return
        await state.premium_shop_col.update_one(
            {"_id": guild_id},
            {
                "$set": {
                    "guild": guild_id,
                    "is_open": True,
                    "closes_at": closes_at.isoformat(),
                    "channel_id": channel.id,
                    "message_id": message.id,
                    "items": items,
                }
            },
            upsert=True,
        )

    async def _close_shop(self, guild: discord.Guild, doc: dict, now: datetime) -> None:
        await self._schedule_next(str(guild.id), now)
        await refresh_message(guild, doc, closed=True)

    async def _tick_guild(self, guild: discord.Guild, now: datetime) -> None:
        guild_id = str(guild.id)
        doc = await state.premium_shop_col.find_one({"_id": guild_id})
        if doc and doc.get("is_open"):
            if not is_open_now(doc, now):
                await self._close_shop(guild, doc, now)
            return
        config = await state.config_col.find_one({"guild": guild_id}) or {}
        channel_id = economy_channel_id(config) if isinstance(config, dict) else None
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None or not hasattr(channel, "send"):
            return
        next_open = parse_time(doc.get("next_open_at")) if doc else None
        if next_open is None:
            await self._schedule_next(guild_id, now)
        elif now >= next_open:
            await self._open_shop(guild, channel, now)

    @tasks.loop(minutes=1)
    async def premium_shop_loop(self):
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            try:
                await self._tick_guild(guild, now)
            except Exception as e:
                print(f"[PremiumShop] tick failed for guild {guild.id}: {type(e).__name__}: {e}")
                traceback.print_exc()

    @premium_shop_loop.before_loop
    async def before_premium_shop_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        """Register the persistent buy button and start the scheduler once (on_ready can refire)."""
        if self._ready_done:
            return
        self._ready_done = True
        self.bot.add_view(PremiumShopView())
        if not self.premium_shop_loop.is_running():
            self.premium_shop_loop.start()

    def cog_unload(self):
        if self.premium_shop_loop.is_running():
            self.premium_shop_loop.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PremiumShop(bot))
