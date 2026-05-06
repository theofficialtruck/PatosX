"""Invite tracking commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import invite_config_col, invites_col
from bot.utils.checks import blacklist_barrier, staff_only, staffperm
from bot.utils.invites_cache import get_guild_invites


class InvitesCog(commands.Cog, name="Invites"):
    """Configure the invite log channel and surface stats."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="invitechannel",
        description="Set the channel where invite joins are announced.",
    )
    @staffperm("invites")
    @staff_only()
    async def invitechannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        await invite_config_col.update_one(
            {"guild_id": str(ctx.guild.id)},
            {"$set": {"channel_id": str(channel.id)}},
            upsert=True,
        )
        await ctx.send(f"✅ Invite announcements will now be sent in {channel.mention}.")

    @commands.hybrid_command(
        name="invites",
        description="Check how many invites a user has.",
    )
    @app_commands.describe(
        member="The user to check (optional - shows your invites if not provided)"
    )
    @staffperm("invites")
    @blacklist_barrier()
    async def invites(
        self, ctx: commands.Context, member: discord.Member | None = None
    ) -> None:
        member = member or ctx.author
        stats = (
            await invites_col.find_one(
                {"guild_id": str(ctx.guild.id), "user_id": str(member.id)}
            )
            or {}
        )
        regular = stats.get("regular", 0)
        fake = stats.get("fake", 0)
        leaves = stats.get("leaves", stats.get("left", 0))
        total_display = regular + leaves

        embed = discord.Embed(
            title=f"📨 Invite Stats for {member.display_name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="✨ Total Invites", value=total_display, inline=False)
        embed.add_field(name="✅ Regular", value=regular, inline=True)
        embed.add_field(name="❌ Leaves", value=leaves, inline=True)
        embed.add_field(name="⚠️ Fake", value=fake, inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="removeinvites",
        aliases=["delinvites"],
        description="Remove a certain number of invites from a user.",
    )
    @app_commands.describe(
        member="The user to remove invites from",
        amount="Number of invites to remove",
    )
    @staffperm("invites")
    @staff_only()
    async def removeinvites(
        self, ctx: commands.Context, member: discord.Member, amount: int
    ) -> None:
        if amount <= 0:
            return await ctx.send(
                "❌ Please provide a **positive number** of invites to remove."
            )

        guild_id = str(ctx.guild.id)
        user_id = str(member.id)

        stats = await invites_col.find_one(
            {"guild_id": guild_id, "user_id": user_id}
        )
        if not stats:
            return await ctx.send(
                f"❌ {member.mention} has no invite records."
            )

        total = stats.get("total", 0)
        regular = stats.get("regular", 0)
        fake = stats.get("fake", 0)
        leaves = stats.get("leaves", stats.get("left", 0))

        if total <= 0:
            return await ctx.send(
                f"❌ {member.mention} already has **0 invites**."
            )

        to_remove = amount
        if regular > 0:
            removed = min(regular, to_remove)
            regular -= removed
            to_remove -= removed
        if to_remove > 0 and fake > 0:
            removed = min(fake, to_remove)
            fake -= removed
            to_remove -= removed
        if to_remove > 0 and leaves > 0:
            removed = min(leaves, to_remove)
            leaves -= removed
            to_remove -= removed

        new_total = max(regular - leaves, 0)

        await invites_col.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {
                "$set": {
                    "regular": regular,
                    "fake": fake,
                    "leaves": leaves,
                    "total": new_total,
                }
            },
        )
        await ctx.send(
            f"✅ Removed **{amount} invites** from {member.mention}. "
            f"New total: **{new_total}**"
        )

    @commands.hybrid_command(
        name="inviteleaderboard",
        aliases=["invitelb"],
        description="Show the top inviters in the server.",
    )
    @blacklist_barrier()
    async def inviteleaderboard(
        self, ctx: commands.Context, limit: int = 10
    ) -> None:
        guild_id = str(ctx.guild.id)
        totals: dict[str, int] = {}

        async for code_doc in invites_col.find(
            {"guild_id": guild_id, "inviter_id": {"$ne": None}}
        ):
            inviter_id = code_doc.get("inviter_id")
            if not inviter_id:
                continue
            try:
                totals[inviter_id] = totals.get(inviter_id, 0) + int(
                    code_doc.get("uses", 0)
                )
            except Exception:
                pass

        if not totals:
            return await ctx.send("❌ No invite data found yet.")

        leaves_map: dict[str, int] = {}
        async for stats_doc in invites_col.find(
            {"guild_id": guild_id, "user_id": {"$in": list(totals.keys())}}
        ):
            inviter = stats_doc.get("user_id")
            leaves_map[inviter] = stats_doc.get(
                "leaves", stats_doc.get("left", 0)
            )

        sorted_inv = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        embed = discord.Embed(
            title=f"🏆 Top {limit} Inviters in {ctx.guild.name}",
            color=discord.Color.gold(),
        )

        rank = 1
        for inviter_id, joins in sorted_inv:
            uid_int = int(inviter_id)
            member = ctx.guild.get_member(uid_int)
            if member:
                username = member.display_name
            else:
                fetched = await ctx.bot.fetch_user(uid_int)
                username = (
                    getattr(fetched, "name", f"Unknown ({uid_int})")
                    if fetched
                    else f"Unknown ({uid_int})"
                )
            leaves = leaves_map.get(inviter_id, 0)
            total = max(joins - leaves, 0)
            embed.add_field(
                name=f"#{rank} {username}",
                value=f"✅ {joins} joins | ❌ {leaves} leaves → **{total} net**",
                inline=False,
            )
            rank += 1
        await ctx.send(embed=embed)

    @commands.command()
    @staffperm("invites")
    @staff_only()
    async def resetinvites(self, ctx: commands.Context) -> None:
        guild_id = str(ctx.guild.id)
        stats_res = await invites_col.delete_many(
            {"guild_id": guild_id, "user_id": {"$exists": True}}
        )
        upd_res = await invites_col.update_many(
            {"guild_id": guild_id, "code": {"$exists": True}},
            {"$set": {"joined_users": []}},
        )

        try:
            current_invites = await get_guild_invites(ctx.guild)
            for invite in current_invites:
                await invites_col.update_one(
                    {"guild_id": guild_id, "code": invite.code},
                    {
                        "$set": {
                            "inviter_id": (
                                str(invite.inviter.id) if invite.inviter else None
                            ),
                            "uses": invite.uses,
                        }
                    },
                    upsert=True,
                )
        except Exception:
            pass

        await ctx.send(
            f"✅ Reset invites for this server.\n"
            f"Cleared {stats_res.deleted_count} inviter records and refreshed "
            f"{upd_res.modified_count} invite codes."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InvitesCog(bot))
