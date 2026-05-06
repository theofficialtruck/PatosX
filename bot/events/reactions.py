"""Raw reaction add/remove listeners for reaction roles."""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database import reaction_col


class ReactionEventsCog(commands.Cog, name="ReactionEvents"):
    """Grant/remove the configured role when a reaction is toggled."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        if payload.user_id == self.bot.user.id:
            return

        data = await reaction_col.find_one({"message": payload.message_id})
        if not data:
            return
        if str(payload.emoji) != data["emoji"]:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(data["role"])
        if role is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None:
            return

        try:
            await member.add_roles(role)
        except Exception as exc:
            print(f"[reactionrole add error] {exc}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        data = await reaction_col.find_one({"message": payload.message_id})
        if not data:
            return
        if str(payload.emoji) != data["emoji"]:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(data["role"])
        if role is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None:
            return

        try:
            await member.remove_roles(role)
        except Exception as exc:
            print(f"[reactionrole remove error] {exc}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionEventsCog(bot))
