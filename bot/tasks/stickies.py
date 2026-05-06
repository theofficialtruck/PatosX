"""Periodic loop that ensures sticky notes are always visible at channel bottom."""

from __future__ import annotations

import discord
from discord.ext import tasks

from bot.database import sticky_col
from bot.utils.state import has_bot, get_bot
from bot.utils.stickies import last_sticky_msg


@tasks.loop(minutes=2)
async def check_and_repost_stickies() -> None:
    if not has_bot():
        return
    bot = get_bot()

    try:
        cursor = sticky_col.find({})
        async for doc in cursor:
            guild_id = doc["guild"]
            channel_id = int(doc["channel"])
            sticky_text = doc["text"]

            guild = bot.get_guild(int(guild_id))
            if not guild:
                continue
            channel = guild.get_channel(channel_id)
            if not channel:
                continue

            stored_message_id = doc.get("message")
            message_exists = False

            if stored_message_id:
                try:
                    await channel.fetch_message(stored_message_id)
                    message_exists = True
                except discord.NotFound:
                    print(
                        f"[Sticky Notes] Message {stored_message_id} not found, "
                        "reposting..."
                    )
                except discord.Forbidden:
                    print(
                        f"[Sticky Notes] No permission to check message "
                        f"{stored_message_id}"
                    )
                    continue
                except Exception as exc:
                    print(
                        f"[Sticky Notes] Error checking message "
                        f"{stored_message_id}: {exc}"
                    )
                    continue

            if not message_exists:
                try:
                    sent = await channel.send(sticky_text)
                    last_sticky_msg[channel_id] = sent.id
                    await sticky_col.update_one(
                        {"guild": guild_id, "channel": str(channel_id)},
                        {"$set": {"message": sent.id}},
                    )
                    print(
                        f"[Sticky Notes] Reposted sticky note in channel {channel_id}"
                    )
                except Exception as exc:
                    print(f"[Sticky Notes] Failed to repost sticky note: {exc}")
    except Exception as exc:
        print(f"[Sticky Notes] Error in check_and_repost_stickies: {exc}")


@check_and_repost_stickies.before_loop
async def _wait() -> None:
    if not has_bot():
        return
    await get_bot().wait_until_ready()


__all__ = ["check_and_repost_stickies"]
