"""Event-handler cogs.

Each event family lives in its own cog so registration is automatic via the
``setup`` hook and the events get clean, discoverable names in DEBUG output.
"""

from __future__ import annotations

EVENT_EXTENSIONS: tuple[str, ...] = (
    "bot.events.checks",
    "bot.events.guild",
    "bot.events.lifecycle",
    "bot.events.errors",
    "bot.events.messages",
    "bot.events.members",
    "bot.events.reactions",
    "bot.events.ready",
)

__all__ = ["EVENT_EXTENSIONS"]
