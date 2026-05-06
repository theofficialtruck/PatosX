"""Daily DuckGPT conversation cleanup."""

from __future__ import annotations

from discord.ext import tasks

from bot.utils.ai import cleanup_old_conversations
from bot.utils.state import has_bot, get_bot


@tasks.loop(hours=24)
async def periodic_cleanup() -> None:
    deleted = await cleanup_old_conversations()
    print(f"[DuckGPT] Cleanup complete: {deleted} old conversations removed.")


@periodic_cleanup.before_loop
async def _wait() -> None:
    if not has_bot():
        return
    await get_bot().wait_until_ready()


__all__ = ["periodic_cleanup"]
