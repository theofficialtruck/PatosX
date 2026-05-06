"""Generic confirm/cancel views shared between cogs."""

from __future__ import annotations

import discord
from discord.ui import View


class ConfirmSellAll(View):
    """Two-button confirmation used by the ``sell all`` flow."""

    def __init__(self, ctx, prices, inventory, user_id, wallet) -> None:
        super().__init__(timeout=30)
        self.ctx = ctx
        self.value: bool | None = None
        self.prices = prices
        self.inventory = inventory
        self.user_id = user_id
        self.wallet = wallet

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = True
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = False
        self.stop()


__all__ = ["ConfirmSellAll"]
