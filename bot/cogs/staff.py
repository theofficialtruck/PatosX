"""Staff role and permissions admin commands."""

from __future__ import annotations

import discord
from discord import AllowedMentions
from discord.ext import commands

from bot.config.constants import STAFF_OVERRIDE_IDS
from bot.database import settings_col, staffperms_col
from bot.utils.checks import staff_only
from bot.views.staff_perms import (
    StaffPermissionView,
    ViewPermsView,
    format_permission_details,
)


class StaffCog(commands.Cog, name="Staff"):
    """Owner/staff configuration commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="staffset", description="Set the staff role. Owner-only."
    )
    async def staffset(self, ctx: commands.Context, role: discord.Role) -> None:
        if ctx.author != ctx.guild.owner:
            return await ctx.send("❌ Only the server owner can set the staff role.")

        await settings_col.update_one(
            {"guild": str(ctx.guild.id)},
            {"$set": {"staff_role": role.id}},
            upsert=True,
        )
        await ctx.send(f"✅ Staff role set to {role.mention}")

    @commands.hybrid_command(
        name="staffget",
        description="Show the configured staff role. Staff-only.",
    )
    @staff_only()
    async def staffget(self, ctx: commands.Context) -> None:
        doc = await settings_col.find_one({"guild": str(ctx.guild.id)})
        role = ctx.guild.get_role(doc.get("staff_role")) if doc else None
        if role:
            await ctx.send(f"ℹ️ Staff role is {role.mention}.")
        else:
            await ctx.send("⚠️ No staff role is currently set.")

    @commands.hybrid_command(
        name="staff",
        description="Give the staff role to a user (owner-only).",
    )
    async def staff(self, ctx: commands.Context, member: discord.Member) -> None:
        data = await settings_col.find_one({"guild": str(ctx.guild.id)})
        if not data or "staff_role" not in data:
            return await ctx.send("❌ No staff role has been set. Use `/staffset` first.")

        staff_role = ctx.guild.get_role(data["staff_role"])
        if not staff_role:
            return await ctx.send(
                "⚠️ The saved staff role no longer exists on this server."
            )

        if (
            ctx.author != ctx.guild.owner
            and ctx.author.id not in STAFF_OVERRIDE_IDS
        ):
            return await ctx.send(
                "❌ Only the server owner and thetruck (for debugging purposes) "
                "can assign the staff role."
            )

        try:
            await member.add_roles(staff_role)
            await ctx.send(
                f"✅ {member.mention} has been given the {staff_role.mention} role!",
                allowed_mentions=AllowedMentions.none(),
            )

            embed = discord.Embed(
                title="Configure Staff Permissions",
                description=(
                    f"{ctx.author.mention}, use the dropdown below to configure "
                    f"which permission categories or commands\n"
                    f"{member.mention} should have access to. You may select "
                    "multiple. Choosing **Give All Permissions** will grant "
                    "everything.\n\n"
                    "Only the person who ran this command can use the dropdown. "
                    "This menu will not expire."
                ),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Target: {member} • User ID: {member.id}")

            view = StaffPermissionView(
                member, staffperms_col, ctx.guild.id, ctx.author.id
            )
            msg = await ctx.send(embed=embed, view=view)
            await view.initialize(msg)

        except discord.Forbidden:
            await ctx.send("❌ I don’t have permission to assign that role.")
        except Exception as exc:
            await ctx.send(f"⚠️ An error occurred: {exc}")

    @commands.hybrid_command(
        name="unstaff",
        description="Remove the staff role and permissions from a user.",
    )
    async def unstaff(self, ctx: commands.Context, member: discord.Member) -> None:
        data = await settings_col.find_one({"guild": str(ctx.guild.id)})
        if not data or "staff_role" not in data:
            return await ctx.send("❌ No staff role has been set. Use `/staffset` first.")

        staff_role = ctx.guild.get_role(data["staff_role"])
        if not staff_role:
            return await ctx.send(
                "⚠️ The saved staff role no longer exists on this server."
            )

        if ctx.author != ctx.guild.owner and ctx.author.id not in STAFF_OVERRIDE_IDS:
            return await ctx.send(
                "❌ Only the server owner and thetruck (for debugging purposes) "
                "can remove the staff role."
            )

        try:
            if staff_role in member.roles:
                await member.remove_roles(staff_role)
                await ctx.send(
                    f"✅ **{member.display_name}** no longer has the "
                    f"**{staff_role.name}** role."
                )
            else:
                await ctx.send(
                    f"⚠️ **{member.display_name}** does not currently have the "
                    f"**{staff_role.name}** role."
                )

            result = await staffperms_col.delete_one(
                {"guild": str(ctx.guild.id), "user": str(member.id)}
            )
            if result.deleted_count:
                await ctx.send(
                    f"🗑️ Removed **{member.display_name}**’s saved staff "
                    "permissions from the database."
                )
            else:
                await ctx.send(
                    f"ℹ️ No saved staff permissions were found for "
                    f"**{member.display_name}**."
                )

        except discord.Forbidden:
            await ctx.send("❌ I don’t have permission to remove that role.")
        except Exception as exc:
            await ctx.send(f"⚠️ An error occurred: {exc}")

    @commands.command(
        name="viewperms",
        description="View staff permissions for the server or a specific user.",
    )
    async def viewperms(
        self, ctx: commands.Context, member: discord.Member | None = None
    ) -> None:
        guild_id = str(ctx.guild.id)

        if member:
            data = await staffperms_col.find_one(
                {"guild": guild_id, "user": str(member.id)}
            )
            perms = data.get("permissions", []) if data else []
            perms_lower = [p.lower() for p in perms]

            embed = discord.Embed(
                title=f"Permissions for {member.display_name}",
                color=discord.Color.blurple(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(
                name="User",
                value=f"{member.mention}\n`{member.id}`",
                inline=False,
            )
            embed.add_field(
                name="Permissions",
                value=format_permission_details(perms_lower),
                inline=False,
            )
            return await ctx.send(embed=embed)

        docs = await staffperms_col.find({"guild": guild_id}).to_list(None)
        if not docs:
            return await ctx.send("ℹ️ No staff permissions found in this server.")

        docs.sort(key=lambda x: x["user"])
        pages: list[discord.Embed] = []
        for entry in docs:
            user_id = int(entry["user"])
            member_obj = ctx.guild.get_member(user_id)
            if not member_obj:
                continue

            perms = entry.get("permissions", [])
            perms_lower = [p.lower() for p in perms]

            embed = discord.Embed(
                title=f"Staff Permissions — {member_obj.display_name}",
                color=discord.Color.blurple(),
            )
            embed.set_thumbnail(url=member_obj.display_avatar.url)
            embed.add_field(
                name="User",
                value=f"{member_obj.mention}\n`{member_obj.id}`",
                inline=False,
            )
            embed.add_field(
                name="Permissions",
                value=format_permission_details(perms_lower),
                inline=False,
            )
            embed.set_footer(text=f"{ctx.guild.name} • {len(pages) + 1}/{len(docs)}")
            pages.append(embed)

        view = ViewPermsView(pages, ctx.author.id)
        await ctx.send(embed=pages[0], view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffCog(bot))
