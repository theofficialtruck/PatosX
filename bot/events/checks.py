"""Global ``commands.check`` predicates registered on the bot.

These run for *every* command invocation. They live in events because they
are bot-wide hooks rather than per-command decorators.
"""

from __future__ import annotations

from discord.ext import commands

from bot.database import disabled_col


class GlobalChecksCog(commands.Cog, name="GlobalChecks"):
    """Bot-wide checks (lock, guild context, disabled commands)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # ``commands.Bot.add_check`` registers a function as a global check.
        bot.add_check(self.global_lock_check)
        bot.add_check(self.ensure_guild_context)
        bot.add_check(self.check_disabled)

    async def global_lock_check(self, ctx: commands.Context) -> bool:
        # `override` is exempt so the lock is recoverable.
        if ctx.command is None or ctx.command.name == "override":
            return True
        if ctx.guild is None:
            return True
        if self.bot.bot_locks.get(str(ctx.guild.id)):
            await ctx.send(
                "🔒 The bot is locked - only `override` by theofficialtruck works."
            )
            return False
        return True

    async def ensure_guild_context(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            await ctx.send("❌ This bot can only be used in a server, not in DMs.")
            return False
        return True

    async def check_disabled(self, ctx: commands.Context) -> bool:
        if not ctx.guild:
            return True
        doc = await disabled_col.find_one({"guild": str(ctx.guild.id)})
        if not doc:
            return True
        if ctx.command and ctx.command.name in doc.get("disabled_commands", []):
            return False
        category = ctx.command.cog_name.lower() if ctx.command and ctx.command.cog_name else None
        if category and category in doc.get("disabled_categories", []):
            return False
        return True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GlobalChecksCog(bot))
