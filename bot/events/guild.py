"""Guild-level lifecycle events (join, etc.)."""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.config.constants import DEFAULT_PREFIX
from bot.database import settings_col


class GuildEventsCog(commands.Cog, name="GuildEvents"):
    """Initialise per-guild defaults when the bot is added."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await settings_col.update_one(
            {"guild": str(guild.id)},
            {"$setOnInsert": {"prefix": DEFAULT_PREFIX}},
            upsert=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildEventsCog(bot))
