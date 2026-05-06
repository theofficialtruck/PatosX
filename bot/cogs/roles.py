"""``/roles``, ``/roleadd`` and ``/roleremove`` claimable-role commands."""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database import roles_col
from bot.utils.checks import staff_only, staffperm
from bot.views.roles import RoleButtons


async def refresh_roles_embed(ctx: commands.Context, guild_id: int) -> None:
    """Re-render the persistent roles message after a role is added/removed."""
    settings = await roles_col.find_one({"_id": guild_id})
    if not settings:
        return
    role_ids = settings.get("roles", [])
    message_id = settings.get("message_id")
    if not message_id:
        return

    try:
        msg = await ctx.channel.fetch_message(message_id)
    except discord.NotFound:
        return

    roles = [ctx.guild.get_role(r) for r in role_ids if ctx.guild.get_role(r)]
    if not roles:
        embed = discord.Embed(
            title="🎭 Claim Your Roles",
            description="⚠️ No valid roles available.",
            color=discord.Color.red(),
        )
        await msg.edit(embed=embed, view=None)
        return

    embed = discord.Embed(
        title="🎭 Claim Your Roles",
        description="\n".join([role.mention for role in roles]),
        color=discord.Color.blurple(),
    )
    view = RoleButtons(role_ids, guild_id, ctx.guild)
    await msg.edit(embed=embed, view=view)


class RolesCog(commands.Cog, name="Roles"):
    """Self-claimable roles."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="roles", description="Show claimable roles")
    async def roles(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        settings = await roles_col.find_one({"_id": guild_id})

        if not settings or not settings.get("roles"):
            return await ctx.send(
                "⚠️ No claimable roles set yet!", ephemeral=True
            )

        role_ids = settings["roles"]
        roles = [
            ctx.guild.get_role(r) for r in role_ids if ctx.guild.get_role(r)
        ]
        if not roles:
            return await ctx.send(
                "⚠️ All stored roles are invalid.", ephemeral=True
            )

        embed = discord.Embed(
            title="🎭 Claim Your Roles",
            description="\n".join([role.mention for role in roles]),
            color=discord.Color.blurple(),
        )
        view = RoleButtons(role_ids, guild_id, ctx.guild)
        msg = await ctx.send(embed=embed, view=view)

        await roles_col.update_one(
            {"_id": guild_id},
            {"$set": {"message_id": msg.id}},
            upsert=True,
        )

    @commands.hybrid_command(name="roleadd", description="Add a claimable role")
    @staffperm("roles")
    @staff_only()
    async def roleadd(
        self, ctx: commands.Context, role: discord.Role
    ) -> None:
        guild_id = ctx.guild.id
        settings = await roles_col.find_one({"_id": guild_id})

        if settings:
            if role.id in settings["roles"]:
                return await ctx.send(
                    "⚠️ That role is already claimable.", ephemeral=True
                )
            await roles_col.update_one(
                {"_id": guild_id}, {"$push": {"roles": role.id}}
            )
        else:
            await roles_col.insert_one({"_id": guild_id, "roles": [role.id]})

        await ctx.send(
            f"✅ {role.mention} has been added as a claimable role!",
            ephemeral=True,
        )
        await refresh_roles_embed(ctx, guild_id)

    @commands.hybrid_command(
        name="roleremove", description="Remove a claimable role"
    )
    @staffperm("roles")
    @staff_only()
    async def roleremove(
        self, ctx: commands.Context, role: discord.Role
    ) -> None:
        guild_id = ctx.guild.id
        settings = await roles_col.find_one({"_id": guild_id})
        if not settings or role.id not in settings.get("roles", []):
            return await ctx.send(
                "⚠️ That role is not claimable.", ephemeral=True
            )

        await roles_col.update_one(
            {"_id": guild_id}, {"$pull": {"roles": role.id}}
        )
        await ctx.send(
            f"❌ {role.mention} has been removed from claimable roles.",
            ephemeral=True,
        )
        await refresh_roles_embed(ctx, guild_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesCog(bot))
