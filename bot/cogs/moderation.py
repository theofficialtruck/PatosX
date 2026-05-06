"""Moderation commands: kick, ban, unban, mute, unmute, warn, etc."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import mod_col, mutes_col
from bot.utils.checks import blacklist_barrier, staff_only, staffperm
from bot.utils.errors import send_hybrid_error
from bot.utils.logging import log_action
from bot.utils.permissions import check_target_permission
from bot.views.moderation import ModerationConfirmView


class ModerationCog(commands.Cog, name="Moderation"):
    """Standard moderation commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="kick", description="Kick a member. Staff-only.")
    @staffperm("kick")
    @staff_only()
    async def kick(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided",
    ) -> None:
        err = check_target_permission(ctx, member)
        if err:
            return await ctx.send(err)

        embed = discord.Embed(
            title="⚠️ Confirm Kick",
            description=f"Are you sure you want to kick {member.mention}?",
            color=discord.Color.orange(),
        )
        embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="This action will be logged.")

        confirm_view = ModerationConfirmView("kick", member, reason, ctx=ctx)
        await ctx.send(embed=embed, view=confirm_view)

    @commands.command(name="ban", description="Ban a member. Staff-only.")
    @staffperm("ban")
    @staff_only()
    async def ban(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided",
    ) -> None:
        err = check_target_permission(ctx, member)
        if err:
            return await ctx.send(err)

        embed = discord.Embed(
            title="⚠️ Confirm Ban",
            description=f"Are you sure you want to ban {member.mention}?",
            color=discord.Color.orange(),
        )
        embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="This action will be logged.")

        confirm_view = ModerationConfirmView("ban", member, reason, ctx=ctx)
        await ctx.send(embed=embed, view=confirm_view)

    @commands.hybrid_command(
        name="unban", description="Unban a member. Staff-only."
    )
    @staffperm("ban")
    @staff_only()
    async def unban(self, ctx: commands.Context, *, user_id: int) -> None:
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.send(f"✅ {user.mention} has been unbanned.")
            await log_action(
                ctx, f"Unbanned {user}", user_id=user.id, action_type="unban"
            )
        except Exception:
            await ctx.send("❌ Failed to unban that user.")

    @commands.command(name="mute", description="Mute a member temporarily. Staff-only.")
    @staffperm("mute")
    @staff_only()
    async def mute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str | None = None,
        *,
        reason: str = "No reason provided",
    ) -> None:
        err = check_target_permission(ctx, member)
        if err:
            return await ctx.send(err)

        embed = discord.Embed(
            title="⚠️ Confirm Mute",
            description=f"Are you sure you want to mute {member.mention}?",
            color=discord.Color.orange(),
        )
        embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Duration", value=duration or "indefinite", inline=False)
        embed.set_footer(text="This action will be logged.")

        confirm_view = ModerationConfirmView(
            "mute", member, reason, duration, ctx=ctx
        )
        await ctx.send(embed=embed, view=confirm_view)

    @commands.hybrid_command(
        name="unmute", description="Unmute a member. Staff-only."
    )
    @staffperm("mute")
    @staff_only()
    async def unmute(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if mute_role and mute_role in member.roles:
            await member.remove_roles(mute_role, reason="Unmute command used")
            await mutes_col.delete_one(
                {"guild_id": ctx.guild.id, "user_id": member.id}
            )
            await ctx.send(f"✅ {member.mention} has been unmuted.")
            await log_action(
                ctx,
                f"Unmuted {member}",
                user_id=member.id,
                action_type="unmute",
            )
        else:
            await ctx.send("⚠️ That member is not muted.")

    @commands.hybrid_command(
        name="warn", description="Warn a user. Staff-only."
    )
    @app_commands.describe(
        member="The user to warn",
        reason="Reason for the warning (optional)",
    )
    @staffperm("other_moderation")
    @staff_only()
    async def warn(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "No reason provided",
    ) -> None:
        await mod_col.update_one(
            {"guild": str(ctx.guild.id), "user": str(member.id)},
            {
                "$push": {
                    "warnings": {
                        "by": str(ctx.author),
                        "reason": reason,
                        "time": datetime.now(timezone.utc).isoformat(),
                    }
                }
            },
            upsert=True,
        )

        try:
            await member.send(
                f"⚠️ You have been **warned** in **{ctx.guild.name}**\n"
                f"**Reason:** {reason}\n"
                f"**Warned by:** {ctx.author} ({ctx.author.mention})"
            )
        except discord.Forbidden:
            await ctx.send(
                f"⚠️ Could not DM {member.mention} - they might have DMs disabled."
            )

        await ctx.send(f"⚠️ {member.mention} has been warned: {reason}")
        await log_action(
            ctx,
            f"Warned {member} for: {reason}",
            user_id=member.id,
            action_type="warn",
        )

    @commands.hybrid_command(
        name="clearwarns",
        description="Clear all warnings. Staff-only.",
    )
    @staffperm("other_moderation")
    @staff_only()
    async def clearwarns(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        await mod_col.update_one(
            {"guild": str(ctx.guild.id), "user": str(member.id)},
            {"$set": {"warnings": []}},
        )
        await ctx.send(f"✅ All warnings for {member.mention} have been cleared.")
        await log_action(
            ctx,
            f"Cleared warnings for {member}",
            user_id=member.id,
            action_type="clearwarns",
        )

    @commands.hybrid_command(
        name="purge",
        description="Bulk delete messages. Staff-only.",
    )
    @staffperm("other_moderation")
    @staff_only()
    async def purge(
        self,
        ctx: commands.Context,
        count: int,
        member: discord.Member | None = None,
    ) -> None:
        def check(message: discord.Message) -> bool:
            return message.author == member if member else True

        deleted = await ctx.channel.purge(limit=count + 1, check=check)
        await ctx.send(
            f"🧹 Deleted {len(deleted) - 1} messages.", delete_after=5
        )
        await log_action(
            ctx,
            f"Purged {len(deleted) - 1} messages"
            f"{(' from ' + member.display_name) if member else ''}",
            action_type="purge",
        )

    @commands.hybrid_command(
        name="slowmode",
        description="Set slowmode for this channel. Staff-only.",
    )
    @staffperm("other_moderation")
    @staff_only()
    async def slowmode(self, ctx: commands.Context, seconds: int) -> None:
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"✅ Slowmode set to {seconds} seconds.")
        await log_action(
            ctx,
            f"Set slowmode to {seconds}s in #{ctx.channel.name}",
            action_type="slowmode",
        )

    @commands.hybrid_command(
        name="say",
        description="Make the bot say a message in a chosen channel.",
    )
    @staff_only()
    @blacklist_barrier()
    async def say(self, ctx: commands.Context) -> None:
        try:
            await ctx.send(
                "📝 Type the message you want me to say, or type `cancel` to cancel."
            )

            def check(message: discord.Message) -> bool:
                return (
                    message.author.id == ctx.author.id
                    and message.channel.id == ctx.channel.id
                )

            try:
                msg = await self.bot.wait_for("message", timeout=60.0, check=check)
            except Exception:
                return await ctx.send("⌛ Timed out waiting for the message.")

            content = msg.content.strip()
            if content.lower() == "cancel":
                return await ctx.send("❎ Cancelled.")
            if not content:
                return await ctx.send("❌ Message cannot be empty.")
            if len(content) > 2000:
                return await ctx.send(
                    "❌ Message is too long. Please keep it under 2000 characters."
                )

            await ctx.send("📨 Mention the channel (e.g. #general). Type `cancel` to abort.")
            try:
                ch_msg = await self.bot.wait_for("message", timeout=60.0, check=check)
            except Exception:
                return await ctx.send("⌛ Timed out waiting for the channel.")

            ch_text = ch_msg.content.strip()
            if ch_text.lower() == "cancel":
                return await ctx.send("❎ Cancelled.")

            target = None
            if ch_msg.channel_mentions:
                target = ch_msg.channel_mentions[0]
            else:
                try:
                    target = ctx.guild.get_channel(int(ch_text))
                except Exception:
                    target = None

            if not isinstance(target, discord.TextChannel):
                return await ctx.send(
                    "❌ Invalid channel. Mention a text channel or provide a valid channel ID."
                )

            try:
                await target.send(content)
            except discord.Forbidden:
                return await ctx.send(
                    "❌ I do not have permission to send messages in that channel."
                )
            except discord.HTTPException as exc:
                return await ctx.send(
                    f"⚠️ Failed to send the message: {type(exc).__name__}"
                )

            await ctx.send(f"✅ Sent your message to {target.mention}.")
        except Exception as exc:
            await ctx.send(
                f"⚠️ An unexpected error occurred: {type(exc).__name__}"
            )

    @say.error
    async def say_error(self, ctx: commands.Context, error) -> None:
        from bot.config.constants import DISCORD_SERVICE_UNAVAILABLE_MESSAGE
        from bot.utils.errors import is_discord_service_unavailable_error

        try:
            if isinstance(error, commands.CheckFailure):
                return await send_hybrid_error(
                    ctx, content="❌ Only staff members can use this command."
                )
            if is_discord_service_unavailable_error(error):
                return await send_hybrid_error(
                    ctx, content=DISCORD_SERVICE_UNAVAILABLE_MESSAGE
                )
            if isinstance(error, commands.CommandInvokeError):
                return await send_hybrid_error(
                    ctx, content="⚠️ Error running say. Please try again shortly."
                )
            await send_hybrid_error(ctx, content=f"⚠️ Error: {type(error).__name__}")
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
