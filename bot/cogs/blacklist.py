"""``/blacklist`` and ``/whitelist`` commands."""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database import settings_col
from bot.utils.checks import staff_only, staffperm
from bot.utils.errors import send_hybrid_error
from bot.utils.logging import log_action
from bot.utils.permissions import get_or_create_blacklist_role


class BlacklistCog(commands.Cog, name="Blacklist"):
    """Toggle the blacklist role on a member."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="blacklist",
        description="Blacklist a user from bot commands. Staff-only.",
    )
    @staffperm("other_moderation")
    @staff_only()
    async def blacklist(self, ctx: commands.Context, member: discord.Member) -> None:
        guild_id = str(ctx.guild.id)
        settings = await settings_col.find_one({"guild": guild_id})
        if not settings:
            settings = {"guild": guild_id}
            await settings_col.insert_one(settings)

        role = await get_or_create_blacklist_role(ctx.guild, settings)

        try:
            await member.add_roles(role, reason=f"Blacklisted by {ctx.author}")
            await log_action(
                ctx,
                f"{member.mention} has been blacklisted from using bot commands.",
                user_id=member.id,
                action_type="Blacklist",
            )
            await ctx.send(
                f"🚫 {member.mention} has been blacklisted from using bot commands."
            )
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to add that role.")
        except Exception as exc:
            await ctx.send(f"❌ Failed to add blacklist role: {exc}")

    @commands.hybrid_command(
        name="whitelist",
        description="Remove a user from the blacklist. Staff-only.",
    )
    @staffperm("other_moderation")
    @staff_only()
    async def whitelist(self, ctx: commands.Context, member: discord.Member) -> None:
        guild_id = str(ctx.guild.id)
        settings = await settings_col.find_one({"guild": guild_id})
        if not settings:
            return await ctx.send("⚠️ No settings found for this server.")

        role = await get_or_create_blacklist_role(ctx.guild, settings)

        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"Unblacklisted by {ctx.author}")
            await log_action(
                ctx,
                f"{member.mention} has been removed from the blacklist.",
                user_id=member.id,
                action_type="Whitelist",
            )
            await ctx.send(f"✅ {member.mention} has been whitelisted.")
        except discord.Forbidden:
            await ctx.send("❌ I don’t have permission to remove that role.")
        except Exception as exc:
            await ctx.send(f"❌ Failed to remove blacklist role: {exc}")

    @blacklist.error
    async def blacklist_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_hybrid_error(
                ctx,
                content="❌ You need **Manage Roles** permission to use this command.",
            )
        elif isinstance(error, commands.BadArgument):
            await send_hybrid_error(ctx, content="❌ Invalid user specified.")
        else:
            await send_hybrid_error(ctx, content=f"⚠️ An error occurred: {error}")

    @whitelist.error
    async def whitelist_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_hybrid_error(
                ctx,
                content="❌ You need **Manage Roles** permission to use this command.",
            )
        elif isinstance(error, commands.BadArgument):
            await send_hybrid_error(ctx, content="❌ Invalid user specified.")
        else:
            await send_hybrid_error(ctx, content=f"⚠️ An error occurred: {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BlacklistCog(bot))
