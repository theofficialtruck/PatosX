"""``/duckquiz`` command."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone

from discord.ext import commands

from bot.config.constants import NUM_Q
from bot.database import config_col, quiz_col
from bot.utils.checks import blacklist_barrier
from bot.utils.errors import send_hybrid_error
from bot.views.quiz import QuizView
from duckquiz_questions import questions


class QuizCog(commands.Cog, name="Quiz"):
    """Run the standardized DuckQuiz."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="duckquiz", description="Standardized Duck Quiz."
    )
    @blacklist_barrier()
    async def duckquiz(self, ctx: commands.Context) -> None:
        cfg_raw = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}
        if isinstance(cfg_raw, str):
            try:
                cfg_raw = json.loads(cfg_raw)
            except Exception:
                cfg_raw = {}
        if not isinstance(cfg_raw, dict):
            cfg_raw = {}

        quiz_channels = cfg_raw.get("QUIZ_CHANNEL")
        if isinstance(quiz_channels, str) and quiz_channels.isdigit():
            quiz_channels = [int(quiz_channels)]
        elif isinstance(quiz_channels, list):
            quiz_channels = [int(x) for x in quiz_channels if str(x).isdigit()]
        else:
            quiz_channels = []

        if quiz_channels and ctx.channel.id not in quiz_channels:
            mention = (
                f"<#{quiz_channels[0]}>" if quiz_channels else "`a quiz channel`"
            )
            return await ctx.send(f"❌ Please use this command in {mention}.")

        user_key, guild_key = str(ctx.author.id), str(ctx.guild.id)
        now = datetime.now(timezone.utc)

        role_ids = cfg_raw.get("ROLE_ID", [])
        if isinstance(role_ids, int):
            role_ids = [role_ids]
        elif isinstance(role_ids, str) and role_ids.isdigit():
            role_ids = [int(role_ids)]

        user_roles = [r.id for r in ctx.author.roles]
        if any(rid in user_roles for rid in role_ids):
            await ctx.send("ℹ You’ve already passed; type `yes` within 30s to retake.")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    timeout=30,
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                )
                if msg.content.strip().lower() != "yes":
                    return await ctx.send("✅ Quiz cancelled.")
            except asyncio.TimeoutError:
                return await ctx.send("⌛ Timed out - quiz cancelled.")

        user_doc = await quiz_col.find_one(
            {"guild": guild_key, "user": user_key}
        )
        last_use = user_doc.get("last_quiz") if user_doc else None
        if last_use:
            last_dt = datetime.fromisoformat(last_use).replace(tzinfo=timezone.utc)
            if now - last_dt < timedelta(hours=1):
                remaining = timedelta(hours=1) - (now - last_dt)
                mins = int(remaining.total_seconds() // 60)
                return await ctx.send(
                    f"🕒 You can take the quiz again in {mins} minute(s)."
                )

        used = await quiz_col.distinct(
            "qid", {"guild": guild_key, "used": True}
        )
        pool = [
            q
            for q in questions
            if isinstance(q.get("id"), (int, str)) and q["id"] not in used
        ]
        if len(pool) < NUM_Q:
            await quiz_col.update_many(
                {"guild": guild_key}, {"$unset": {"used": ""}}
            )
            pool = [
                q for q in questions if isinstance(q.get("id"), (int, str))
            ]

        selected = random.sample(pool, NUM_Q)

        quiz_doc = {
            "guild": guild_key,
            "user": user_key,
            "started": now,
            "questions": [q["id"] for q in selected],
            "answers": {},
            "score": 0,
            "completed": None,
            "passed": False,
        }
        result = await quiz_col.insert_one(quiz_doc)

        await quiz_col.update_one(
            {"guild": guild_key, "user": user_key},
            {"$set": {"last_quiz": now.isoformat()}},
            upsert=True,
        )

        view = QuizView(ctx, result.inserted_id, selected)
        await view.show_next()

    @duckquiz.error
    async def duckquiz_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            mins = int(error.retry_after // 60)
            await send_hybrid_error(
                ctx,
                content=(
                    f"🕒 Please wait another **{mins} minute(s)** "
                    "before taking the quiz again."
                ),
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await send_hybrid_error(
                ctx,
                content=(
                    "❌ Missing arguments, type the quiz command without "
                    "additional input (no parameters required)."
                ),
            )
        elif isinstance(error, commands.CheckFailure):
            await send_hybrid_error(
                ctx, content="❌ You can't use this command right now."
            )
        else:
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ An unexpected error occurred, please contact thetruck: "
                    f"`{type(error).__name__} - {error}`"
                ),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuizCog(bot))
