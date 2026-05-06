"""Sticky-note bookkeeping shared by the cog and the periodic loop.

State that needs to outlive a single command lives here:

* ``last_sticky_msg`` maps a channel id to the last sticky message id we
  posted there. The on_message handler uses it to delete the prior copy
  before reposting so the sticky stays at the bottom of the channel.
* ``last_sticky_trigger`` debounces ``check_and_repost_stickies`` so a
  rapid burst of messages doesn't hammer Discord.
"""

from __future__ import annotations

from collections import defaultdict

import discord

from bot.database import sticky_col
from bot.utils.state import has_bot, get_bot


last_sticky_msg: dict[int, int] = {}
last_sticky_trigger: defaultdict[int, float] = defaultdict(float)


async def load_sticky_messages() -> None:
    """Hydrate ``last_sticky_msg`` from the database on startup."""
    try:
        cursor = sticky_col.find({})
        async for doc in cursor:
            if "message" in doc:
                last_sticky_msg[int(doc["channel"])] = doc["message"]
        print(
            f"[Sticky Notes] Loaded {len(last_sticky_msg)} sticky message IDs "
            "from database"
        )
    except Exception as exc:  # pragma: no cover
        print(f"[Sticky Notes] Error loading sticky messages: {exc}")


async def load_sticky_notes() -> None:
    """Repost every active sticky on startup so they sit at the channel bottom."""
    if not has_bot():
        return
    bot = get_bot()
    print("📝 Loading sticky notes...")
    loaded_count = 0

    async for doc in sticky_col.find({}):
        try:
            guild = bot.get_guild(int(doc["guild"]))
            if not guild:
                continue
            channel = guild.get_channel(int(doc["channel"]))
            if not channel:
                continue

            try:
                existing_msg = await channel.fetch_message(doc["message"])
                await existing_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

            new_msg = await channel.send(doc["text"])
            await sticky_col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"message": new_msg.id}},
            )
            last_sticky_msg[int(doc["channel"])] = new_msg.id
            loaded_count += 1
        except Exception as exc:
            print(
                f"❌ Failed to load sticky note for "
                f"{doc.get('guild')}-{doc.get('channel')}: {exc}"
            )

    print(f"✅ Loaded {loaded_count} sticky notes")


async def repost_sticky_note(channel_id, guild_id) -> None:
    """Force-repost the sticky for a single channel (used after vanity logs)."""
    if not has_bot():
        return
    bot = get_bot()
    doc = await sticky_col.find_one(
        {"guild": str(guild_id), "channel": str(channel_id)}
    )
    if not doc:
        return

    try:
        guild = bot.get_guild(int(guild_id))
        channel = guild.get_channel(int(channel_id)) if guild else None
        if not channel:
            return

        try:
            old_msg = await channel.fetch_message(doc["message"])
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        new_msg = await channel.send(doc["text"])
        await sticky_col.update_one(
            {"guild": str(guild_id), "channel": str(channel_id)},
            {"$set": {"message": new_msg.id}},
        )
    except Exception as exc:
        print(f"❌ Failed to repost sticky note: {exc}")


# --- One-time channels ----------------------------------------------------
# Maps guild_id -> {channel_id -> {user_id -> first-message timestamp}}
onetime_channels: dict[str, dict[str, dict[str, object]]] = {}


async def load_onetime_channels() -> None:
    """Read one-time-channel state from settings_col into the in-memory map."""
    from bot.database import settings_col

    try:
        cursor = settings_col.find({"onetime_channels": {"$exists": True}})
        async for doc in cursor:
            guild_id = doc["guild"]
            data = doc.get("onetime_channels", {})
            if data:
                onetime_channels.setdefault(guild_id, {}).update(data)
        print(
            "[One-Time Channels] Loaded one-time channels for "
            f"{len(onetime_channels)} guilds"
        )
    except Exception as exc:  # pragma: no cover
        print(f"[One-Time Channels] Error loading one-time channels: {exc}")


__all__ = [
    "last_sticky_msg",
    "last_sticky_trigger",
    "onetime_channels",
    "load_sticky_messages",
    "load_sticky_notes",
    "load_onetime_channels",
    "repost_sticky_note",
]
