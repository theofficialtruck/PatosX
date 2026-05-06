"""Bot factory + ``DuckParadiseBot`` subclass.

Centralising bot construction here keeps ``main.py`` skinny: anyone wanting
to embed the bot in a different process (an aiohttp app, a script, a test)
just calls :func:`create_bot` and is done.
"""

from __future__ import annotations

import sys
import types

# discord.py's voice module imports ``audioop``, which was removed in Python
# 3.13. We don't use voice features, so a stub keeps imports happy.
sys.modules.setdefault("audioop", types.ModuleType("audioop"))

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402

from bot.prefix import get_prefix  # noqa: E402
from bot.utils.state import set_bot  # noqa: E402


class DuckParadiseBot(commands.Bot):
    """Application-specific subclass; primarily exists for type clarity."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix=get_prefix,
            intents=discord.Intents.all(),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=True, roles=True
            ),
        )

        # Per-guild lock toggled by the ``stop``/``override`` commands.
        self.bot_locks: dict[str, bool] = {}

        # Populated by the help cog so views can recover it after a restart.
        self.help_pages: list[discord.Embed] = []

        # The on_ready handler uses this guard so persistent views are only
        # registered once even if Discord re-emits READY.
        self.views_loaded: bool = False


def create_bot() -> DuckParadiseBot:
    """Construct the bot, expose it to utility helpers, and return it."""
    bot = DuckParadiseBot()
    set_bot(bot)
    print("🔧 Bot initialized with built-in tree")
    print(f"🔧 Bot object: {bot}")
    print(f"🔧 Tree object: {bot.tree}")
    return bot


__all__ = ["DuckParadiseBot", "create_bot"]
