"""``/poll`` command (slash uses a modal, prefix uses a wizard)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from bot.database import polls_col
from bot.utils.time_parsing import parse_time
from bot.views.polls import PollModal, PollView, build_poll_embed


class PollsCog(commands.Cog, name="Polls"):
    """Create polls; auto-close handled by the background task."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="poll", description="Create a poll")
    async def poll(self, ctx: commands.Context) -> None:
        # Slash invocations open a modal; prefix invocations use a wizard.
        if ctx.interaction is not None:
            await ctx.interaction.response.send_modal(PollModal())
            return

        def check(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        try:
            await ctx.send("📝 Let's create a poll! Type `cancel` anytime to stop.")

            await ctx.send("❓ What is the question?")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            question = msg.content

            options: list[str | None] = []
            await ctx.send("➡️ Enter option 1:")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            options.append(msg.content)

            await ctx.send("➡️ Enter option 2:")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            options.append(msg.content)

            for i in range(3, 6):
                await ctx.send(f"➡️ Enter option {i} (or type `skip` to leave blank):")
                msg = await self.bot.wait_for("message", check=check, timeout=300)
                if msg.content.lower() == "cancel":
                    return await ctx.send("❌ Poll creation cancelled.")
                if msg.content.lower() == "skip":
                    options.append(None)
                    continue
                options.append(msg.content)

            await ctx.send("📺 Mention the channel to post in (e.g., #general):")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            if not msg.channel_mentions:
                return await ctx.send("⚠️ Invalid channel mention. Cancelled.")
            channel = msg.channel_mentions[0]

            await ctx.send("⏳ Enter poll duration (e.g., `1h`, `30m`, `2d`):")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            try:
                duration_seconds = parse_time(msg.content)
            except Exception as exc:
                return await ctx.send(f"⚠️ Invalid duration format. {exc}")

            end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            final_options = [opt for opt in options if opt]

            poll_id = f"{ctx.guild.id}-{ctx.message.id}"
            counts: dict[str, int] = {}
            view = PollView(poll_id, final_options)
            embed = build_poll_embed(
                question, final_options, counts,
                closed=False, duration=msg.content,
            )

            poll_msg = await channel.send(embed=embed, view=view)

            await polls_col.insert_one(
                {
                    "poll_id": poll_id,
                    "question": question,
                    "options": final_options,
                    "votes": {},
                    "channel_id": str(channel.id),
                    "message_id": str(poll_msg.id),
                    "end_time": end_time,
                    "duration_raw": msg.content,
                }
            )
            await ctx.send("✅ Poll created successfully!")

        except asyncio.TimeoutError:
            await ctx.send("⌛ Poll creation timed out due to inactivity.")
        except Exception as exc:
            await ctx.send(f"⚠️ Error: {exc}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PollsCog(bot))
