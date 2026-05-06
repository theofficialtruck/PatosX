"""Claimable role buttons attached to the ``/roles`` embed."""

from __future__ import annotations

import discord
from discord.ui import Button, View


class RoleButtons(View):
    """One toggle button per role; persists across restarts via custom_id."""

    def __init__(self, roles: list[int], guild_id: int, guild: discord.Guild) -> None:
        super().__init__(timeout=None)
        self.guild_id = guild_id

        for role_id in roles:
            role = guild.get_role(role_id)
            if not role:
                continue

            role_button = Button(
                label=role.name,
                style=discord.ButtonStyle.primary,
                custom_id=f"claim_{role_id}",
            )
            role_button.callback = self._make_callback(role_id)
            self.add_item(role_button)

    def _make_callback(self, role_id: int):
        async def callback(interaction: discord.Interaction) -> None:
            role = interaction.guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message(
                    "❌ Role not found.", ephemeral=True
                )

            await interaction.response.defer(ephemeral=True)

            try:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role)
                    msg = f"❌ Removed {role.mention}"
                else:
                    await interaction.user.add_roles(role)
                    msg = f"✅ You claimed {role.mention}"
                await interaction.followup.send(msg, ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send(
                    "⚠️ I don't have permission to manage roles.", ephemeral=True
                )
            except Exception as exc:
                await interaction.followup.send(
                    f"❌ Something went wrong: `{exc}`", ephemeral=True
                )

        return callback


__all__ = ["RoleButtons"]
