# PatosX, a multipurpose Discord bot (moderation, economy, AI, fun)
# Copyright (C) 2025 theofficialtruck
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Staff role assignment and granular staff permissions, plus the blacklist/whitelist commands."""

import asyncio

import discord
from discord import (
    AllowedMentions,
    SelectOption,
    ui,
)
from discord.ext import commands

from core import config as cfg
from core import errors, state
from core import permsHelperFuncs as perms


class StaffPermissionSelect(ui.Select):
    def __init__(self, member: discord.Member, staffperms_col, guild_id: int, author_id: int, parent_view: ui.View):
        self.member = member
        self.staffperms_col = staffperms_col
        self.guild_id = guild_id
        self.author_id = author_id
        self.parent_view = parent_view
        super().__init__(
            placeholder="Loading ticket types...",
            min_values=1,
            max_values=1,
            options=[SelectOption(label="Loading...", value="loading")],
        )
        asyncio.create_task(self.load_options())

    async def load_options(self, message: discord.Message = None):
        base_options = [
            SelectOption(label="Kick", value="kick", description="Use the kick command"),
            SelectOption(label="Ban", value="ban", description="Use the ban command"),
            SelectOption(label="Mute", value="mute", description="Use the mute/unmute commands"),
            SelectOption(label="Stop Bot", value="stopbot", description="Lock the bot from responding"),
            SelectOption(label="Money Drop", value="money_drop", description="Use the drop command"),
            SelectOption(
                label="Other Moderation", value="other_moderation", description="warn / purge / slowmode / fine etc."
            ),
        ]
        ticket_options = [
            SelectOption(
                label="Ticket Admin", value="tickets:admin", description="Manage ticket panels and admin actions"
            )
        ]
        categories = {}
        cursor = state.ticket_panels_col.find({"guild": str(self.guild_id)})
        async for panel in cursor:
            for btn in panel.get("buttons", []):
                cat = btn.get("category_name")
                label = btn.get("label")
                emoji = btn.get("emoji")
                if cat:
                    categories[cat] = {"label": label or cat, "emoji": emoji}
        if categories:
            ticket_options.append(
                SelectOption(
                    label="All Ticket Types", value="tickets:all", description="Access to ALL ticket categories"
                )
            )
            for cat, info in categories.items():
                ticket_options.append(
                    SelectOption(
                        label=info["label"],
                        value=f"tickets:{cat}",
                        description=f"Access to ticket type: {info['label']}",
                        emoji=info["emoji"],
                    )
                )
        base_options += ticket_options + [
            SelectOption(label="StickyNotes", value="stickynotes", description="stickynote / unstickynote"),
            SelectOption(label="Economy", value="economy", description="shop, addmoney, drop, etc."),
            SelectOption(label="Vanity", value="vanity", description="vanityroles, promoters"),
            SelectOption(label="Roles", value="roles", description="roleadd / claimable roles"),
            SelectOption(label="Config Changes", value="config", description="configure / editconfig / viewconfig"),
            SelectOption(label="Invites", value="invites", description="invitechannel / invites / invite removal"),
            SelectOption(label="Enable/Disable", value="toggle_commands", description="enable/disable/listdisabled"),
            SelectOption(label="Reaction Roles", value="reactionroles", description="reactionrole management"),
            SelectOption(label="Giveaways", value="giveaways", description="giveaway / reroll"),
            SelectOption(label="Give All Permissions", value="all", description="Grant everything"),
        ]
        self.options = base_options
        self.max_values = len(base_options)
        self.placeholder = "Select staff permissions/categories to grant"
        if message is None and hasattr(self.parent_view, "message"):
            message = self.parent_view.message
        if message:
            try:
                await message.edit(view=self.parent_view)
            except discord.HTTPException:
                pass

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who ran the command can use this menu.", ephemeral=True
            )
            return
        selected = self.values
        if "all" in [s.lower() for s in selected]:
            permissions = ["all"]
            perms_text = "✅ All permissions granted!"
        else:
            permissions = [p.lower() for p in selected]
            perms_text = f"✅ Granted permissions: `{', '.join(selected)}`"
        await self.staffperms_col.update_one(
            {"guild": str(self.guild_id), "user": str(self.member.id)},
            {"$set": {"permissions": permissions}},
            upsert=True,
        )
        embed = discord.Embed(
            title="Permissions Updated",
            description=f"{self.member.mention} has been updated:\n{perms_text}\n\nYou can change selections at any time; this menu does not expire.",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Configured by {interaction.user} • User ID: {self.member.id}")
        try:
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
        except Exception:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class StaffPermissionView(ui.View):
    def __init__(self, member, staffperms_col, guild_id, author_id):
        super().__init__(timeout=None)
        self.select = StaffPermissionSelect(member, state.staffperms_col, guild_id, author_id, self)
        self.add_item(self.select)

    async def initialize(self, message):
        await self.select.load_options(message)


PERMISSION_COMMAND_MAP = {
    "kick": ["kick"],
    "ban": ["ban"],
    "mute": ["mute", "unmute"],
    "money_drop": ["drop"],
    "other_moderation": ["warn", "purge", "slowmode", "fine"],
    "stickynotes": ["stickynote", "unstickynote"],
    "economy": ["shop", "addmoney", "drop"],
    "vanity": ["vanityroles", "promoters"],
    "roles": ["roleadd", "claimableroles"],
    "config": ["configure", "editconfig", "viewconfig"],
    "invites": ["invitechannel", "invites", "removeinvite"],
    "toggle_commands": ["enable", "disable", "listdisabled"],
    "reactionroles": ["reactionrole"],
    "giveaways": ["giveaway", "reroll"],
    "tickets:admin": [
        "ticketsetup",
        "ticketpanel",
        "ticketaddbutton",
        "ticketremovebutton",
        "ticketeditbutton",
        "ticketdeletepanel",
        "ticketlist",
        "transcript",
        "transcriptsearch",
        "transcriptlist",
        "ticketadduser",
        "ticketremoveuser",
    ],
    "all": ["ALL COMMANDS"],
}


def format_permission_details(permissions: list[str]):
    if not permissions:
        return "No permissions"
    final = ""
    for p in permissions:
        cmds = PERMISSION_COMMAND_MAP.get(p, ["Unknown"])
        cmds_text = ", ".join(cmds)
        final += f"**• {p}** - `{cmds_text}`\n"
    return final


class ViewPermsView(discord.ui.View):
    def __init__(self, pages, author_id):
        super().__init__(timeout=120)
        self.pages = pages
        self.author_id = author_id
        self.index = 0

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the command user can use this menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction, button):
        self.index = 0
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction, button):
        if self.index > 0:
            self.index -= 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction, button):
        if self.index < len(self.pages) - 1:
            self.index += 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction, button):
        self.index = len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.primary)
    async def search(self, interaction, button):
        modal = ViewPermsSearchModal(self)
        await interaction.response.send_modal(modal)


class ViewPermsSearchModal(discord.ui.Modal, title="Search User"):
    def __init__(self, view_ref):
        super().__init__()
        self.view_ref = view_ref
        self.username = discord.ui.TextInput(label="Enter user ID", placeholder="Example: 1234567890123", required=True)
        self.add_item(self.username)

    async def on_submit(self, interaction):
        query = self.username.value.lower()
        for i, embed in enumerate(self.view_ref.pages):
            user_field = embed.fields[0].value
            if query in user_field.lower():
                self.view_ref.index = i
                await interaction.response.edit_message(embed=self.view_ref.pages[i], view=self.view_ref)
                return
        await interaction.response.send_message("❌ Could not find that user.", ephemeral=True)


async def get_or_create_blacklist_role(guild: discord.Guild, settings: dict):
    role = None
    if "blacklist_role" in settings:
        role = discord.utils.get(guild.roles, id=settings["blacklist_role"])
    if role is None:
        role = discord.utils.get(guild.roles, name="Blacklist")
    if role is None:
        role = await guild.create_role(
            name="Blacklist", colour=discord.Colour(0), reason="Blacklist role created automatically by bot"
        )
    await state.settings_col.update_one({"guild": str(guild.id)}, {"$set": {"blacklist_role": role.id}}, upsert=True)
    return role


async def resolve_member(ctx: commands.Context, member_str: str) -> discord.Member | None:
    try:
        member_id = int(member_str)
        member = ctx.guild.get_member(member_id)
        if member:
            return member
    except ValueError:
        pass
    if member_str.startswith("<@") and member_str.endswith(">"):
        member_id = int(member_str.replace("<@", "").replace("!", "").replace(">", ""))
        member = ctx.guild.get_member(member_id)
        if member:
            return member
    member = discord.utils.get(ctx.guild.members, name=member_str)
    return member


class StaffManagement(commands.Cog):
    """Staff role assignment and granular staff permissions, plus the blacklist/whitelist commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="staff", description="Give the staff role to a user (owner only).")
    async def staff(self, ctx, member: discord.Member):
        """Assign the configured staff role to member and open a permission selection menu."""
        data = await state.settings_col.find_one({"guild": str(ctx.guild.id)})
        if not data or "staff_role" not in data:
            return await ctx.send("❌ No staff role has been set. Use `.configure` first.")
        staff_role_id = data["staff_role"]
        staff_role = ctx.guild.get_role(staff_role_id)
        if not staff_role:
            return await ctx.send("⚠️ The saved staff role no longer exists on this server.")
        if ctx.author != ctx.guild.owner and ctx.author.id not in cfg.AUTHORIZED_USER_IDS:
            return await ctx.send(
                "❌ Only the server owner and authorized users (for debugging purposes) can assign the staff role."
            )
        try:
            await member.add_roles(staff_role)
            await ctx.send(
                f"✅ {member.mention} has been given the {staff_role.mention} role!",
                allowed_mentions=AllowedMentions.none(),
            )
            embed = discord.Embed(
                title="Configure Staff Permissions",
                description=f"{ctx.author.mention}, use the dropdown below to configure which permission categories or commands\n{member.mention} should have access to. You may select multiple. Choosing **Give All Permissions** will grant everything.\n\nOnly the person who ran this command can use the dropdown. This menu will not expire.",
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Target: {member} • User ID: {member.id}")
            view = StaffPermissionView(member, state.staffperms_col, ctx.guild.id, ctx.author.id)
            msg = await ctx.send(embed=embed, view=view)
            await view.initialize(msg)
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to assign that role.")
        except Exception as e:
            await ctx.send(f"⚠️ An error occurred: {e}")

    @commands.hybrid_command(name="unstaff", description="Remove the staff role and permissions from a user.")
    async def unstaff(self, ctx, member: discord.Member):
        """Remove the configured staff role from member and delete their saved staff permissions."""
        data = await state.settings_col.find_one({"guild": str(ctx.guild.id)})
        if not data or "staff_role" not in data:
            return await ctx.send("❌ No staff role has been set. Use `.configure` first.")
        staff_role_id = data["staff_role"]
        staff_role = ctx.guild.get_role(staff_role_id)
        if not staff_role:
            return await ctx.send("⚠️ The saved staff role no longer exists on this server.")
        if ctx.author != ctx.guild.owner and ctx.author.id not in cfg.AUTHORIZED_USER_IDS:
            return await ctx.send(
                "❌ Only the server owner and authorized users (for debugging purposes) can remove the staff role."
            )
        try:
            if staff_role in member.roles:
                await member.remove_roles(staff_role)
                await ctx.send(f"✅ **{member.display_name}** no longer has the **{staff_role.name}** role.")
            else:
                await ctx.send(f"⚠️ **{member.display_name}** does not currently have the **{staff_role.name}** role.")
            result = await state.staffperms_col.delete_one({"guild": str(ctx.guild.id), "user": str(member.id)})
            if result.deleted_count > 0:
                await ctx.send(f"🗑️ Removed **{member.display_name}**'s saved staff permissions from the database.")
            else:
                await ctx.send(f"ℹ️ No saved staff permissions were found for **{member.display_name}**.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to remove that role.")
        except Exception as e:
            await ctx.send(f"⚠️ An error occurred: {e}")

    @commands.hybrid_command(name="viewperms", description="View staff permissions for the server or a specific user.")
    async def viewperms(self, ctx, member: discord.Member = None):
        guild_id = str(ctx.guild.id)
        if member:
            data = await state.staffperms_col.find_one({"guild": guild_id, "user": str(member.id)})
            granted = data.get("permissions", []) if data else []
            perms_lower = [p.lower() for p in granted]
            embed = discord.Embed(title=f"Permissions for {member.display_name}", color=discord.Color.blurple())
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=False)
            embed.add_field(name="Permissions", value=format_permission_details(perms_lower), inline=False)
            return await ctx.send(embed=embed)
        docs = await state.staffperms_col.find({"guild": guild_id}).to_list(None)
        if not docs:
            return await ctx.send("ℹ️ No staff permissions found in this server.")
        docs.sort(key=lambda x: x["user"])
        pages = []
        for entry in docs:
            user_id = int(entry["user"])
            member_obj = ctx.guild.get_member(user_id)
            if not member_obj:
                continue
            granted = entry.get("permissions", [])
            perms_lower = [p.lower() for p in granted]
            embed = discord.Embed(title=f"Staff Permissions - {member_obj.display_name}", color=discord.Color.blurple())
            embed.set_thumbnail(url=member_obj.display_avatar.url)
            embed.add_field(name="User", value=f"{member_obj.mention}\n`{member_obj.id}`", inline=False)
            embed.add_field(name="Permissions", value=format_permission_details(perms_lower), inline=False)
            embed.set_footer(text=f"{ctx.guild.name} • {len(pages) + 1}/{len(docs)}")
            pages.append(embed)
        if not pages:
            return await ctx.send("ℹ️ No staff with saved permissions are still in this server.")
        view = ViewPermsView(pages, ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @commands.hybrid_command(name="blacklist", description="Blacklist a user from bot commands. Staff only.")
    @perms.staffperm("other_moderation")
    @perms.staff_only()
    async def blacklist(self, ctx, member: discord.Member):
        guild_id = str(ctx.guild.id)
        settings = await state.settings_col.find_one({"guild": guild_id})
        if not settings:
            settings = {"guild": guild_id}
            await state.settings_col.insert_one(settings)
        role = await get_or_create_blacklist_role(ctx.guild, settings)
        try:
            await member.add_roles(role, reason=f"Blacklisted by {ctx.author}")
            await perms.log_action(
                ctx,
                f"{member.mention} has been blacklisted from using bot commands.",
                user_id=member.id,
                action_type="Blacklist",
            )
            await ctx.send(f"🚫 {member.mention} has been blacklisted from using bot commands.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to add that role.")
        except Exception as e:
            await ctx.send(f"❌ Failed to add blacklist role: {e}")

    @commands.hybrid_command(name="whitelist", description="Remove a user from the blacklist. Staff only.")
    @perms.staffperm("other_moderation")
    @perms.staff_only()
    async def whitelist(self, ctx, member: discord.Member):
        guild_id = str(ctx.guild.id)
        settings = await state.settings_col.find_one({"guild": guild_id})
        if not settings:
            await ctx.send("⚠️ No settings found for this server.")
            return
        role = await get_or_create_blacklist_role(ctx.guild, settings)
        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"Unblacklisted by {ctx.author}")
            await perms.log_action(
                ctx,
                f"{member.mention} has been removed from the blacklist.",
                user_id=member.id,
                action_type="Whitelist",
            )
            await ctx.send(f"✅ {member.mention} has been whitelisted.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to remove that role.")
        except Exception as e:
            await ctx.send(f"❌ Failed to remove blacklist role: {e}")

    @blacklist.error
    async def blacklist_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await errors.send_hybrid_error(ctx, content="❌ You need **Manage Roles** permission to use this command.")
        elif isinstance(error, commands.BadArgument):
            await errors.send_hybrid_error(ctx, content="❌ Invalid user specified.")
        else:
            await errors.send_hybrid_error(ctx, content=f"⚠️ An error occurred: {error}")

    @whitelist.error
    async def whitelist_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await errors.send_hybrid_error(ctx, content="❌ You need **Manage Roles** permission to use this command.")
        elif isinstance(error, commands.BadArgument):
            await errors.send_hybrid_error(ctx, content="❌ Invalid user specified.")
        else:
            await errors.send_hybrid_error(ctx, content=f"⚠️ An error occurred: {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffManagement(bot))
