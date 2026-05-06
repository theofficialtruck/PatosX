"""Periodic poll-closer loop."""

from __future__ import annotations

from datetime import datetime, timezone

from discord.ext import tasks

from bot.database import polls_col
from bot.utils.state import has_bot, get_bot
from bot.views.polls import build_poll_embed


@tasks.loop(seconds=30)
async def check_polls() -> None:
    if not has_bot():
        return
    bot = get_bot()
    now = datetime.now(timezone.utc)

    async for poll in polls_col.find({"end_time": {"$lte": now}}):
        channel = bot.get_channel(int(poll["channel_id"]))
        if not channel:
            continue
        try:
            msg = await channel.fetch_message(int(poll["message_id"]))
            counts: dict[str, int] = {}
            for vote in poll["votes"].values():
                counts[vote] = counts.get(vote, 0) + 1
            closed_embed = build_poll_embed(
                poll["question"], poll["options"], counts, closed=True
            )
            await msg.edit(embed=closed_embed, view=None)
            await channel.send("⏰ Poll closed!", reference=msg)
        except Exception as exc:
            print(f"Error closing poll {poll['poll_id']}: {exc}")

        await polls_col.delete_one({"poll_id": poll["poll_id"]})


@check_polls.before_loop
async def _wait() -> None:
    if not has_bot():
        return
    await get_bot().wait_until_ready()


__all__ = ["check_polls"]
