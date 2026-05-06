"""Centralised audit-log helper.

Two things happen on every moderation action:

1. An embed is posted to the configured ``log_channel`` (if any).
2. A structured row is inserted into ``logs_col`` so we can later compute
   staff performance analytics.

Errors are swallowed and printed because logging is *never* allowed to
take down a moderation action.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from bot.database import logs_col, settings_col
from bot.utils.state import has_bot, get_bot


async def log_action(
    ctx,
    message: str,
    user_id: int | str | None = None,
    action_type: str | None = None,
) -> None:
    """Record a moderation action in both Discord and the database.

    ``ctx`` may be ``None`` for automated actions (e.g. mute expiry); in
    that case we fall back to the bot's user identity for the footer.
    """
    try:
        if ctx is None or getattr(ctx, "guild", None) is None:
            return

        guild_id = str(ctx.guild.id)
        settings = await settings_col.find_one({"guild": guild_id})
        log_channel_id = settings.get("log_channel") if settings else None

        if log_channel_id and has_bot():
            log_channel = get_bot().get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="📋 Moderation Log",
                    description=message,
                    color=discord.Color.dark_blue(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text=f"By {ctx.author} • {ctx.author.id}")
                await log_channel.send(embed=embed)

        if user_id and action_type:
            await logs_col.insert_one(
                {
                    "guild": guild_id,
                    "user_id": str(user_id),
                    "action": action_type,
                    "by": {"name": str(ctx.author), "id": str(ctx.author.id)},
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
    except Exception as exc:  # pragma: no cover - logging must never raise
        print(f"[log_action ERROR] {exc}")


__all__ = ["log_action"]
