"""Rate-limited fetcher for guild invites.

Discord rate-limits ``GET /guilds/.../invites`` aggressively, so we keep a
per-process cache and serialise actual HTTP calls through an asyncio queue.
Each guild also has a per-guild cooldown to spread load across many guilds.
"""

from __future__ import annotations

import asyncio
import time

import discord

from bot.config.constants import GLOBAL_RATE_LIMIT, INVITE_CACHE_DURATION

# Caches keyed on guild id ------------------------------------------------
invite_cache: dict[int, tuple[float, list]] = {}
last_invite_fetch: dict[int, float] = {}
last_global_invite_fetch: float = 0
_processing_invite: bool = False
_invite_queue: asyncio.Queue = asyncio.Queue()


async def _process_invite_queue() -> None:
    """Background worker that pulls one fetch off the queue at a time."""
    global last_global_invite_fetch
    while True:
        try:
            guild, future = await _invite_queue.get()

            current_time = time.time()
            time_since_global = current_time - last_global_invite_fetch
            if time_since_global < GLOBAL_RATE_LIMIT:
                await asyncio.sleep(GLOBAL_RATE_LIMIT - time_since_global)

            guild_id = guild.id
            if guild_id in last_invite_fetch:
                time_since_last = current_time - last_invite_fetch[guild_id]
                if time_since_last < 60:
                    await asyncio.sleep(60 - time_since_last)

            try:
                last_global_invite_fetch = time.time()
                invites = await guild.invites()
                invite_cache[guild_id] = (current_time, invites)
                last_invite_fetch[guild_id] = current_time
                future.set_result(invites)
            except discord.HTTPException as exc:
                if exc.status == 429:
                    print(
                        f"⚠️ Rate limited for guild {guild.name}, "
                        f"waiting {exc.retry_after or 10}s..."
                    )
                    await asyncio.sleep(getattr(exc, "retry_after", 10))
                    try:
                        invites = await guild.invites()
                        invite_cache[guild_id] = (current_time, invites)
                        last_invite_fetch[guild_id] = current_time
                        future.set_result(invites)
                    except Exception as retry_exc:
                        print(f"❌ Retry failed for {guild.name}: {retry_exc}")
                        future.set_exception(retry_exc)
                else:
                    future.set_exception(exc)

            _invite_queue.task_done()
            await asyncio.sleep(5)

        except Exception as exc:  # pragma: no cover
            print(f"❌ Error in invite queue processor: {exc}")
            await asyncio.sleep(10)


async def get_guild_invites(guild: discord.Guild) -> list:
    """Return cached invites, otherwise queue a fresh fetch."""
    global _processing_invite
    guild_id = guild.id
    current_time = time.time()

    if guild_id in invite_cache:
        cached_time, cached_invites = invite_cache[guild_id]
        if current_time - cached_time < INVITE_CACHE_DURATION:
            return cached_invites

    if not _processing_invite:
        _processing_invite = True
        asyncio.create_task(_process_invite_queue())

    future: asyncio.Future = asyncio.Future()
    await _invite_queue.put((guild, future))

    try:
        return await future
    except discord.HTTPException as exc:
        if exc.status == 429:
            print(f"⚠️ Rate limited for guild {guild.name}, using cached data...")
            return invite_cache.get(guild_id, (0, []))[1]
        print(f"❌ Error fetching invites for {guild.name}: {exc}")
        return invite_cache.get(guild_id, (0, []))[1]


async def get_invites_count(guild_id: int, user_id: int) -> int:
    """Sum the ``uses`` field for every invite owned by ``user_id``."""
    from bot.database import invites_col  # local import: avoid cycles

    total_uses = 0
    async for code_doc in invites_col.find(
        {"guild_id": str(guild_id), "inviter_id": str(user_id)}
    ):
        try:
            total_uses += int(code_doc.get("uses", 0))
        except Exception:
            pass
    return total_uses


__all__ = [
    "invite_cache",
    "last_invite_fetch",
    "last_global_invite_fetch",
    "get_guild_invites",
    "get_invites_count",
]
