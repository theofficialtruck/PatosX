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

"""Shop browsing/purchasing/refunds, per-guild shop item management, inventory, and shop seeding."""

import asyncio
import traceback

import discord
from discord import (
    app_commands,
)
from discord.ext import commands

from core import config as cfg
from core import economyHelperFuncs as econ
from core import permsHelperFuncs as perms
from core import state
from core import xpHelperFuncs as xp


async def process_shop_purchase(member, guild, store_item: dict, user_data: dict):
    item_name = store_item.get("name") or store_item.get("name_lower") or "Unknown Item"
    try:
        price = int(store_item.get("price", 0))
    except (TypeError, ValueError):
        return {"ok": False, "message": "❌ Invalid item price! Ask staff to fix this shop item."}
    if price <= 0:
        return {"ok": False, "message": "❌ Invalid item price! Ask staff to fix this shop item."}
    wallet = int(user_data.get("wallet", 0) or 0)
    inventory = list(user_data.get("inventory", []))
    user_key = f"{guild.id}-{member.id}"
    role_id = store_item.get("role_id")
    if role_id is not None:
        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            role = None
        if not role:
            return {
                "ok": False,
                "message": "❌ This item's linked role is invalid or was deleted. Ask staff to update it.",
            }
        if role in getattr(member, "roles", []):
            return {"ok": False, "message": f"✅ You already have the role for **{item_name}**."}
        if wallet < price:
            return {"ok": False, "message": f"❌ You don't have enough coins. **{item_name}** costs {price} coins."}
        new_wallet = wallet - price
        await state.economy_col.update_one({"_id": user_key}, {"$set": {"wallet": new_wallet}})
        try:
            await member.add_roles(role, reason=f"Purchased shop role item: {item_name}")
        except (discord.Forbidden, discord.HTTPException) as role_error:
            await state.economy_col.update_one({"_id": user_key}, {"$set": {"wallet": wallet}})
            return {"ok": False, "message": f"❌ Couldn't assign the role (`{role_error}`). You were refunded."}
        return {
            "ok": True,
            "message": f"✅ You bought **{item_name}** for {price} coins!",
            "item_name": item_name,
            "price": price,
            "old_wallet": wallet,
            "new_wallet": new_wallet,
            "purchase_type": "role",
            "role_mention": role.mention,
        }
    if wallet < price:
        return {"ok": False, "message": f"❌ You don't have enough coins. **{item_name}** costs {price} coins."}
    new_wallet = wallet - price
    item_id = str(store_item.get("_id", ""))
    item_key = str(store_item.get("name_lower") or item_name).strip().lower()
    is_pet_duck = store_item.get("name_lower") == "pet_duck" or item_id == "pet_duck" or item_id.endswith("-pet_duck")
    is_nitro_boost = (
        store_item.get("name_lower") == "nitro_boost" or item_id == "nitro_boost" or item_id.endswith("-nitro_boost")
    )
    if is_pet_duck:
        inventory.append({"_id": "pet_duck", "uses_left": 3})
        success_message = "🦆 You bought a Pet Duck! It has 3 uses. You can stack multiple ducks."
        purchase_type = "pet_duck"
    elif is_nitro_boost:
        inventory.append({"_id": "nitro_boost", "uses_left": 3})
        success_message = "🚀 You bought a Nitro Boost! It has 3 uses. You can stack multiple boosts."
        purchase_type = "nitro_boost"
    elif item_key in cfg.TOOL_DURABILITIES:
        max_uses = cfg.TOOL_DURABILITIES[item_key]
        inventory.append({"_id": item_key, "uses_left": max_uses})
        success_message = f"✅ You bought **{item_name}** for {price} coins! Durability: **{max_uses}/{max_uses}**"
        purchase_type = "inventory"
    else:
        inventory.append(item_key)
        success_message = f"✅ You bought **{item_name}** for {price} coins!"
        purchase_type = "inventory"
    await state.economy_col.update_one({"_id": user_key}, {"$set": {"wallet": new_wallet, "inventory": inventory}})
    return {
        "ok": True,
        "message": success_message,
        "item_name": item_name,
        "price": price,
        "old_wallet": wallet,
        "new_wallet": new_wallet,
        "purchase_type": purchase_type,
    }


async def process_shop_refund(member, guild, item_key: str, user_data: dict):
    normalized_key = str(item_key or "").strip().lower()
    if not normalized_key:
        return {"ok": False, "message": "❌ Invalid refund item."}
    wallet = int(user_data.get("wallet", 0) or 0)
    inventory = list(user_data.get("inventory", []))
    user_key = f"{guild.id}-{member.id}"
    remove_index = None
    removed_item = None
    for idx, item in enumerate(inventory):
        if econ.normalize_item_key(item) == normalized_key:
            remove_index = idx
            removed_item = item
            break
    if remove_index is None:
        return {"ok": False, "message": "❌ That item is not in your inventory anymore."}
    normalized_candidates = {normalized_key, normalized_key.replace("_", " "), normalized_key.replace(" ", "_")}
    prefixed_ids = [f"{guild.id}-{candidate}" for candidate in normalized_candidates]
    store_item = await state.guild_shop_col.find_one(
        {
            "guild": str(guild.id),
            "$or": [{"name_lower": {"$in": list(normalized_candidates)}}, {"_id": {"$in": prefixed_ids}}],
        }
    )
    if not store_item:
        store_item = await state.shop_col.find_one(
            {
                "$or": [
                    {"name_lower": {"$in": list(normalized_candidates)}},
                    {"_id": {"$in": list(normalized_candidates)}},
                ]
            }
        )
    if not store_item:
        return {"ok": False, "message": "❌ This item can't be refunded because it no longer exists in the shop."}
    try:
        price = int(store_item.get("price", 0))
    except (TypeError, ValueError):
        return {"ok": False, "message": "❌ This item has an invalid shop price and cannot be refunded."}
    if price <= 0:
        return {"ok": False, "message": "❌ This item has an invalid shop price and cannot be refunded."}
    refund_amount = price // 2
    inventory.pop(remove_index)
    new_wallet = wallet + refund_amount
    await state.economy_col.update_one({"_id": user_key}, {"$set": {"wallet": new_wallet, "inventory": inventory}})
    item_name = store_item.get("name") or normalized_key.replace("_", " ").title()
    return {
        "ok": True,
        "item_name": item_name,
        "refund_amount": refund_amount,
        "old_wallet": wallet,
        "new_wallet": new_wallet,
        "removed_item": removed_item,
    }


class ShopView(discord.ui.View):
    def __init__(self, user_id, guild_id, items, user_balance):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.guild_id = guild_id
        self.items = items
        self.balance = user_balance

    @discord.ui.button(label="🛒 Buy Items", style=discord.ButtonStyle.green, custom_id="buy_items_button")
    async def buy_items(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can't use this button!", ephemeral=True)
            return
        if not self.items:
            await interaction.response.send_message("❌ The shop is empty!", ephemeral=True)
            return
        options = []
        for item in self.items:
            display_name = item.get("name") or item.get("_id", "Unnamed Item")
            price = item.get("price", "Unknown")
            description = item.get("description", "No description available.")
            can_afford = "✅" if isinstance(price, (int, float)) and self.balance >= price else "❌"
            options.append(
                discord.SelectOption(
                    label=f"{display_name} - 🪙 {price}",
                    description=f"{description[:50]}..." if len(description) > 50 else description,
                    value=item["_id"],
                    emoji=can_afford,
                )
            )
        view = ShopDropdown(self.user_id, self.guild_id, self.items, self.balance, options)
        embed = discord.Embed(
            title="🛒 Select Item to Buy",
            description=f"Your wallet: 🪙 {self.balance:,}\n\nChoose an item from the dropdown below:",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="💸 Refund", style=discord.ButtonStyle.blurple, custom_id="refund_items_button")
    async def refund_items(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can't use this button!", ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        user_data = await econ.get_user(None, guild_id, interaction.user.id)
        inventory = list(user_data.get("inventory", []))
        if not inventory:
            await interaction.response.send_message(
                "❌ Your inventory is empty, so there's nothing to refund.", ephemeral=True
            )
            return
        counts = {}
        for item in inventory:
            item_key = econ.normalize_item_key(item)
            if not item_key:
                continue
            counts[item_key] = counts.get(item_key, 0) + 1
        if not counts:
            await interaction.response.send_message(
                "❌ No refundable items were found in your inventory.", ephemeral=True
            )
            return
        options = []
        for item_key, count in counts.items():
            normalized_candidates = {item_key, item_key.replace("_", " "), item_key.replace(" ", "_")}
            prefixed_ids = [f"{guild_id}-{candidate}" for candidate in normalized_candidates]
            store_item = await state.guild_shop_col.find_one(
                {
                    "guild": guild_id,
                    "$or": [{"name_lower": {"$in": list(normalized_candidates)}}, {"_id": {"$in": prefixed_ids}}],
                }
            )
            if not store_item:
                store_item = await state.shop_col.find_one(
                    {
                        "$or": [
                            {"name_lower": {"$in": list(normalized_candidates)}},
                            {"_id": {"$in": list(normalized_candidates)}},
                        ]
                    }
                )
            if not store_item:
                continue
            try:
                item_price = int(store_item.get("price", 0))
            except (TypeError, ValueError):
                continue
            if item_price <= 0:
                continue
            refund_amount = item_price // 2
            item_name = store_item.get("name") or item_key.replace("_", " ").title()
            label = f"{item_name} x{count}"
            description = f"Refund value: +🪙 {refund_amount:,} each"
            options.append(
                discord.SelectOption(label=label[:100], description=description[:100], value=item_key, emoji="💸")
            )
        if not options:
            await interaction.response.send_message(
                "❌ No refundable items were found in your inventory.", ephemeral=True
            )
            return
        view = RefundDropdown(self.user_id, guild_id, options)
        embed = discord.Embed(
            title="💸 Refund an Item",
            description="Choose one inventory item to refund for half its shop price.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ShopDropdown(discord.ui.View):
    def __init__(self, user_id, guild_id, items, user_balance, options):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.guild_id = guild_id
        self.items = items
        self.balance = user_balance
        self.dropdown = discord.ui.Select(placeholder="Choose an item to buy...", options=options[:25])
        self.dropdown.callback = self.dropdown_callback
        self.add_item(self.dropdown)

    async def dropdown_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can't use this dropdown!", ephemeral=True)
            return
        selected_item_id = self.dropdown.values[0]
        selected_item = next((item for item in self.items if item["_id"] == selected_item_id), None)
        if not selected_item:
            await interaction.response.send_message("❌ Item not found!", ephemeral=True)
            return
        try:
            guild_id = str(interaction.guild.id)
            user_data = await econ.get_user(None, guild_id, interaction.user.id)
            result = await process_shop_purchase(interaction.user, interaction.guild, selected_item, user_data)
            if not result["ok"]:
                await interaction.response.send_message(result["message"], ephemeral=True)
                return
            self.balance = result["new_wallet"]
            await xp.increment_badge_counter(guild_id, str(interaction.user.id), "shop_purchases")
            fresh_data = await econ.get_user(None, guild_id, interaction.user.id)
            await xp.check_and_award_badges(
                getattr(interaction, "channel", None), interaction.guild, interaction.user, fresh_data
            )
            embed = discord.Embed(
                title="✅ Purchase Successful!",
                description=f"You bought **{result['item_name']}**!\n\nPrice: 🪙 {result['price']:,}\nOld Wallet: 🪙 {result['old_wallet']:,}\nNew Wallet: 🪙 {result['new_wallet']:,}"
                + (
                    f"\nRole Granted: {result['role_mention']}"
                    if result["purchase_type"] == "role"
                    else "\n\nUse `.inventory` to view your items!"
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
        except Exception as e:
            print(f"[ERROR] ShopDropdown purchase: {type(e).__name__}: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An unexpected error occurred during purchase. Please contact " + cfg.BOT_ADMIN_NAME + ".",
                ephemeral=True,
            )


class RefundDropdown(discord.ui.View):
    def __init__(self, user_id, guild_id, options):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.guild_id = guild_id
        self.dropdown = discord.ui.Select(placeholder="Choose an item to refund...", options=options[:25])
        self.dropdown.callback = self.dropdown_callback
        self.add_item(self.dropdown)

    async def dropdown_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can't use this dropdown!", ephemeral=True)
            return
        selected_item_key = self.dropdown.values[0]
        user_data = await econ.get_user(None, self.guild_id, interaction.user.id)
        result = await process_shop_refund(interaction.user, interaction.guild, selected_item_key, user_data)
        if not result.get("ok"):
            await interaction.response.send_message(result["message"], ephemeral=True)
            return
        embed = discord.Embed(
            title="✅ Refund Complete",
            description=f"Refunded **{result['item_name']}**.\n\nReturned: 🪙 {result['refund_amount']:,}\nOld Wallet: 🪙 {result['old_wallet']:,}\nNew Wallet: 🪙 {result['new_wallet']:,}",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.dropdown.disabled = True
        self.dropdown.placeholder = "Refund completed!"
        try:
            await interaction.followup.edit_message(interaction.message.id, view=self)
        except (discord.NotFound, discord.HTTPException):
            pass


async def ensure_shop_items():
    """Seed the global shop with default items if they do not already exist.
    Also purges any retired items before inserting to keep the shop clean."""
    await econ.purge_removed_shop_items()
    initial_items = [
        {
            "_id": "fishing rod",
            "name": "Fishing Rod",
            "name_lower": "fishing rod",
            "price": 150,
            "description": f"🎣 Needed to catch fish to earn coins. Breaks after {cfg.TOOL_DURABILITIES['fishing rod']} uses.",
        },
        {
            "_id": "scuba gear",
            "name": "Scuba Gear",
            "name_lower": "scuba gear",
            "price": 750,
            "description": f"🤿 Needed to swim for exotic deep ocean fish. Breaks after {cfg.TOOL_DURABILITIES['scuba gear']} uses.",
        },
        {
            "_id": "laptop",
            "name": "Laptop",
            "name_lower": "laptop",
            "price": 500,
            "description": f"💻 Needed to work the developer job. Breaks after {cfg.TOOL_DURABILITIES['laptop']} uses.",
        },
        {
            "_id": "pickaxe",
            "name": "Pickaxe",
            "name_lower": "pickaxe",
            "price": 500,
            "description": f"⛏️ Needed to go mining. Breaks after {cfg.TOOL_DURABILITIES['pickaxe']} uses.",
        },
        {
            "_id": "shovel",
            "name": "Shovel",
            "name_lower": "shovel",
            "price": 350,
            "description": f"🪏 Needed to dig for cool rocks. Breaks after {cfg.TOOL_DURABILITIES['shovel']} uses.",
        },
        {
            "_id": "rifle",
            "name": "Rifle",
            "name_lower": "rifle",
            "price": 500,
            "description": f"🔫 Needed to go hunting. Breaks after {cfg.TOOL_DURABILITIES['rifle']} uses.",
        },
        {
            "_id": "lockpick",
            "name": "Lockpick",
            "name_lower": "lockpick",
            "price": 250,
            "description": f"🗝️ Needed for bank crimes. Breaks after {cfg.TOOL_DURABILITIES['lockpick']} uses.",
        },
        {
            "_id": "butterfly net",
            "name": "Butterfly Net",
            "name_lower": "butterfly net",
            "price": 450,
            "description": f"🦋 Needed to catch bugs with bugcatch. Breaks after {cfg.TOOL_DURABILITIES['butterfly net']} uses.",
        },
        {
            "_id": "pet_duck",
            "name": "Pet Duck",
            "name_lower": "pet duck",
            "price": 1000,
            "description": "🦆 Cool pet duck! Gives 30% luck for 3 uses on certain activities.",
            "uses_left": 3,
        },
        {
            "_id": "nitro_boost",
            "name": "Nitro Boost",
            "name_lower": "nitro boost",
            "price": 1000,
            "description": (
                "🚀 Cuts the cooldown of beg, lottery, work, fish, swim, crime, and bugcatch by "
                f"{int(cfg.NITRO_BOOST_COOLDOWN_REDUCTION_PCT * 100)}% for 3 uses."
            ),
            "uses_left": 3,
        },
        {
            "_id": "energy_drink",
            "name": "Energy Drink",
            "name_lower": "energy drink",
            "price": 200,
            "description": "⚡ Reduces work cooldown by 50% for your next work session. One time use.",
            "uses_left": 1,
        },
        {
            "_id": "lucky_cookie",
            "name": "Lucky Cookie",
            "name_lower": "lucky cookie",
            "price": 150,
            "description": "🍪 Doubles your next work/beg earnings. One time use.",
            "uses_left": 1,
        },
        {
            "_id": "coffee_cup",
            "name": "Coffee Cup",
            "name_lower": "coffee cup",
            "price": 100,
            "description": "☕ Gives 25% bonus on your next crime success chance. One time use.",
            "uses_left": 1,
        },
        {
            "_id": "pond_royalty",
            "name": "Pond Royalty",
            "name_lower": "pond royalty",
            "price": 30000,
            "description": "👑 Claim the title of Pond Royalty! Grants you the exclusive Pond Royalty role.",
        },
    ]
    for item in initial_items:
        if econ.is_removed_shop_item(item.get("name_lower") or item.get("_id")):
            continue
        await state.shop_col.update_one({"_id": item["_id"]}, {"$set": item}, upsert=True)
    print("✅ Shop synced with initial items.")


async def prompt_for_role(ctx):
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    while True:
        await ctx.send("📌 Please enter the **role ID** (or type `cancel` to skip):")
        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            await ctx.send("⌛ Cancelled due to timeout.")
            return None
        if msg.content.lower() == "cancel":
            return None
        try:
            role_id = int(msg.content)
        except ValueError:
            await ctx.send("❌ Invalid format. Role ID must be numbers only.")
            continue
        role = ctx.guild.get_role(role_id)
        if not role:
            await ctx.send("❌ No role found with that ID. Please try again.")
            continue
        await ctx.send(f"✅ Linked role: {role.mention}")
        return role_id


class Shop(commands.Cog):
    """Shop browsing/purchasing/refunds, per-guild shop item management, inventory, and shop seeding."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    @commands.hybrid_command(name="shop", description="View the shop.", aliases=["store"])
    @perms.blacklist_barrier()
    @xp.xp_earn(3, 7)
    async def shop(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            guild_id = str(ctx.guild.id)
            user_id = str(ctx.author.id)
            await econ.purge_removed_shop_items(guild_id)
            user_data = await state.economy_col.find_one({"_id": f"{guild_id}-{user_id}"})
            wallet_balance = user_data.get("wallet", 0) if user_data else 0
            bank_balance = user_data.get("bank", 0) if user_data else 0
            total_balance = wallet_balance + bank_balance
            shop_items = state.guild_shop_col.find({"guild": guild_id}).sort("price", 1)
            exists = False
            items_list = []
            async for item in shop_items:
                if econ.is_removed_shop_item(item.get("name_lower") or item.get("_id")):
                    continue
                exists = True
                items_list.append(item)
            existing_name_lowers = {
                str(item.get("name_lower") or "").strip().lower() for item in items_list if item.get("name_lower")
            }
            if not exists:
                defaults = state.shop_col.find()
                async for item in defaults:
                    if econ.is_removed_shop_item(item.get("name_lower") or item.get("_id")):
                        continue
                    doc = dict(item)
                    doc["_id"] = f"{guild_id}-{item['_id']}"
                    doc["guild"] = guild_id
                    await state.guild_shop_col.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
                shop_items = state.guild_shop_col.find({"guild": guild_id}).sort("price", 1)
                async for item in shop_items:
                    items_list.append(item)
                    if item.get("name_lower"):
                        existing_name_lowers.add(str(item.get("name_lower")).strip().lower())
            defaults = state.shop_col.find()
            added_default_item = False
            async for item in defaults:
                if econ.is_removed_shop_item(item.get("name_lower") or item.get("_id")):
                    continue
                default_name_lower = str(item.get("name_lower") or item.get("_id") or "").strip().lower()
                if not default_name_lower or default_name_lower in existing_name_lowers:
                    continue
                doc = dict(item)
                doc["_id"] = f"{guild_id}-{item['_id']}"
                doc["guild"] = guild_id
                await state.guild_shop_col.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
                items_list.append(doc)
                existing_name_lowers.add(default_name_lower)
                added_default_item = True
            if added_default_item:
                items_list.sort(key=lambda x: x.get("price", 0))
            guild_config = await state.config_col.find_one({"guild": guild_id}) or {}
            pond_royalty_role_id = guild_config.get("pond_royalty_role")
            filtered_items = []
            for item in items_list:
                if str(item.get("name_lower") or "").strip().lower() == "pond royalty":
                    if not pond_royalty_role_id:
                        continue
                    item = dict(item)
                    item["role_id"] = pond_royalty_role_id
                filtered_items.append(item)
            items_list = filtered_items
            embed = discord.Embed(
                title="🛍️ Shop",
                description=f"💰 Wallet: 🪙 {wallet_balance:,}\n🏦 Bank: 🪙 {bank_balance:,}\n💳 **Total: 🪙 {total_balance:,}**\n\nClick the button below to purchase items! Purchases use wallet coins only.",
                color=discord.Color.green(),
            )
            if items_list:
                for item in items_list:
                    display_name = item.get("name") or item.get("_id", "Unnamed Item")
                    price = item.get("price", "Unknown")
                    description = item.get("description", "No description available.")
                    embed.add_field(name=f"{display_name} - 🪙 {price}", value=description, inline=False)
                    if len(embed.fields) >= 24:
                        embed.add_field(
                            name="🛍️ More Items",
                            value=f"... and {len(items_list) - len(embed.fields) + 1} more items! Use the shop view to see all items.",
                            inline=False,
                        )
                        break
            else:
                embed.description += "\n\n❌ The shop is empty. Ask a staff member to refill it."
            view = ShopView(ctx.author.id, guild_id, items_list, wallet_balance) if items_list else None
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            print(f"[ERROR] shop command: {type(e).__name__}: {e}")
            traceback.print_exc()
            await ctx.send(
                "❌ An unexpected error occurred while loading the shop. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(name="additem", description="Add a new item to the shop.")
    @app_commands.describe(name="Item name", price="Item price in coins")
    @perms.staffperm("economy")
    @perms.staff_only()
    async def additem(self, ctx, name: str, price: int):
        name = name.strip()
        if not name:
            return await ctx.send('❌ Usage: `.additem "item name" <price>` or `/additem <name> <price>`')
        if econ.is_removed_shop_item(name):
            return await ctx.send("❌ This item is blocked and cannot be added to the shop.")
        if price <= 0:
            return await ctx.send("❌ Price must be greater than 0.")
        name_lower = name.lower()
        await ctx.send(f"📝 Enter the description for **{name}**:")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            desc_msg = await self.bot.wait_for("message", check=check, timeout=120)
            description = desc_msg.content.strip()
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Item creation cancelled due to timeout.")
        await ctx.send(f"🔗 Do you want to link a role to **{name}**? (yes/no)")
        try:
            choice_msg = await self.bot.wait_for("message", check=check, timeout=60)
            choice = choice_msg.content.lower()
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Item creation cancelled due to timeout.")
        role_id = None
        if choice in ["yes", "y"]:
            role_id = await prompt_for_role(ctx)
        item_data = {
            "_id": name_lower,
            "name": name,
            "name_lower": name_lower,
            "price": price,
            "description": description,
        }
        if role_id:
            item_data["role_id"] = role_id
        guild_id = str(ctx.guild.id)
        item_data["_id"] = f"{guild_id}-{name_lower}"
        item_data["guild"] = guild_id
        await state.guild_shop_col.replace_one({"_id": item_data["_id"]}, item_data, upsert=True)
        confirmation_msg = f"✅ Added **{name}** to the shop!\n**Price:** {price}\n**Description:** {description}"
        if role_id:
            confirmation_msg += f"\n**Linked Role:** <@&{role_id}>"
        await ctx.send(confirmation_msg)

    @commands.hybrid_command(name="edititem", description="Edit an existing shop item.")
    @perms.staffperm("economy")
    @perms.staff_only()
    async def edititem(self, ctx, *, name: str):
        if econ.is_removed_shop_item(name):
            return await ctx.send("❌ This item is blocked and cannot be used.")
        guild_id = str(ctx.guild.id)
        item = await state.guild_shop_col.find_one({"guild": guild_id, "name_lower": name.lower()})
        if not item:
            return await ctx.send(f"❌ No item found with name `{name}`.")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send(f"✏️ Enter a new name for **{item['name']}** (or type `skip` to keep the same):")
        try:
            name_msg = await self.bot.wait_for("message", check=check, timeout=60)
            new_name = name_msg.content.strip()
            if new_name.lower() == "skip":
                new_name = item["name"]
            elif econ.is_removed_shop_item(new_name):
                return await ctx.send("❌ This item name is blocked and cannot be used.")
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Edit cancelled due to timeout.")
        await ctx.send(f"💰 Enter a new price for **{new_name}** (or type `skip`):")
        try:
            price_msg = await self.bot.wait_for("message", check=check, timeout=60)
            if price_msg.content.lower() == "skip":
                new_price = item["price"]
            else:
                new_price = int(price_msg.content)
        except (asyncio.TimeoutError, ValueError):
            return await ctx.send("❌ Invalid price or timeout. Edit cancelled.")
        await ctx.send(f"📝 Enter a new description for **{new_name}** (or type `skip`):")
        try:
            desc_msg = await self.bot.wait_for("message", check=check, timeout=120)
            if desc_msg.content.lower() == "skip":
                new_desc = item["description"]
            else:
                new_desc = desc_msg.content.strip()
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Edit cancelled due to timeout.")
        await ctx.send("🔗 Do you want to change the linked role? (yes/no)")
        try:
            choice_msg = await self.bot.wait_for("message", check=check, timeout=60)
            choice = choice_msg.content.lower()
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Edit cancelled due to timeout.")
        role_id = item.get("role_id")
        if choice in ["yes", "y"]:
            role_id = await prompt_for_role(ctx)
        await state.guild_shop_col.update_one(
            {"guild": guild_id, "name_lower": name.lower()},
            {
                "$set": {
                    "name": new_name,
                    "name_lower": new_name.lower(),
                    "price": new_price,
                    "description": new_desc,
                    "role_id": role_id,
                }
            },
        )
        confirmation_msg = f"✅ Updated **{new_name}**!\n**Price:** {new_price}\n**Description:** {new_desc}"
        if role_id:
            confirmation_msg += f"\n**Linked Role:** <@&{role_id}>"
        await ctx.send(confirmation_msg)

    @commands.hybrid_command(name="delitem", description="Remove an item from the shop.")
    @perms.staffperm("economy")
    @perms.staff_only()
    async def delitem(self, ctx, *, name: str):
        guild_id = str(ctx.guild.id)
        result = await state.guild_shop_col.delete_one({"guild": guild_id, "name_lower": name.lower()})
        if result.deleted_count:
            await ctx.send(f"🗑️ `{name}` removed from the shop.")
        else:
            await ctx.send("❌ Item not found.")

    @commands.hybrid_command(name="buy", description="Buy an item from the shop.", aliases=["purchase"])
    @app_commands.describe(
        item="The item to buy (e.g., 'fishing rod', 'rifle', 'laptop'). Use '/shop' to see available items."
    )
    @perms.blacklist_barrier()
    @xp.xp_earn(8, 16)
    async def buy(self, ctx, *, item: str | None = None):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        if not item:
            return await ctx.send("❌ You must specify an item to buy.")
        parts = item.lower().strip().split()
        amount = 1
        if parts and parts[-1].isdigit():
            try:
                amount = int(parts[-1])
            except ValueError:
                amount = 1
            item_name = " ".join(parts[:-1]).strip()
        else:
            item_name = " ".join(parts).strip()
        if not item_name:
            return await ctx.send("❌ You must specify an item to buy.")
        if econ.is_removed_shop_item(item_name):
            return await ctx.send("❌ This item is no longer available in the shop.")
        store_item = await state.guild_shop_col.find_one({"guild": str(ctx.guild.id), "name_lower": item_name})
        if not store_item:
            default_item = await state.shop_col.find_one({"name_lower": item_name})
            if default_item:
                guild_id = str(ctx.guild.id)
                store_item = dict(default_item)
                store_item["_id"] = f"{guild_id}-{default_item['_id']}"
                store_item["guild"] = guild_id
                await state.guild_shop_col.update_one({"_id": store_item["_id"]}, {"$set": store_item}, upsert=True)
            else:
                return await ctx.send(f"❌ Item **{item_name}** not found in the shop.")
        if amount <= 1:
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            result = await process_shop_purchase(ctx.author, ctx.guild, store_item, data)
            await ctx.send(result["message"])
            if result.get("ok"):
                await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "shop_purchases")
                fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
                await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
            return
        bought = 0
        messages = []
        price = 0
        try:
            price = int(store_item.get("price", 0))
        except Exception:
            price = 0
        for i in range(amount):
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            result = await process_shop_purchase(ctx.author, ctx.guild, store_item, data)
            messages.append(result.get("message", "Unknown response"))
            if not result.get("ok"):
                break
            bought += 1
            if result.get("purchase_type") == "role":
                break
        if bought == 0:
            await ctx.send(messages[0] if messages else "❌ Purchase failed.")
            return
        if bought < amount:
            total_cost = price * bought if price else None
            summary = f"✅ Successfully bought **{store_item.get('name', item_name)}** x{bought}."
            if total_cost is not None:
                summary += f" Total cost: {total_cost} coins."
            summary += f"\n\n{messages[-1]}"
            await ctx.send(summary)
        else:
            total_cost = price * bought if price else None
            summary = f"✅ Successfully bought **{store_item.get('name', item_name)}** x{bought}."
            if total_cost is not None:
                summary += f" Total cost: {total_cost} coins. ({price} each)"
            await ctx.send(summary)
        if bought > 0:
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "shop_purchases", amount=bought)
            fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)

    @commands.hybrid_command(name="use", description="Use an item from your inventory.")
    @app_commands.describe(
        item_name="The item to use (e.g., 'fishing rod', 'energy drink', 'laptop'). Use '/inventory' to see your items."
    )
    @perms.blacklist_barrier()
    @xp.xp_earn(7, 14)
    async def use(self, ctx, item_name: str):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
        inventory = data.get("inventory", [])
        item_name = item_name.strip().lower()
        matched_item = next((i for i in inventory if econ.normalize_item_key(i) == item_name), None)
        if not matched_item:
            return await ctx.send("❌ You don't have that item in your inventory.")
        if econ.normalize_item_key(matched_item) == "luck potion":
            await state.economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$pull": {"inventory": matched_item}, "$set": {"luck_buff": True}},
            )
            return await ctx.send(
                "🍀 You used a **Luck Potion**! You'll have better odds in your next activities for 1 use."
            )
        await ctx.send("❌ That item can't be used yet.")

    @commands.hybrid_command(name="inventory", description="View your items.", aliases=["inv"])
    @perms.blacklist_barrier()
    @xp.xp_earn(3, 7)
    async def inventory(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
        inv = data.get("inventory", [])
        if not inv:
            return await ctx.send("🎒 Your inventory is empty.")
        counts = {}
        tool_durability = {}
        duck_total = 0
        duck_uses = 0
        nitro_total = 0
        nitro_uses = 0
        for item in inv:
            item_key = econ.normalize_item_key(item)
            if item_key == "pet_duck":
                duck_total += 1
                duck_uses += item.get("uses_left", 0) if isinstance(item, dict) else 0
            elif item_key == "nitro_boost":
                nitro_total += 1
                nitro_uses += item.get("uses_left", 0) if isinstance(item, dict) else 0
            elif item_key in cfg.TOOL_DURABILITIES:
                uses_left = (
                    item.get("uses_left", cfg.TOOL_DURABILITIES[item_key])
                    if isinstance(item, dict)
                    else cfg.TOOL_DURABILITIES[item_key]
                )
                tool_durability.setdefault(item_key, []).append(uses_left)
            else:
                key = item_key if item_key else str(item)
                counts[key] = counts.get(key, 0) + 1
        embed = discord.Embed(title=f"🎒 {ctx.author.display_name}'s Inventory", color=discord.Color.purple())
        if duck_total > 0:
            shop_item = await state.shop_col.find_one({"_id": "pet_duck"}) or {"name": "Pet Duck", "description": ""}
            embed.add_field(
                name=f"{shop_item['name']} x{duck_total}",
                value=f"{shop_item.get('description', '')} ({duck_uses} uses left total)",
                inline=False,
            )
        if nitro_total > 0:
            shop_item = await state.shop_col.find_one({"_id": "nitro_boost"}) or {
                "name": "Nitro Boost",
                "description": "",
            }
            embed.add_field(
                name=f"{shop_item['name']} x{nitro_total}",
                value=f"{shop_item.get('description', '')} ({nitro_uses} uses left total)",
                inline=False,
            )
        for tool_key, durability_values in tool_durability.items():
            shop_item = await state.shop_col.find_one({"name_lower": tool_key})
            max_uses = cfg.TOOL_DURABILITIES[tool_key]
            display_name = shop_item["name"] if shop_item else tool_key.replace("_", " ").title()
            description = shop_item.get("description", "No description.") if shop_item else "No description."
            durability_text = ", ".join(f"{uses}/{max_uses}" for uses in durability_values[:5])
            if len(durability_values) > 5:
                durability_text += ", ..."
            embed.add_field(
                name=f"{display_name} x{len(durability_values)}",
                value=f"{description}\nDurability: {durability_text}",
                inline=False,
            )
            if len(embed.fields) >= 24:
                break
        if len(embed.fields) >= 24:
            return await ctx.send(embed=embed)
        for key, count in counts.items():
            shop_item = await state.shop_col.find_one({"name_lower": key})
            if shop_item:
                embed.add_field(
                    name=f"{shop_item['name']} x{count}",
                    value=f"{shop_item.get('description', 'No description.')}",
                    inline=False,
                )
            else:
                clean_name = key.split("-", 1)[-1] if "-" in key else key
                emoji = "📦"
                embed.add_field(
                    name=f"{emoji} {clean_name.replace('_', ' ').title()} x{count}",
                    value="*Item no longer sold in shop*",
                    inline=False,
                )
            if len(embed.fields) >= 24:
                embed.add_field(
                    name="📦 More Items",
                    value=f"... and {len(counts) - len(embed.fields) + 1} more items! Use `.inventory` again to see details.",
                    inline=False,
                )
                break
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        """Seed the global shop with the default items once the bot has connected (runs once)."""
        if self._ready_done:
            return
        self._ready_done = True
        await ensure_shop_items()
        print("✅ Shop synced with initial items.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))
