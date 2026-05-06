"""Vanity-role commands and the presence-update auto-grant logic."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from pytz import UTC

from bot.database import vanity_col
from bot.utils.checks import staff_only, staffperm
from bot.utils.stickies import repost_sticky_note
from bot.views.promoters import PromotersView


class VanityCog(commands.Cog, name="Vanity"):
    """Track members displaying a configured keyword in their status."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def cog_unload(self) -> None:
        if self.check_all_statuses.is_running():
            self.check_all_statuses.cancel()

    @commands.hybrid_command(
        name="vanityroles",
        description="Track users with keyword in status. Staff-only.",
    )
    @app_commands.describe(
        role="Role to assign",
        log_channel="Channel to log changes",
        keyword="Keyword to track in status",
    )
    @staffperm("vanity")
    @staff_only()
    async def vanityroles(
        self,
        ctx: commands.Context,
        role: discord.Role,
        log_channel: discord.TextChannel,
        keyword: str,
    ) -> None:
        await vanity_col.update_one(
            {"guild": str(ctx.guild.id)},
            {
                "$set": {
                    "role": role.id,
                    "log": log_channel.id,
                    "keyword": keyword,
                    "users": [],
                }
            },
            upsert=True,
        )
        await ctx.send(f"✅ Vanity role set for '{keyword}' → {role.mention}")

    @commands.hybrid_command(
        name="promoters",
        description="View users with the vanity role. Staff-only.",
    )
    @staffperm("vanity")
    @staff_only()
    async def promoters(self, ctx: commands.Context) -> None:
        data = await vanity_col.find_one({"guild": str(ctx.guild.id)})
        users = data.get("users", []) if data else []
        mentions: list[str] = []
        for uid in users:
            member = ctx.guild.get_member(uid)
            if member:
                mentions.append(member.mention)

        view = PromotersView(ctx, mentions)
        msg = await ctx.send(embed=view.make_embed(), view=view)
        view.message = msg

    @commands.hybrid_command(
        name="resetpromoters",
        description="Clear all users from the vanity role. Staff-only.",
    )
    @staffperm("vanity")
    @staff_only()
    async def resetpromoters(self, ctx: commands.Context) -> None:
        guild = str(ctx.guild.id)
        data = await vanity_col.find_one({"guild": guild})
        if not data:
            return await ctx.send("❌ No vanity config set.")

        await ctx.send(
            "⚠️ Type exactly:\n`I confirm I want to reset all the promoters.`"
        )
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30,
            )
        except asyncio.TimeoutError:
            return await ctx.send("❌ Timeout - cancelled.")

        if msg.content.strip() != "I confirm I want to reset all the promoters.":
            return await ctx.send("❌ Confirmation failed - cancelled.")

        role = ctx.guild.get_role(data["role"])
        removed = 0
        for uid in data["users"]:
            member = ctx.guild.get_member(uid)
            if member and role in member.roles:
                await member.remove_roles(role, reason="reset promoters")
                removed += 1

        await vanity_col.update_one({"guild": guild}, {"$set": {"users": []}})
        await ctx.send(
            embed=discord.Embed(
                title="🔁 Promoters Reset",
                description=f"{removed} users removed. List cleared.",
                color=discord.Color.red(),
            )
        )

    # ------------------------------------------------------------------
    # Listeners and background loop
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_presence_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if not self.check_all_statuses.is_running():
            self.check_all_statuses.start()
        if after.bot or not after.guild:
            return
        if after.status == discord.Status.offline:
            return

        data = await vanity_col.find_one({"guild": str(after.guild.id)})
        if not data:
            return

        keyword = data["keyword"].lower()
        status = (
            before.activity.name.lower()
            if before.activity and before.activity.name
            else ""
        )
        new_status = (
            after.activity.name.lower()
            if after.activity and after.activity.name
            else ""
        )
        role = after.guild.get_role(data["role"])
        log_ch = after.guild.get_channel(data["log"])
        has_role = role in after.roles if role else False

        if keyword not in status and keyword in new_status and not has_role and role:
            await after.add_roles(role, reason="vanity match")
            await vanity_col.update_one(
                {"guild": str(after.guild.id)},
                {"$addToSet": {"users": after.id}},
            )
            if log_ch:
                await log_ch.send(
                    embed=discord.Embed(
                        title="Vanity Added ✨",
                        description=(
                            f"{after.mention} has been awarded **{role.name}** "
                            f"for proudly displaying our vanity `{keyword}` "
                            "in their status!"
                        ),
                        color=discord.Color.magenta(),
                        timestamp=datetime.now(timezone.utc),
                    ).set_thumbnail(url=after.display_avatar.url)
                )

        elif keyword in status and keyword not in new_status and has_role and role:
            await after.remove_roles(role, reason="vanity lost")
            await vanity_col.update_one(
                {"guild": str(after.guild.id)},
                {"$pull": {"users": after.id}},
            )
            if log_ch:
                await log_ch.send(
                    embed=discord.Embed(
                        title="Vanity Removed",
                        description=(
                            f"{after.mention} has lost **{role.name}** for no "
                            f"longer displaying our vanity `{keyword}`."
                        ),
                        color=discord.Color.light_gray(),
                        timestamp=datetime.now(timezone.utc),
                    ).set_thumbnail(url=after.display_avatar.url)
                )

    @tasks.loop(seconds=0.01)
    async def check_all_statuses(self) -> None:
        for guild in self.bot.guilds:
            data = await vanity_col.find_one({"guild": str(guild.id)})
            if not data:
                continue

            keyword = data["keyword"].lower()
            role = guild.get_role(data["role"])
            log_ch = guild.get_channel(data["log"])
            if not role:
                continue

            for member in guild.members:
                if member.bot or member.status == discord.Status.offline:
                    continue

                status = (
                    member.activity.name.lower()
                    if member.activity and member.activity.name
                    else ""
                )
                has_role = role in member.roles

                if keyword in status and not has_role:
                    await member.add_roles(role, reason="Vanity match (auto-check)")
                    await vanity_col.update_one(
                        {"guild": str(guild.id)},
                        {"$addToSet": {"users": member.id}},
                    )
                    if log_ch:
                        await log_ch.send(
                            embed=discord.Embed(
                                title="Vanity Added ✨",
                                description=(
                                    f"{member.mention} has been awarded "
                                    f"**{role.name}**\nFor displaying "
                                    f"`{keyword}` in their status!"
                                ),
                                color=discord.Color.magenta(),
                                timestamp=datetime.now(UTC),
                            ).set_thumbnail(url=member.display_avatar.url)
                        )
                        await repost_sticky_note(log_ch.id, guild.id)

                elif keyword not in status and has_role:
                    await member.remove_roles(role, reason="Vanity removed (auto-check)")
                    await vanity_col.update_one(
                        {"guild": str(guild.id)},
                        {"$pull": {"users": member.id}},
                    )
                    if log_ch:
                        await log_ch.send(
                            embed=discord.Embed(
                                title="Vanity Removed",
                                description=(
                                    f"{member.mention} lost **{role.name}** "
                                    f"for no longer displaying `{keyword}` "
                                    "in their status."
                                ),
                                color=discord.Color.light_gray(),
                                timestamp=datetime.now(UTC),
                            ).set_thumbnail(url=member.display_avatar.url)
                        )
                        await repost_sticky_note(log_ch.id, guild.id)

    @check_all_statuses.before_loop
    async def _before_check_all_statuses(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VanityCog(bot))
