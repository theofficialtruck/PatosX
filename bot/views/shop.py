"""Shop browse + purchase views."""

from __future__ import annotations

import discord

from bot.database import economy_col


async def process_shop_purchase(
    member: discord.Member,
    guild: discord.Guild,
    store_item: dict,
    user_data: dict,
    quantity: int = 1,
) -> dict:
    """Run the buy logic for a single shop item.

    Returns a dict the caller can use to render an embed (success or error).
    Atomic-ish: a refund is issued if the role grant fails after the wallet
    deduction, so a failed purchase never leaves the user out of pocket.
    """
    item_name = store_item.get("name") or store_item.get("name_lower") or "Unknown Item"

    if quantity <= 0:
        return {"ok": False, "message": "❌ Quantity must be greater than 0."}

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
    total_price = price * quantity

    if role_id is not None:
        if quantity != 1:
            return {
                "ok": False,
                "message": "❌ Role items can only be purchased one at a time.",
            }
        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            role = None

        if not role:
            return {"ok": False, "message": "❌ This item's linked role is invalid or was deleted. Ask staff to update it."}

        if role in getattr(member, "roles", []):
            return {"ok": False, "message": f"✅ You already have the role for **{item_name}**."}

        if wallet < price:
            return {
                "ok": False,
                "message": f"❌ You don’t have enough coins. **{item_name}** costs {price} coins.",
            }

        new_wallet = wallet - price
        await economy_col.update_one({"_id": user_key}, {"$set": {"wallet": new_wallet}})

        try:
            await member.add_roles(role, reason=f"Purchased shop role item: {item_name}")
        except (discord.Forbidden, discord.HTTPException) as role_error:
            await economy_col.update_one({"_id": user_key}, {"$set": {"wallet": wallet}})
            return {"ok": False, "message": f"❌ Couldn't assign the role (`{role_error}`). You were refunded."}

        return {
            "ok": True,
            "message": f"✅ You bought **{item_name}** for {price} coins and got {role.mention}!",
            "item_name": item_name,
            "price": price,
            "unit_price": price,
            "quantity": 1,
            "old_wallet": wallet,
            "new_wallet": new_wallet,
            "purchase_type": "role",
            "role_mention": role.mention,
        }

    if wallet < total_price:
        return {
            "ok": False,
            "message": f"❌ You don’t have enough coins. **{item_name} x{quantity}** costs {total_price} coins.",
        }

    new_wallet = wallet - total_price
    item_id = str(store_item.get("_id", ""))
    is_pet_duck = (
        store_item.get("name_lower") == "pet_duck"
        or item_id == "pet_duck"
        or item_id.endswith("-pet_duck")
    )

    if is_pet_duck:
        inventory.extend({"_id": "pet_duck", "uses_left": 3} for _ in range(quantity))
        success_message = (
            f"🦆 You bought **{item_name} x{quantity}** for {total_price} coins! "
            "Each has 3 uses."
        )
        purchase_type = "pet_duck"
    else:
        inventory.extend(
            [store_item.get("name_lower", item_name.lower())] * quantity
        )
        success_message = f"✅ You bought **{item_name} x{quantity}** for {total_price} coins!"
        purchase_type = "inventory"

    await economy_col.update_one(
        {"_id": user_key},
        {"$set": {"wallet": new_wallet, "inventory": inventory}},
    )

    return {
        "ok": True,
        "message": success_message,
        "item_name": item_name,
        "price": total_price,
        "unit_price": price,
        "quantity": quantity,
        "old_wallet": wallet,
        "new_wallet": new_wallet,
        "purchase_type": purchase_type,
    }


class ShopDropdown(discord.ui.View):
    """Single-select that triggers ``process_shop_purchase``."""

    def __init__(self, user_id, guild_id, items, user_balance, options) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.guild_id = guild_id
        self.items = items
        self.balance = user_balance

        self.dropdown = discord.ui.Select(
            placeholder="Choose an item to buy...", options=options[:25]
        )
        self.dropdown.callback = self._dropdown_callback
        self.add_item(self.dropdown)

    async def _dropdown_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ You can't use this dropdown!", ephemeral=True
            )
            return

        selected_item_id = self.dropdown.values[0]
        selected_item = next(
            (item for item in self.items if item["_id"] == selected_item_id), None
        )
        if not selected_item:
            await interaction.response.send_message("❌ Item not found!", ephemeral=True)
            return

        from bot.utils.economy import get_user

        try:
            guild_id = str(interaction.guild.id)
            user_data = await get_user(None, guild_id, interaction.user.id)
            result = await process_shop_purchase(
                interaction.user, interaction.guild, selected_item, user_data
            )

            if not result["ok"]:
                await interaction.response.send_message(result["message"], ephemeral=True)
                return

            self.balance = result["new_wallet"]

            embed = discord.Embed(
                title="✅ Purchase Successful!",
                description=(
                    f"You bought **{result['item_name']}**!\n\n"
                    f"Price: 🪙 {result['price']:,}\n"
                    f"Old Wallet: 🪙 {result['old_wallet']:,}\n"
                    f"New Wallet: 🪙 {result['new_wallet']:,}"
                    + (
                        f"\nRole Granted: {result['role_mention']}"
                        if result["purchase_type"] == "role"
                        else "\n\nUse `.inventory` to view your items!"
                    )
                ),
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            self.dropdown.disabled = True
            self.dropdown.placeholder = "Purchase completed!"

            try:
                await interaction.followup.edit_message(
                    interaction.message.id, view=self
                )
            except (discord.NotFound, discord.HTTPException):
                pass

        except Exception as exc:
            await interaction.response.send_message(
                f"❌ An error occurred during purchase: `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )


class ShopView(discord.ui.View):
    """Top-level shop view with a "Buy Items" button that opens the dropdown."""

    def __init__(self, user_id, guild_id, items, user_balance) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.guild_id = guild_id
        self.items = items
        self.balance = user_balance

    @discord.ui.button(
        label="🛒 Buy Items",
        style=discord.ButtonStyle.green,
        custom_id="buy_items_button",
    )
    async def buy_items(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ You can't use this button!", ephemeral=True
            )
            return

        if not self.items:
            await interaction.response.send_message(
                "❌ The shop is empty!", ephemeral=True
            )
            return

        options: list[discord.SelectOption] = []
        for item in self.items:
            display_name = item.get("name") or item.get("_id", "Unnamed Item")
            price = item.get("price", "Unknown")
            description = item.get("description", "No description available.")

            can_afford = (
                "✅" if isinstance(price, (int, float)) and self.balance >= price else "❌"
            )
            options.append(
                discord.SelectOption(
                    label=f"{display_name} - 🪙 {price}",
                    description=(
                        f"{description[:50]}..." if len(description) > 50 else description
                    ),
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


__all__ = ["ShopView", "ShopDropdown", "process_shop_purchase"]
