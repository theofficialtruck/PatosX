"""Cog registry.

``EXTENSIONS`` is the canonical list of extension paths the runner loads at
startup. Adding a new cog is therefore one line of code instead of an import
plus a ``bot.add_cog`` call somewhere else.
"""

from __future__ import annotations

EXTENSIONS: tuple[str, ...] = (
    "bot.cogs.staff",
    "bot.cogs.configuration",
    "bot.cogs.blacklist",
    "bot.cogs.vanity",
    "bot.cogs.invites",
    "bot.cogs.economy",
    "bot.cogs.shop",
    "bot.cogs.investments",
    "bot.cogs.activities",
    "bot.cogs.games",
    "bot.cogs.quiz",
    "bot.cogs.fun",
    "bot.cogs.afk",
    "bot.cogs.tickets",
    "bot.cogs.polls",
    "bot.cogs.giveaways",
    "bot.cogs.roles",
    "bot.cogs.money_admin",
    "bot.cogs.reactionroles",
    "bot.cogs.stickynotes",
    "bot.cogs.welcome",
    "bot.cogs.server",
    "bot.cogs.help",
    "bot.cogs.moderation",
    "bot.cogs.modview",
    "bot.cogs.disable_cmds",
    "bot.cogs.system",
)

__all__ = ["EXTENSIONS"]
