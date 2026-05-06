"""``/afk`` command and the relevant on_message handlers (handled in events)."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import commands

from bot.database import afk_col


class AfkCog(commands.Cog, name="AFK"):
    """Set AFK status; the actual welcome-back logic lives in messages.py."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="afk", description="Set your AFK status."
    )
    async def afk(
        self, ctx: commands.Context, *, reason: str = "AFK"
    ) -> None:
        afk_key = f"{ctx.guild.id}-{ctx.author.id}"

        await afk_col.update_one(
            {"_id": afk_key},
            {
                "$set": {
                    "reason": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "original_nick": ctx.author.nick,
                }
            },
            upsert=True,
        )

        if ctx.author.display_name.startswith("[AFK]"):
            await ctx.send(f"🛌 You are now AFK: {reason}", delete_after=7)
            return

        try:
            new_nick = f"[AFK] {ctx.author.display_name}"
            await ctx.author.edit(nick=new_nick)
        except discord.Forbidden:
            await ctx.send(
                "⚠️ I can't change your nickname (role hierarchy or missing "
                "permissions). AFK still set!",
                delete_after=5,
            )
        except discord.HTTPException:
            await ctx.send(
                "⚠️ Something went wrong while changing your nickname. AFK still set!"
            )

        await ctx.send(f"🛌 You are now AFK: {reason}", delete_after=7)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AfkCog(bot))
