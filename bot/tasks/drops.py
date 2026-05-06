"""Background expiry of unclaimed money drops."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord.ext import tasks

from bot.database import drop_instances_col
from bot.utils.economy import add_balance
from bot.utils.state import has_bot, get_bot


@tasks.loop(hours=1)
async def check_expired_drops() -> None:
    if not has_bot():
        return
    bot = get_bot()

    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    query = {
        "claimed": False,
        "created_at": {"$lt": three_days_ago.isoformat()},
    }

    async for drop in drop_instances_col.find(query):
        try:
            guild = bot.get_guild(int(drop["guild_id"]))
            if not guild:
                continue
            channel = guild.get_channel(int(drop["channel_id"]))
            if not channel:
                continue
            message = await channel.fetch_message(int(drop["message_id"]))
            await message.delete()
        except discord.NotFound:
            pass
        except Exception as exc:
            print(
                f"Error deleting drop message {drop['message_id']}: {exc}"
            )

        if not drop.get("staff_drop"):
            try:
                await add_balance(
                    int(drop["author_id"]),
                    int(drop["guild_id"]),
                    int(drop["amount"]),
                )
            except Exception as exc:
                print(
                    f"Error refunding drop {drop['_id']} to "
                    f"{drop['author_id']}: {exc}"
                )

        await drop_instances_col.delete_one({"_id": drop["_id"]})


@check_expired_drops.before_loop
async def _wait() -> None:
    if not has_bot():
        return
    await get_bot().wait_until_ready()


__all__ = ["check_expired_drops"]
