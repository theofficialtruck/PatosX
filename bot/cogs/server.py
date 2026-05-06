"""``/serverinfo`` and ``/userinfo``."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import mod_col


class ServerCog(commands.Cog, name="Server"):
    """Server- and user-info commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="serverinfo", description="View server information"
    )
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild

        embed = discord.Embed(
            title=f"📜 Server Information - {guild.name}",
            color=discord.Color.blurple(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(
            name="👥 Members", value=f"{guild.member_count:,}", inline=True
        )
        embed.add_field(name="🆔 Server ID", value=guild.id, inline=True)
        embed.add_field(
            name="📅 Created On",
            value=guild.created_at.strftime("%B %d, %Y"),
            inline=False,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="userinfo",
        description="View info about the specified user.",
    )
    @app_commands.describe(
        member="The user to check (optional - shows your info if not provided)"
    )
    async def userinfo(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        member = member or ctx.author
        join = (
            member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "Unknown"
        )
        created = member.created_at.strftime("%Y-%m-%d")
        doc = await mod_col.find_one(
            {"guild": str(ctx.guild.id), "user": str(member.id)}
        )
        warns = len(doc.get("warnings", [])) if doc else 0

        embed = discord.Embed(
            title="User Information", color=discord.Color.blurple()
        )
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        embed.add_field(name="ID", value=member.id)
        embed.add_field(name="Joined Server", value=join)
        embed.add_field(name="Account Created", value=created)
        embed.add_field(name="Warnings", value=warns)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCog(bot))
