"""Periodic booster-thanks loop (currently kept disabled — see on_ready).

This is retained as-is so it can be re-enabled if Discord changes behaviour
around system boost messages.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import tasks

from bot.database import boost_col, config_col
from bot.utils.state import has_bot, get_bot


@tasks.loop(minutes=30)
async def check_boosters_loop() -> None:
    if not has_bot():
        return
    bot = get_bot()

    for guild in bot.guilds:
        try:
            config = await config_col.find_one({"guild": str(guild.id)})
            if not config:
                continue
            boost_channel_id = config.get("boost_channel")
            if not boost_channel_id:
                continue
            channel = guild.get_channel(boost_channel_id)
            if not channel:
                continue
            boost_message = config.get("boost_message")
            if not boost_message:
                continue

            for member in guild.members:
                if not member.premium_since:
                    continue
                boost_key = f"{guild.id}-{member.id}"
                boost_record = await boost_col.find_one({"_id": boost_key})

                if (
                    boost_record
                    and boost_record.get("last_thanked")
                    == member.premium_since.isoformat()
                ):
                    continue

                msg_content = (
                    boost_message.replace("{username}", member.name)
                    .replace("{mention}", member.mention)
                    .replace("{server}", guild.name)
                    .replace(
                        "{boostcount}",
                        str(guild.premium_subscription_count or 0),
                    )
                )

                embed = discord.Embed(
                    description=msg_content,
                    color=discord.Color.fuchsia(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_author(
                    name="Boost Alert!",
                    icon_url=member.display_avatar.url,
                )
                embed.set_thumbnail(url=member.display_avatar.url)

                try:
                    sent_message = await channel.send(embed=embed)
                    emoji = config.get("boost_react_emoji")
                    if emoji:
                        try:
                            await sent_message.add_reaction(emoji)
                        except Exception:
                            pass

                    await boost_col.update_one(
                        {"_id": boost_key},
                        {
                            "$set": {
                                "last_thanked": member.premium_since.isoformat()
                            }
                        },
                        upsert=True,
                    )
                except Exception as exc:
                    print(
                        "⚠️ Error sending periodic boost message in "
                        f"{guild.name} for {member}: {exc}"
                    )

        except Exception as exc:
            print(f"⚠️ Error in check_boosters_loop for guild {guild.id}: {exc}")


@check_boosters_loop.before_loop
async def _wait() -> None:
    if not has_bot():
        return
    await get_bot().wait_until_ready()


__all__ = ["check_boosters_loop"]
