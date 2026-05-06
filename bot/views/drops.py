"""Persistent claim button for the ``drop`` command."""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from bot.database import drop_instances_col
from bot.utils.economy import add_balance


class DropClaimView(discord.ui.View):
    """A persistent view (no timeout) so claim buttons survive bot restarts."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.success,
        custom_id="drop_claim",
    )
    async def claim(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        msg_id = str(interaction.message.id)
        doc = await drop_instances_col.find_one({"message_id": msg_id})
        if not doc:
            await interaction.response.send_message(
                "⚠️ This drop is no longer valid.", ephemeral=True
            )
            return
        if str(doc.get("author_id")) == str(interaction.user.id):
            await interaction.response.send_message(
                "❌ You can't claim your own drop.", ephemeral=True
            )
            return
        if doc.get("claimed"):
            await interaction.response.send_message(
                "⚠️ This drop has already been claimed.", ephemeral=True
            )
            return

        amount = int(doc.get("amount", 0))
        await drop_instances_col.update_one(
            {"message_id": msg_id},
            {
                "$set": {
                    "claimed": True,
                    "claimer_id": str(interaction.user.id),
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        await add_balance(interaction.user.id, interaction.guild.id, amount)

        button.disabled = True
        embed = (
            interaction.message.embeds[0] if interaction.message.embeds else None
        )
        if embed:
            embed.description = (
                f"Claimed by {interaction.user.mention} for 🪙 {amount:,}"
            )
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(
            f"✅ You claimed 🪙 {amount:,}", ephemeral=True
        )


__all__ = ["DropClaimView"]
