"""Per-guild prefix resolver passed to ``commands.Bot``.

Stored prefixes live on the ``settings_col`` document. DMs always fall back
to the default ``?`` prefix.
"""

from __future__ import annotations

import discord

from bot.config.constants import DEFAULT_PREFIX
from bot.database import settings_col


async def get_prefix(bot, message: discord.Message) -> str:
    """Resolve the per-guild prefix, defaulting to ``DEFAULT_PREFIX``."""
    if not message.guild:
        return DEFAULT_PREFIX
    doc = await settings_col.find_one({"guild": str(message.guild.id)})
    return doc.get("prefix", DEFAULT_PREFIX) if doc else DEFAULT_PREFIX


__all__ = ["get_prefix"]
