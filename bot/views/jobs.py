"""Two-button picker used by the ``choosejob`` command."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import ButtonStyle
from discord.ui import View, Button, button as ui_button

from bot.database import economy_col


class JobPicker(View):
    """Lets the invoker pick between Developer and Duck."""

    def __init__(self, ctx) -> None:
        super().__init__(timeout=30)
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user == self.ctx.author

    @ui_button(label="Developer 🧑‍💻", style=ButtonStyle.blurple)
    async def dev_button(self, interaction: discord.Interaction, button: Button) -> None:
        await self._set_job(interaction, "developer")

    @ui_button(label="Duck 🦆", style=discord.ButtonStyle.green)
    async def duck_button(self, interaction: discord.Interaction, button: Button) -> None:
        await self._set_job(interaction, "duck")

    async def _set_job(self, interaction: discord.Interaction, job_name: str) -> None:
        await economy_col.update_one(
            {"_id": f"{self.ctx.guild.id}-{self.ctx.author.id}"},
            {
                "$set": {
                    "job": job_name,
                    "job_start": datetime.now(timezone.utc).isoformat(),
                    "promotion_level": 0,
                    "promotion_chance": 20.0,
                    "last_promo_check": None,
                }
            },
            upsert=True,
        )
        await interaction.response.edit_message(
            content=f"✅ You are now working as a **{job_name.capitalize()}**!",
            view=None,
        )


__all__ = ["JobPicker"]
