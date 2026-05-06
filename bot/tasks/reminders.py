"""Reminder dispatch loop."""

from __future__ import annotations

from datetime import datetime, timezone

from discord.ext import tasks

from bot.database import reminders_col
from bot.utils.state import has_bot, get_bot


@tasks.loop(seconds=0.01)
async def check_reminders() -> None:
    if not has_bot():
        return
    bot = get_bot()
    now = datetime.now(timezone.utc)

    reminders = await reminders_col.find(
        {"remind_at": {"$lte": now}}
    ).to_list(length=None)

    for reminder in reminders:
        user = bot.get_user(int(reminder["user_id"]))
        if user:
            try:
                await user.send(f"⏰ Reminder: {reminder['message']}")
            except Exception as exc:
                print(f"Failed to send reminder to {user}: {exc}")
        await reminders_col.delete_one({"_id": reminder["_id"]})


@check_reminders.before_loop
async def _wait() -> None:
    if not has_bot():
        return
    await get_bot().wait_until_ready()


__all__ = ["check_reminders"]
