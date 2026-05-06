"""Connection/disconnect listeners."""

from __future__ import annotations

from discord.ext import commands


class LifecycleCog(commands.Cog, name="Lifecycle"):
    """Misc connection-state events."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        print("⚠️ Bot disconnected from Discord. Will attempt reconnect soon.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LifecycleCog(bot))
