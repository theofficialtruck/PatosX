"""``/modview`` and ``/performance`` — staff inspection commands."""

from __future__ import annotations

from datetime import datetime

import discord
from discord import VerificationLevel
from discord.ext import commands

from bot.config.constants import DISCORD_SERVICE_UNAVAILABLE_MESSAGE
from bot.database import settings_col
from bot.utils.checks import staff_only, staffperm
from bot.utils.errors import (
    is_discord_service_unavailable_error,
    send_hybrid_error,
)
from bot.utils.moderation import (
    fetch_punishments,
    format_activity,
    format_flags,
    format_permissions,
    format_roles,
)
from bot.views.moderation import ModViewButtons
from bot.views.performance import PerformanceView


VERIFICATION_NAMES = {
    VerificationLevel.none: "None",
    VerificationLevel.low: "Low",
    VerificationLevel.medium: "Medium",
    VerificationLevel.high: "High",
}


class ModViewCog(commands.Cog, name="ModView"):
    """Staff utilities for reviewing members and analytics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(
        name="performance",
        description="View staff performance analytics. Staff-only.",
    )
    @staffperm("other_moderation")
    @staff_only()
    async def performance(
        self, ctx: commands.Context, days: int = 30
    ) -> None:
        try:
            settings = await settings_col.find_one(
                {"guild": str(ctx.guild.id)}
            )
            staff_role_id = settings["staff_role"] if settings and "staff_role" in settings else None
            if not staff_role_id:
                return await ctx.send(
                    "❌ No staff role configured. Use `.staffset` to configure one."
                )

            if days < 1 or days > 365:
                return await ctx.send(
                    "❌ Review period must be between 1 and 365 days."
                )

            staff_role = ctx.guild.get_role(staff_role_id)
            if not staff_role:
                return await ctx.send("❌ Staff role not found.")

            staff_members = [
                member for member in ctx.guild.members if staff_role in member.roles
            ]
            if not staff_members:
                return await ctx.send("❌ No staff members found.")

            embed = discord.Embed(
                title="📊 Staff Performance Review",
                description=(
                    "Select a staff member from the dropdown below to view their "
                    "performance analytics.\n\n"
                    f"**Total Staff Members:** {len(staff_members)}\n"
                    f"**Review Period:** Last {days} days"
                ),
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="📊 Available Analytics",
                value=(
                    "• Total moderation actions\n"
                    "• Punishment breakdown\n"
                    "• Activity patterns\n"
                    "• Efficiency rating"
                ),
                inline=False,
            )
            embed.set_footer(
                text="Review period can be adjusted with .performance <days> (1-365)"
            )

            view = PerformanceView(ctx.guild.id, staff_members, days)
            await ctx.send(embed=embed, view=view)

        except Exception as exc:
            await ctx.send(
                f"❌ An error occurred: `{type(exc).__name__}: {exc}`"
            )

    @commands.hybrid_command(
        name="modview",
        description="Open moderator view for a user. Staff-only.",
    )
    @staffperm("other_moderation")
    @staff_only()
    async def modview(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        punishments = await fetch_punishments(ctx.guild.id, member.id)
        mod_perms = format_permissions(member)
        roles = format_roles(member)
        flags = format_flags(member)
        activity = format_activity(member)  # noqa: F841 - kept for parity

        nick = member.nick or "None"
        pending = "✅ Yes" if member.pending else "❌ No"
        bot_flag = "🤖 Yes" if member.bot else "👤 No"
        top_role = member.top_role.mention
        status = str(member.status).title()
        joined_discord = f"<t:{int(member.created_at.timestamp())}:F>"
        joined_server = (
            f"<t:{int(member.joined_at.timestamp())}:F>"
            if member.joined_at
            else "Unknown"
        )

        verification_name = VERIFICATION_NAMES.get(
            ctx.guild.verification_level,
            str(ctx.guild.verification_level).title(),
        )

        embed = discord.Embed(
            title=f"🛠️ Moderator View: {member}",
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="👤 Username",
            value=f"{member} (`{member.name}`)",
            inline=False,
        )
        embed.add_field(name="🪪 Nickname", value=nick, inline=True)
        embed.add_field(name="🤖 Bot Account", value=bot_flag, inline=True)
        embed.add_field(name="📶 Status", value=status, inline=True)
        embed.add_field(name="🧩 Top Role", value=top_role, inline=True)
        embed.add_field(name="🎭 Roles", value=roles, inline=False)
        embed.add_field(name="🕐 Joined Discord", value=joined_discord, inline=True)
        embed.add_field(name="🏠 Joined Server", value=joined_server, inline=True)
        embed.add_field(name="🧾 Pending Verification", value=pending, inline=True)
        embed.add_field(
            name="🔒 Guild Verification Level",
            value=verification_name,
            inline=False,
        )
        embed.add_field(name="🎖️ Badges / Flags", value=flags, inline=False)
        embed.add_field(
            name="⚙️ Effective Permissions", value=mod_perms, inline=False
        )
        embed.add_field(name="📜 Past Punishments", value=punishments, inline=False)

        msg = await ctx.send(embed=embed)
        view = ModViewButtons(self.bot, ctx, member, msg)
        await msg.edit(view=view)

    @modview.error
    async def modview_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_hybrid_error(
                ctx,
                content="❌ You don't have the required permissions to use this command.",
            )
        elif isinstance(error, commands.CheckFailure):
            await send_hybrid_error(
                ctx, content="❌ This command is restricted to staff members only."
            )
        elif isinstance(error, commands.BadArgument):
            await send_hybrid_error(
                ctx,
                content="❌ Invalid member provided. Please mention a valid user.",
            )
        elif isinstance(error, commands.MemberNotFound):
            await send_hybrid_error(
                ctx, content="❌ Could not find that member in this server."
            )
        elif is_discord_service_unavailable_error(error):
            await send_hybrid_error(
                ctx, content=DISCORD_SERVICE_UNAVAILABLE_MESSAGE
            )
        elif isinstance(error, commands.CommandInvokeError):
            await send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please try again later."
            )
        else:
            await send_hybrid_error(
                ctx, content="⚠️ An error occurred. Please try again later."
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModViewCog(bot))
