"""Background ``tasks.loop`` registry.

Tasks are defined as module-level ``Loop`` objects so the on_ready handler
can iterate the canonical list and call ``.start()`` on each one. Adding a
new background task means appending to ``ALL_TASKS`` — ``on_ready`` picks
it up automatically.
"""

from __future__ import annotations

from typing import Callable, Iterable

from discord.ext import tasks

from bot.tasks.boosters import check_boosters_loop  # noqa: F401
from bot.tasks.conversations import periodic_cleanup
from bot.tasks.drops import check_expired_drops
from bot.tasks.invites import cleanup_invite_cache, update_invite_cache
from bot.tasks.polls import check_polls
from bot.tasks.reminders import check_reminders
from bot.tasks.stickies import check_and_repost_stickies
from bot.tasks.unmute import check_expired_mutes, check_muted_role_permissions

# We deliberately exclude ``check_boosters_loop`` because it's still being
# evaluated and is currently disabled in production.
_TASKS: tuple[tasks.Loop, ...] = (
    cleanup_invite_cache,
    update_invite_cache,
    periodic_cleanup,
    check_expired_drops,
    check_reminders,
    check_expired_mutes,
    check_muted_role_permissions,
    check_and_repost_stickies,
    check_polls,
)


def all_tasks() -> Iterable[tasks.Loop]:
    """Return every task registered with the bot."""
    return _TASKS


__all__: tuple[str, ...] = (
    "all_tasks",
    "check_boosters_loop",
    "cleanup_invite_cache",
    "update_invite_cache",
    "periodic_cleanup",
    "check_expired_drops",
    "check_reminders",
    "check_expired_mutes",
    "check_muted_role_permissions",
    "check_and_repost_stickies",
    "check_polls",
)
