"""Background loops for invite-cache hygiene."""

from __future__ import annotations

import asyncio
import time

from discord.ext import tasks

from bot.config.constants import INVITE_CACHE_DURATION
from bot.utils.invites_cache import get_guild_invites, invite_cache
from bot.utils.state import has_bot, get_bot


@tasks.loop(hours=1)
async def cleanup_invite_cache() -> None:
    """Drop cached invite payloads that have aged out."""
    current_time = time.time()
    expired_keys: list[int] = []

    for guild_id, cached_data in invite_cache.items():
        if isinstance(cached_data, tuple) and len(cached_data) == 2:
            cached_time, _ = cached_data
            if current_time - cached_time > INVITE_CACHE_DURATION * 2:
                expired_keys.append(guild_id)
        elif isinstance(cached_data, list):
            expired_keys.append(guild_id)

    for key in expired_keys:
        del invite_cache[key]

    if expired_keys:
        print(f"🧹 Cleaned up {len(expired_keys)} expired invite cache entries")


@tasks.loop(hours=1)
async def update_invite_cache() -> None:
    """Refresh invite caches gradually so we don't hit Discord rate limits."""
    if not has_bot():
        return
    bot = get_bot()
    for guild in bot.guilds:
        try:
            await get_guild_invites(guild)
            await asyncio.sleep(10)
        except Exception as exc:
            print(f"⚠️ Error updating invite cache for {guild.name}: {exc}")


@cleanup_invite_cache.before_loop
@update_invite_cache.before_loop
async def _wait() -> None:
    if not has_bot():
        return
    await get_bot().wait_until_ready()


__all__ = ["cleanup_invite_cache", "update_invite_cache"]
