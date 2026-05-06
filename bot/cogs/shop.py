"""Shop browsing, inventory, and CRUD admin commands."""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import economy_col, guild_shop_col, shop_col
from bot.utils.channels import check_channel_setting as check_channel
from bot.utils.checks import blacklist_barrier, staff_only, staffperm, xp_earn
from bot.utils.economy import get_user
from bot.utils.role_prompt import prompt_for_role
from bot.views.shop import ShopView, process_shop_purchase


class ShopCog(commands.Cog, name="Shop"):
    """Shop browse + per-guild item CRUD."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="shop",
        description="View the shop.",
        aliases=["store"],
    )
    @blacklist_barrier()
    @xp_earn(3, 7)
    async def shop(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        try:
            guild_id = str(ctx.guild.id)
            user_id = str(ctx.author.id)

            user_data = await economy_col.find_one(
                {"_id": f"{guild_id}-{user_id}"}
            )
            wallet_balance = user_data.get("wallet", 0) if user_data else 0
            bank_balance = user_data.get("bank", 0) if user_data else 0
            total_balance = wallet_balance + bank_balance

            # Per-guild shop is empty — seed it from the global default catalogue.
            shop_items_cursor = guild_shop_col.find({"guild": guild_id}).sort("price", 1)
            items_list: list[dict] = [item async for item in shop_items_cursor]

            if not items_list:
                async for item in shop_col.find():
                    doc = dict(item)
                    doc["_id"] = f"{guild_id}-{item['_id']}"
                    doc["guild"] = guild_id
                    await guild_shop_col.update_one(
                        {"_id": doc["_id"]}, {"$set": doc}, upsert=True
                    )
                items_list = [
                    item
                    async for item in guild_shop_col.find(
                        {"guild": guild_id}
                    ).sort("price", 1)
                ]

            embed = discord.Embed(
                title="🛍️ Shop",
                description=(
                    f"💰 Wallet: 🪙 {wallet_balance:,}\n"
                    f"🏦 Bank: 🪙 {bank_balance:,}\n"
                    f"💳 **Total: 🪙 {total_balance:,}**\n\n"
                    "Click the button below to purchase items! "
                    "Purchases use wallet coins only."
                ),
                color=discord.Color.green(),
            )

            if items_list:
                for item in items_list:
                    display_name = item.get("name") or item.get("_id", "Unnamed Item")
                    price = item.get("price", "Unknown")
                    description = item.get("description", "No description available.")
                    if item["_id"] == "pet_duck":
                        description += "\n🦆 Stackable: Yes (3 uses per duck)"
                    embed.add_field(
                        name=f"{display_name} - 🪙 {price}",
                        value=description,
                        inline=False,
                    )
                    if len(embed.fields) >= 24:
                        embed.add_field(
                            name="🛍️ More Items",
                            value=(
                                f"... and {len(items_list) - len(embed.fields) + 1} "
                                "more items! Use the shop view to see all items."
                            ),
                            inline=False,
                        )
                        break
            else:
                embed.description += (
                    "\n\n❌ The shop is empty. Ask a staff member to refill it."
                )

            view = (
                ShopView(ctx.author.id, guild_id, items_list, wallet_balance)
                if items_list
                else None
            )
            await ctx.send(embed=embed, view=view)

        except Exception as exc:
            await ctx.send(
                "❌ An error occurred while loading the shop: "
                f"`{type(exc).__name__}: {exc}`"
            )

    @commands.hybrid_command(
        name="additem", description="Add a new item to the shop."
    )
    @app_commands.describe(name="Item name", price="Item price in coins")
    @staffperm("economy")
    @staff_only()
    async def additem(self, ctx: commands.Context, name: str, price: int) -> None:
        name = name.strip()
        if not name:
            return await ctx.send(
                "❌ Usage: `.additem \"item name\" <price>` or `/additem <name> <price>`"
            )
        if price <= 0:
            return await ctx.send("❌ Price must be greater than 0.")

        name_lower = name.lower()

        await ctx.send(f"📝 Enter the description for **{name}**:")

        def check(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

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

        role_id: int | None = None
        if choice in {"yes", "y"}:
            role_id = await prompt_for_role(ctx)

        guild_id = str(ctx.guild.id)
        item_data = {
            "_id": f"{guild_id}-{name_lower}",
            "guild": guild_id,
            "name": name,
            "name_lower": name_lower,
            "price": price,
            "description": description,
        }
        if role_id:
            item_data["role_id"] = role_id

        await guild_shop_col.replace_one(
            {"_id": item_data["_id"]}, item_data, upsert=True
        )

        confirmation_msg = (
            f"✅ Added **{name}** to the shop!\n"
            f"**Price:** {price}\n"
            f"**Description:** {description}"
        )
        if role_id:
            confirmation_msg += f"\n**Linked Role:** <@&{role_id}>"
        await ctx.send(confirmation_msg)

    @commands.hybrid_command(
        name="edititem",
        description="Edit an existing shop item.",
    )
    @staffperm("economy")
    @staff_only()
    async def edititem(self, ctx: commands.Context, *, name: str) -> None:
        guild_id = str(ctx.guild.id)
        item = await guild_shop_col.find_one(
            {"guild": guild_id, "name_lower": name.lower()}
        )
        if not item:
            return await ctx.send(f"❌ No item found with name `{name}`.")

        def check(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        await ctx.send(
            f"✏️ Enter a new name for **{item['name']}** (or type `skip` to keep the same):"
        )
        try:
            name_msg = await self.bot.wait_for("message", check=check, timeout=60)
            new_name = name_msg.content.strip()
            if new_name.lower() == "skip":
                new_name = item["name"]
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Edit cancelled due to timeout.")

        await ctx.send(f"💰 Enter a new price for **{new_name}** (or type `skip`):")
        try:
            price_msg = await self.bot.wait_for("message", check=check, timeout=60)
            new_price = (
                item["price"]
                if price_msg.content.lower() == "skip"
                else int(price_msg.content)
            )
        except (asyncio.TimeoutError, ValueError):
            return await ctx.send("❌ Invalid price or timeout. Edit cancelled.")

        await ctx.send(f"📝 Enter a new description for **{new_name}** (or type `skip`):")
        try:
            desc_msg = await self.bot.wait_for("message", check=check, timeout=120)
            new_desc = (
                item["description"]
                if desc_msg.content.lower() == "skip"
                else desc_msg.content.strip()
            )
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Edit cancelled due to timeout.")

        await ctx.send("🔗 Do you want to change the linked role? (yes/no)")
        try:
            choice_msg = await self.bot.wait_for("message", check=check, timeout=60)
            choice = choice_msg.content.lower()
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Edit cancelled due to timeout.")

        role_id = item.get("role_id")
        if choice in {"yes", "y"}:
            role_id = await prompt_for_role(ctx)

        await guild_shop_col.update_one(
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

        confirmation_msg = (
            f"✅ Updated **{new_name}**!\n"
            f"**Price:** {new_price}\n**Description:** {new_desc}"
        )
        if role_id:
            confirmation_msg += f"\n**Linked Role:** <@&{role_id}>"
        await ctx.send(confirmation_msg)

    @commands.hybrid_command(
        name="delitem", description="Remove an item from the shop."
    )
    @staffperm("economy")
    @staff_only()
    async def delitem(self, ctx: commands.Context, *, name: str) -> None:
        guild_id = str(ctx.guild.id)
        result = await guild_shop_col.delete_one(
            {"guild": guild_id, "name_lower": name.lower()}
        )
        if result.deleted_count:
            await ctx.send(f"🗑️ `{name}` removed from the shop.")
        else:
            await ctx.send("❌ Item not found.")

    @commands.hybrid_command(
        name="buy",
        description="Buy an item from the shop.",
        aliases=["purchase"],
    )
    @app_commands.describe(
        item="The item to buy (e.g., 'fishing rod', 'rifle', 'laptop'). "
        "You can add an optional amount at the end (e.g., 'coffee cup 10'). "
        "Use '/shop' to see available items."
    )
    @blacklist_barrier()
    @xp_earn(8, 16)
    async def buy(self, ctx: commands.Context, *, item: str | None = None) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        if not item:
            return await ctx.send("❌ You must specify an item to buy.")

        raw_item = item.strip()
        if not raw_item:
            return await ctx.send("❌ You must specify an item to buy.")

        guild_id = str(ctx.guild.id)

        async def fetch_store_item(name_lower: str) -> dict | None:
            shop_item = await guild_shop_col.find_one(
                {"guild": guild_id, "name_lower": name_lower}
            )
            if shop_item:
                return shop_item

            default_item = await shop_col.find_one({"name_lower": name_lower})
            if not default_item:
                return None

            shop_item = dict(default_item)
            shop_item["_id"] = f"{guild_id}-{default_item['_id']}"
            shop_item["guild"] = guild_id
            await guild_shop_col.update_one(
                {"_id": shop_item["_id"]},
                {"$set": shop_item},
                upsert=True,
            )
            return shop_item

        quantity = 1
        item_name_input = raw_item
        store_item = await fetch_store_item(raw_item.lower())

        if not store_item and " " in raw_item:
            maybe_name, maybe_quantity = raw_item.rsplit(" ", 1)
            maybe_name = maybe_name.strip()
            if maybe_quantity.isdigit() and maybe_name:
                quantity = int(maybe_quantity)
                if quantity <= 0:
                    return await ctx.send("❌ Quantity must be greater than 0.")
                item_name_input = maybe_name
                store_item = await fetch_store_item(item_name_input.lower())

        if not store_item:
            return await ctx.send(f"❌ Item **{item_name_input}** not found in the shop.")

        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        result = await process_shop_purchase(
            ctx.author, ctx.guild, store_item, data, quantity=quantity
        )
        await ctx.send(result["message"])

    @commands.hybrid_command(
        name="use",
        description="Use an item from your inventory.",
    )
    @app_commands.describe(
        item_name="The item to use (e.g., 'fishing rod', 'energy drink', 'laptop'). "
        "Use '/inventory' to see your items."
    )
    @blacklist_barrier()
    @xp_earn(7, 14)
    async def use(self, ctx: commands.Context, item_name: str) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        inventory = data.get("inventory", [])

        item_name = item_name.strip().lower()
        matched_item = next(
            (i for i in inventory if isinstance(i, str) and i.lower() == item_name),
            None,
        )
        if not matched_item:
            return await ctx.send("❌ You don’t have that item in your inventory.")

        if matched_item.lower() == "luck potion":
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {
                    "$pull": {"inventory": matched_item},
                    "$set": {"luck_buff": True},
                },
            )
            return await ctx.send(
                "🍀 You used a **Luck Potion**! You’ll have better odds in your "
                "next activities for 1 use."
            )

        await ctx.send("❌ That item can’t be used yet.")

    @commands.hybrid_command(
        name="inventory",
        description="View your items.",
        aliases=["inv"],
    )
    @blacklist_barrier()
    @xp_earn(3, 7)
    async def inventory(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        inv = data.get("inventory", [])
        if not inv:
            return await ctx.send("🎒 Your inventory is empty.")

        counts: dict[str, int] = {}
        duck_total = 0
        duck_uses = 0
        tool_durabilities: dict[str, list[int]] = {}

        for item in inv:
            if isinstance(item, dict) and item.get("_id") == "pet_duck":
                duck_total += 1
                duck_uses += int(item.get("uses_left", 0) or 0)
            elif isinstance(item, str):
                counts[item] = counts.get(item, 0) + 1
            elif isinstance(item, dict):
                item_key = item.get("name_lower") or item.get("_id") or item.get("name")
                if isinstance(item_key, str):
                    normalized_key = item_key.lower()
                    uses_left = item.get("uses_left")
                    if isinstance(uses_left, int):
                        tool_durabilities.setdefault(normalized_key, []).append(uses_left)
                    else:
                        counts[normalized_key] = counts.get(normalized_key, 0) + 1

        embed = discord.Embed(
            title=f"🎒 {ctx.author.display_name}'s Inventory",
            color=discord.Color.purple(),
        )

        if duck_total > 0:
            shop_item = await shop_col.find_one({"_id": "pet_duck"})
            embed.add_field(
                name=f"{shop_item['name']} x{duck_total}",
                value=f"{shop_item.get('description', '')} ({duck_uses} uses left total)",
                inline=False,
            )

        for key, durability_values in tool_durabilities.items():
            count = len(durability_values)
            min_uses = min(durability_values)
            max_uses = max(durability_values)
            shop_item = await shop_col.find_one({"name_lower": key})
            item_name = shop_item["name"] if shop_item else key.replace("_", " ").title()
            if min_uses == max_uses:
                durability_text = f"{min_uses} durability each"
            else:
                durability_text = f"{min_uses}-{max_uses} durability"
            embed.add_field(
                name=f"{item_name} x{count}",
                value=f"🛠️ {durability_text}",
                inline=False,
            )

        for key, count in counts.items():
            shop_item = await shop_col.find_one({"name_lower": key})
            if shop_item:
                embed.add_field(
                    name=f"{shop_item['name']} x{count}",
                    value=f"{shop_item.get('description', 'No description.')}",
                    inline=False,
                )
            else:
                clean_name = key.split("-", 1)[-1] if "-" in key else key
                embed.add_field(
                    name=f"📦 {clean_name.replace('_', ' ').title()} x{count}",
                    value="*Item no longer sold in shop*",
                    inline=False,
                )
            if len(embed.fields) >= 24:
                embed.add_field(
                    name="📦 More Items",
                    value=(
                        f"... and {len(counts) - len(embed.fields) + 1} more items! "
                        "Use `.inventory` again to see details."
                    ),
                    inline=False,
                )
                break

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog(bot))
