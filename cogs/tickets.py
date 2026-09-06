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

"""Ticket system: panels, buttons, ticket channels, transcripts, and the plain-text close confirmation."""

import io
import re
import traceback
from datetime import datetime, timezone

import discord
from discord import (
    Embed,
    File,
    SelectOption,
    app_commands,
)
from discord.ext import commands

from core import config as cfg
from core import errors, state
from core import permsHelperFuncs as perms


async def ticket_error(interaction: discord.Interaction, func):
    try:
        return await func()
    except Exception as e:
        print(f"[ERROR] ticket_error: {e}")
        traceback.print_exc()
        embed = discord.Embed(title="⚠️ Error", description="An unexpected error occurred.", color=discord.Color.red())
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def get_category_support_members(guild: discord.Guild, category_name: str):
    """Return the guild members who have ticket permissions for category_name (or tickets:all or all)."""
    category_key = f"tickets:{category_name.lower()}"
    all_key = "tickets:all"
    docs = await state.staffperms_col.find({"guild": str(guild.id)}).to_list(None)
    member_ids = []
    for entry in docs:
        raw_perms = entry.get("permissions", [])
        entry_perms = [p.lower() for p in raw_perms]
        if category_key in entry_perms or all_key in entry_perms or "all" in entry_perms:
            member_ids.append(int(entry["user"]))
    members = []
    for mid in member_ids:
        m = guild.get_member(mid)
        if m:
            members.append(m)
    return members


async def resolve_ticket_access_members(guild: discord.Guild, btn_data: dict, category_name: str):
    """Return the members who should get channel access (and be pinged) for a ticket opened
    from btn_data. A button with an explicit allowed_staff list (set via the staff-select
    prompt shown right after /ticketaddbutton) restricts access to just those members instead
    of the category's normal staff permissions. Buttons created before that feature existed
    have no allowed_staff key at all, so they fall through to the old category-wide behavior
    unchanged."""
    allowed_staff = btn_data.get("allowed_staff")
    if allowed_staff:
        members = []
        for uid in allowed_staff:
            try:
                member = guild.get_member(int(uid))
            except (TypeError, ValueError):
                member = None
            if member:
                members.append(member)
        return members
    return await get_category_support_members(guild, category_name)


async def get_ticket_button_permissions(guild_id: int):
    """Return a list of SelectOption objects covering every ticket button category in the guild,
    plus an 'All Ticket Types' option when at least one category exists."""
    cursor = state.ticket_panels_col.find({"guild": str(guild_id)})
    categories = {}
    async for panel in cursor:
        buttons = panel.get("buttons", [])
        for btn in buttons:
            cat = btn.get("category_name")
            label = btn.get("label")
            emoji = btn.get("emoji")
            if cat:
                categories[cat] = {"label": label or cat, "emoji": emoji}
    options = []
    if categories:
        options.append(
            SelectOption(label="All Ticket Types", value="tickets:all", description="Access to ALL ticket types")
        )
    for cat, info in categories.items():
        display = info["label"]
        emoji = info["emoji"]
        options.append(
            SelectOption(
                label=display, value=f"tickets:{cat}", description=f"Access to ticket type: {display}", emoji=emoji
            )
        )
    return options


class TicketSetupModal(discord.ui.Modal, title="Create Ticket Panel"):
    panel_name = discord.ui.TextInput(label="Panel Name", placeholder="Example: SupportPanel1", required=True)
    embed_title = discord.ui.TextInput(label="Embed Title", placeholder="Example: 🎫 Need Help?", required=True)
    embed_desc = discord.ui.TextInput(
        label="Embed Description",
        placeholder="Click a button below to create a ticket.",
        required=True,
        style=discord.TextStyle.paragraph,
    )
    embed_color = discord.ui.TextInput(label="Embed Color (hex)", placeholder="#5865F2", required=False)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction):
        guild = self.ctx.guild
        data = await state.settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None
        if not staff_role_id or staff_role_id not in [r.id for r in self.ctx.author.roles]:
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only staff members can use this command.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        color_value = int(self.embed_color.value.replace("#", ""), 16) if self.embed_color.value else 5793266
        await state.ticket_panels_col.insert_one(
            {
                "guild": str(guild.id),
                "panel_name": self.panel_name.value,
                "ticket_embed_title": self.embed_title.value,
                "ticket_embed_desc": self.embed_desc.value,
                "ticket_embed_color": color_value,
                "buttons": [],
            }
        )
        embed = discord.Embed(
            title="✅ Ticket Panel Created",
            description=f"Panel `{self.panel_name.value}` created successfully!\nUse `/ticketaddbutton` to add buttons.",
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class TicketAddButtonModal(discord.ui.Modal, title="Add Ticket Panel Button"):
    panel_name = discord.ui.TextInput(label="Panel Name", placeholder="Example: SupportPanel1", required=True)
    category_name = discord.ui.TextInput(label="Ticket Category", placeholder="Example: Support", required=True)
    button_label = discord.ui.TextInput(label="Button Label", placeholder="Example: Open Support Ticket", required=True)
    emoji = discord.ui.TextInput(label="Emoji (optional)", placeholder="Example: 🎫", required=False)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction):
        guild = self.ctx.guild
        data = await state.settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None
        if not staff_role_id or staff_role_id not in [r.id for r in self.ctx.author.roles]:
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only staff members can use this command.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        panel_data = await state.ticket_panels_col.find_one(
            {"guild": str(guild.id), "panel_name": self.panel_name.value}
        )
        if not panel_data:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Not Found",
                    description=f"No panel found with name `{self.panel_name.value}`.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        new_button = {
            "category_name": self.category_name.value,
            "label": self.button_label.value,
            "emoji": self.emoji.value if self.emoji.value else None,
        }
        await state.ticket_panels_col.update_one(
            {"guild": str(guild.id), "panel_name": self.panel_name.value}, {"$push": {"buttons": new_button}}
        )
        embed = discord.Embed(
            title="✅ Button Added",
            description=f"Added button to panel `{self.panel_name.value}`:\n{self.emoji.value or ''} **{self.button_label.value}** -> Category `{self.category_name.value}`",
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        await prompt_ticket_button_staff_choice(
            interaction, str(guild.id), self.panel_name.value, self.button_label.value, interaction.user.id
        )


async def prompt_ticket_button_staff_choice(
    ctx_or_interaction, guild_id: str, panel_name: str, btn_label: str, author_id: int
):
    """Ask the staff member who just added a ticket button whether access to tickets opened
    from it should be limited to specific staff, or left as the category's normal staff
    permissions. Works after either the modal flow (an Interaction, via followup) or the
    prefix wizard flow (a Context, via ctx.send) since both accept embed/view kwargs."""
    embed = discord.Embed(
        title="👥 Restrict Staff Access? (optional)",
        description=(
            f"By default, tickets opened from **{btn_label}** are visible to whichever staff "
            f"already have ticket permission for that category.\n\n"
            "Pick specific staff below if only certain people should be pinged and given access "
            "to tickets from this button, or press **Everyone** to keep the default."
        ),
        color=discord.Color.blurple(),
    )
    view = TicketButtonStaffChoiceView(guild_id, panel_name, btn_label, author_id)
    if isinstance(ctx_or_interaction, discord.Interaction):
        msg = await ctx_or_interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
    else:
        msg = await ctx_or_interaction.send(embed=embed, view=view)
    view.message = msg


class TicketButtonStaffSelect(discord.ui.UserSelect):
    def __init__(self, guild_id: str, panel_name: str, btn_label: str, author_id: int):
        super().__init__(placeholder="Choose specific staff members (optional)...", min_values=1, max_values=25)
        self.guild_id = guild_id
        self.panel_name = panel_name
        self.btn_label = btn_label
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ This isn't your setup prompt.", ephemeral=True)
        guild = interaction.guild
        valid_members = []
        invalid_users = []
        for u in self.values:
            member = u if isinstance(u, discord.Member) else guild.get_member(u.id)
            if member and await perms.has_staff_role(member, guild):
                valid_members.append(member)
            else:
                invalid_users.append(u)
        if not valid_members:
            return await interaction.response.send_message(
                "❌ None of the selected users hold the configured staff role. Pick actual staff members.",
                ephemeral=True,
            )
        staff_ids = [str(m.id) for m in valid_members]
        await state.ticket_panels_col.update_one(
            {"guild": self.guild_id, "panel_name": self.panel_name, "buttons.label": self.btn_label},
            {"$set": {"buttons.$.allowed_staff": staff_ids}},
        )
        mentions = ", ".join(m.mention for m in valid_members)
        description = f"Only {mentions} will be pinged and given access to tickets opened from **{self.btn_label}**."
        if invalid_users:
            skipped = ", ".join(u.mention for u in invalid_users)
            description += f"\n⚠️ Skipped (not staff): {skipped}"
        embed = discord.Embed(
            title="✅ Ticket Access Restricted",
            description=description,
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.view.message = None
        self.view.stop()


class TicketButtonStaffChoiceView(discord.ui.View):
    def __init__(self, guild_id: str, panel_name: str, btn_label: str, author_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.panel_name = panel_name
        self.btn_label = btn_label
        self.author_id = author_id
        self.message = None
        self.add_item(TicketButtonStaffSelect(guild_id, panel_name, btn_label, author_id))

    @discord.ui.button(label="🌐 Everyone (default staff permissions)", style=discord.ButtonStyle.grey)
    async def everyone_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ This isn't your setup prompt.", ephemeral=True)
        await state.ticket_panels_col.update_one(
            {"guild": self.guild_id, "panel_name": self.panel_name, "buttons.label": self.btn_label},
            {"$set": {"buttons.$.allowed_staff": None}},
        )
        embed = discord.Embed(
            title="✅ Open Access",
            description=f"Tickets opened from **{self.btn_label}** will use the normal staff permissions for its category.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.message = None
        self.stop()

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(
                    content="⌛ No staff selection made - this button will use the normal staff permissions for its category.",
                    embed=None,
                    view=None,
                )
            except (discord.HTTPException, discord.NotFound):
                pass


class TicketEditButtonModal(discord.ui.Modal, title="Edit Ticket Panel Button"):
    category_name = discord.ui.TextInput(label="Ticket Category", required=True)
    button_label = discord.ui.TextInput(label="Button Label", required=True)
    emoji = discord.ui.TextInput(label="Emoji (optional)", required=False)

    def __init__(self, ctx, panel_name, btn_data):
        super().__init__()
        self.ctx = ctx
        self.panel_name = panel_name
        self.btn_data = btn_data
        self.category_name.default = btn_data.get("category_name", "")
        self.button_label.default = btn_data.get("label", "")
        self.emoji.default = btn_data.get("emoji", "")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction):
        guild = self.ctx.guild
        await state.ticket_panels_col.update_one(
            {"guild": str(guild.id), "panel_name": self.panel_name, "buttons.label": self.btn_data["label"]},
            {
                "$set": {
                    "buttons.$.category_name": self.category_name.value,
                    "buttons.$.label": self.button_label.value,
                    "buttons.$.emoji": self.emoji.value if self.emoji.value else None,
                }
            },
        )
        embed = discord.Embed(
            title="✅ Button Updated",
            description=f"Updated button in panel `{self.panel_name}`:\n{self.emoji.value or ''} **{self.button_label.value}** -> Category `{self.category_name.value}`",
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class TicketButtonActionView(discord.ui.View):
    def __init__(self, ctx, panel_name, btn_data):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.panel_name = panel_name
        self.btn_data = btn_data

    @discord.ui.button(label="✏ Edit", style=discord.ButtonStyle.blurple)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ticket_error(interaction, lambda: self._edit(interaction))

    async def _edit(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can edit.", ephemeral=True
            )
        modal = TicketEditButtonModal(self.ctx, self.panel_name, self.btn_data)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑 Delete", style=discord.ButtonStyle.red)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ticket_error(interaction, lambda: self._delete(interaction))

    async def _delete(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can delete.", ephemeral=True
            )
        guild = self.ctx.guild
        await state.ticket_panels_col.update_one(
            {"guild": str(guild.id), "panel_name": self.panel_name},
            {"$pull": {"buttons": {"label": self.btn_data["label"]}}},
        )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🗑 Button Deleted",
                description=f"Removed **{self.btn_data['label']}** from panel `{self.panel_name}`.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        self.stop()


class TicketPanelEditView(discord.ui.View):
    def __init__(self, ctx, panel_data):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.panel_data = panel_data
        for btn in panel_data.get("buttons", []):
            self.add_item(TicketEditButton(btn, panel_data, ctx))


class TicketEditButton(discord.ui.Button):
    def __init__(self, btn_data, panel_data, ctx):
        safe_category = btn_data["category_name"].replace(" ", "_")
        safe_label = btn_data["label"].replace(" ", "_")
        super().__init__(
            label=btn_data.get("label", "Unnamed"),
            emoji=btn_data.get("emoji") or None,
            style=discord.ButtonStyle.gray,
            custom_id=f"editbtn_{safe_category}_{safe_label}",
        )
        self.btn_data = btn_data
        self.panel_data = panel_data
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await ticket_error(interaction, lambda: self._callback(interaction))

    async def _callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can manage buttons.", ephemeral=True
            )
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"⚙ Manage Button: {self.btn_data.get('label', 'Unnamed')}",
                description="Choose what you want to do with this button.",
                color=discord.Color.orange(),
            ),
            view=TicketButtonActionView(self.ctx, self.panel_data["panel_name"], self.btn_data),
            ephemeral=True,
        )


class TicketPanelView(discord.ui.View):
    def __init__(self, panel_data):
        super().__init__(timeout=None)
        self.panel_data = panel_data
        for btn in panel_data.get("buttons", []):
            self.add_item(TicketCategoryButton(btn, panel_data))


class TicketCategoryButton(discord.ui.Button):
    def __init__(self, btn_data, panel_data):
        safe_category = btn_data["category_name"].replace(" ", "_")
        safe_label = btn_data["label"].replace(" ", "_")
        guild_id = panel_data.get("guild", "unknown")
        panel_name = panel_data.get("panel_name", "unknown").replace(" ", "_")
        super().__init__(
            label=btn_data.get("label", "Open Ticket"),
            emoji=btn_data.get("emoji") or None,
            style=discord.ButtonStyle.green,
            custom_id=f"ticket_{guild_id}_{panel_name}_{safe_category}_{safe_label}",
        )
        self.btn_data = btn_data
        self.panel_data = panel_data

    async def callback(self, interaction: discord.Interaction):
        await ticket_error(interaction, lambda: self.create_ticket(interaction))

    async def create_ticket(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        author = interaction.user
        counter_doc = await state.tickets_counter_col.find_one({"guild": str(guild.id)})
        if not counter_doc:
            ticket_number = 1
            await state.tickets_counter_col.insert_one({"guild": str(guild.id), "counter": ticket_number})
        else:
            ticket_number = counter_doc["counter"] + 1
            await state.tickets_counter_col.update_one({"guild": str(guild.id)}, {"$set": {"counter": ticket_number}})
        safe_username = re.sub("[^a-zA-Z0-9_-]", "", author.name).lower()
        safe_label = re.sub("[^a-zA-Z0-9_-]", "", self.btn_data["label"]).replace(" ", "-").lower()
        ticket_name = f"{safe_username}-{safe_label}"
        if len(ticket_name) > 90:
            available = 90 - (len(safe_username) + 1)
            if available < 1:
                safe_username = safe_username[:45]
                safe_label = safe_label[:44]
            else:
                safe_label = safe_label[:available]
            ticket_name = f"{safe_username}-{safe_label}"
        data = await state.settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None
        if not staff_role_id:
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Staff Role Not Set", description="Use `.configure` first.", color=discord.Color.red()
                ),
                ephemeral=True,
            )
        category = discord.utils.get(guild.categories, name="Tickets") or await guild.create_category("Tickets")
        for c in category.channels:
            if c.name.lower() == ticket_name.lower():
                return await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Duplicate Ticket",
                        description=f"A ticket with that name already exists: {c.mention}",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
        category_name = self.btn_data["category_name"].lower()
        access_members = await resolve_ticket_access_members(guild, self.btn_data, category_name)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, embed_links=True, attach_files=True),
            author: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True
            ),
        }
        for member in access_members:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True
            )
        channel = await guild.create_text_channel(ticket_name, category=category, overwrites=overwrites)
        await state.tickets_col.insert_one(
            {
                "guild": str(guild.id),
                "channel_id": str(channel.id),
                "owner_id": str(author.id),
                "category": category_name.lower(),
                "allowed_staff": self.btn_data.get("allowed_staff"),
                "created_at": datetime.now(timezone.utc),
            }
        )
        embed = discord.Embed(
            title="🎟️ Ticket Created",
            description="Please state your concern and the staff team will respond soon.",
            color=discord.Color(self.panel_data.get("ticket_embed_color", 5793266)),
        )
        await channel.send(embed=embed)
        await ping_ticket_roles(channel, guild.id, opener_id=author.id)
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Ticket Created!",
                description=f"Your ticket was successfully created!\nHere it is: {channel.mention}",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


async def ping_ticket_roles(channel: discord.TextChannel, guild_id: str, opener_id: int | None = None):
    """Mention any staff members with ticket permissions in the newly created ticket channel."""
    try:
        allowed_members = {}
        staff_role_mentions = set()
        ticket_entry = await state.tickets_col.find_one({"guild": str(guild_id), "channel_id": str(channel.id)})
        restricted_staff = ticket_entry.get("allowed_staff") if ticket_entry else None
        if restricted_staff:
            # This ticket's button was configured with a specific staff list, so only those
            # members - not the category's normal staff or the general staff role - get pinged.
            for uid in restricted_staff:
                try:
                    member = channel.guild.get_member(int(uid))
                except (TypeError, ValueError):
                    member = None
                if member and member.id != opener_id:
                    allowed_members[member.id] = member
        else:
            category_name = str(ticket_entry.get("category", "")).strip().lower() if ticket_entry else ""
            category_support_members = []
            if category_name:
                category_support_members = await get_category_support_members(channel.guild, category_name)
            for member in category_support_members:
                if member.id != opener_id:
                    allowed_members[member.id] = member
            data = await state.settings_col.find_one({"guild": str(guild_id)})
            staff_role_id = data.get("staff_role") if data else None
            if staff_role_id:
                staff_role = channel.guild.get_role(int(staff_role_id))
                if staff_role:
                    staff_role_mentions.add(staff_role.mention)
        for member in channel.members:
            if member.bot or member.id == opener_id:
                continue
            if channel.permissions_for(member).view_channel:
                allowed_members[member.id] = member
        if not allowed_members and (not staff_role_mentions):
            return
        ping_parts = list(staff_role_mentions)
        ping_parts.extend(member.mention for member in allowed_members.values())
        ping_text = " ".join(ping_parts)
        msg = await channel.send(
            content=ping_text, allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False)
        )
        await msg.delete(delay=8)
    except Exception:
        print("ping_ticket_roles ERROR:", traceback.format_exc())


async def actually_close_ticket(ctx, opener, forced=False):
    """Snapshot the ticket channel's message history, store the transcript, then delete the channel.
    forced=True indicates the close was initiated by staff rather than the ticket opener."""
    channel = ctx.channel
    messages = [msg async for msg in channel.history(limit=None, oldest_first=True)]
    transcript_text = "\n".join([f"[{msg.created_at}] {msg.author}: {msg.content}" for msg in messages])
    ticket_id = f"{channel.id}-{int(datetime.now(timezone.utc).timestamp())}"
    await state.tickets_col.insert_one(
        {
            "ticket_id": ticket_id,
            "guild_id": str(channel.guild.id),
            "channel_id": str(channel.id),
            "opener_id": str(opener.id) if opener else None,
            "closer_id": str(ctx.author.id),
            "closer_name": str(ctx.author),
            "transcript": transcript_text,
            "created_at": str(channel.created_at),
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "forced": forced,
        }
    )
    transcript_file = io.StringIO(transcript_text)
    discord_file = discord.File(fp=transcript_file, filename=f"{ticket_id}_transcript.txt")
    if opener:
        try:
            await opener.send(
                embed=discord.Embed(
                    title="📜 Ticket Transcript",
                    description=f"Transcript for `{channel.name}` attached below.",
                    color=discord.Color.blue(),
                ),
                file=discord_file,
            )
        except (discord.HTTPException, discord.Forbidden):
            pass
    action_type = "forceclose" if forced else "close"
    closer_text = f"{ctx.author} ({ctx.author.mention})"
    opener_text = f"{opener} ({opener.mention})" if opener else "Unknown"
    await perms.log_action(
        ctx,
        f"Ticket `{channel.name}` closed by {closer_text} (opener: {opener_text}){(' [FORCED]' if forced else '')}",
        user_id=ctx.author.id,
        action_type=action_type,
    )
    if forced:
        await channel.send(f"✅ Ticket force closed by {ctx.author.mention}.")
    else:
        await channel.send("✅ Ticket confirmed and closed.")
    await channel.delete()


class TicketRemoveButtonSelect(discord.ui.Select):
    """Dropdown listing every button on a panel, used to pick which one to delete."""

    def __init__(self, ctx, panel_data):
        self.ctx = ctx
        self.panel_data = panel_data
        buttons = panel_data.get("buttons", [])
        options = [
            discord.SelectOption(
                label=(btn.get("label") or "Unnamed")[:100],
                description=f"Category: {btn.get('category_name', 'Unknown')}"[:100],
                emoji=btn.get("emoji") or None,
                value=str(i),
            )
            # Discord select menus support at most 25 options.
            for i, btn in enumerate(buttons[:25])
        ]
        super().__init__(placeholder="Select a button to remove...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await ticket_error(interaction, lambda: self._callback(interaction))

    async def _callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can remove a button.", ephemeral=True
            )
        buttons = self.panel_data.get("buttons", [])
        idx = int(self.values[0])
        if idx >= len(buttons):
            return await interaction.response.send_message("❌ That button no longer exists.", ephemeral=True)
        btn_data = buttons[idx]
        await state.ticket_panels_col.update_one(
            {"guild": str(self.ctx.guild.id), "panel_name": self.panel_data["panel_name"]},
            {"$pull": {"buttons": {"label": btn_data.get("label")}}},
        )
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🗑 Button Removed",
                description=f"Removed **{btn_data.get('label', 'Unnamed')}** from panel `{self.panel_data['panel_name']}`.",
                color=discord.Color.red(),
            ),
            view=None,
        )


class TicketRemoveButtonView(discord.ui.View):
    def __init__(self, ctx, panel_data):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.add_item(TicketRemoveButtonSelect(ctx, panel_data))


def _normalize_open_ticket(t: dict) -> dict:
    """Still-open ticket documents use a different schema than closed-ticket transcript
    records (guild/owner_id vs guild_id/opener_id) because actually_close_ticket writes a
    brand-new document instead of updating the original one, and the original is only
    deleted once the ticket closes. Normalize field names so both schemas can be merged
    into a single list for display."""
    return {
        "channel_id": t.get("channel_id"),
        "opener_id": t.get("owner_id"),
        "created_at": t.get("created_at"),
    }


def _ticket_sort_key(t: dict) -> datetime:
    """Sort key used to merge open and closed tickets into one newest-to-oldest list:
    closed tickets sort by when they closed, open ones by when they were opened."""
    raw = t.get("closed_at") or t.get("created_at")
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


class TranscriptPaginationView(discord.ui.View):
    def __init__(self, ctx, tickets, per_page=8):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.tickets = tickets
        self.per_page = per_page
        self.page = 0
        self.max_page = (len(tickets) - 1) // per_page
        self.message = None

    def format_time(self, dt, style="both"):
        if not dt:
            return "Unknown"
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError:
                return str(dt)
        if not isinstance(dt, datetime):
            return "Unknown"
        if dt.tzinfo is None:
            # Mongo strips tzinfo from stored datetimes and always returns UTC wall-clock
            # values, so a naive dt here is UTC, not local time - without this, .timestamp()
            # would assume the local system timezone and skew the rendered time by that offset.
            dt = dt.replace(tzinfo=timezone.utc)
        ts = int(dt.timestamp())
        if style == "full":
            return f"<t:{ts}:F>"
        elif style == "short":
            return f"<t:{ts}:f>"
        elif style == "relative":
            return f"<t:{ts}:R>"
        else:
            return f"<t:{ts}:f> • <t:{ts}:R>"

    async def format_user(self, user_id):
        if not user_id or user_id == "unknown":
            return "Unknown"
        try:
            user_id = int(user_id)
            user_id_int = int(user_id)
            user = self.ctx.bot.get_user(user_id_int) or await self.ctx.bot.fetch_user(user_id_int)
            return user.mention if user else "Unknown"
        except Exception:
            return "Unknown"

    async def build_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.tickets[start:end]
        embed = discord.Embed(
            title="📜 Ticket Overview",
            description=f"Sorted newest to oldest • **{len(self.tickets)}** ticket{'s' if len(self.tickets) != 1 else ''} total",
            color=discord.Color.blurple(),
        )
        for idx, t in enumerate(chunk, start=start + 1):
            opener = await self.format_user(t.get("opener_id"))
            opened_at = self.format_time(t.get("created_at"), "relative")
            is_closed = bool(t.get("closed_at"))
            lines = [f"👤 Opened by {opener} • {opened_at}"]
            if is_closed:
                closer = await self.format_user(t.get("closer_id"))
                closed_at = self.format_time(t.get("closed_at"), "relative")
                lines.append(f"🔒 Closed by {closer} • {closed_at}")
                lines.append(f"`{t.get('ticket_id', 'Unknown')}`")
            elif t.get("channel_id"):
                lines.append(f"📍 <#{t['channel_id']}>")
            badge = "🔴 Closed" if is_closed else "🟢 Ongoing"
            embed.add_field(name=f"{badge} • Ticket #{idx}", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{self.max_page + 1}")
        return embed

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can use this.", ephemeral=True
            )
        self.page = max(0, self.page - 1)
        self.children[0].disabled = self.page == 0
        self.children[1].disabled = self.page == self.max_page
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can use this.", ephemeral=True
            )
        self.page = min(self.max_page, self.page + 1)
        self.children[0].disabled = self.page == 0
        self.children[1].disabled = self.page == self.max_page
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.delete()
            except discord.NotFound:
                pass


class Tickets(commands.Cog):
    """Ticket system: panels, buttons, ticket channels, transcripts, and the plain-text close confirmation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    async def resolve_ticket_opener(self, guild: discord.Guild, user_id):
        """Resolve a ticket opener to a Member/User object. guild.get_member() only checks the
        local member cache, which misses members who haven't been active recently - that cache
        miss was silently turning into a None opener, which is why closed ticket transcripts
        recorded no opener_id and showed "Opened by: Unknown" for practically every ticket.
        Falls back to an API fetch, and finally to a global user fetch for members who have
        since left the guild (still valid for DMing the transcript and displaying who it was)."""
        if not user_id:
            return None
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None
        member = guild.get_member(user_id_int)
        if member:
            return member
        try:
            return await guild.fetch_member(user_id_int)
        except (discord.NotFound, discord.HTTPException):
            pass
        try:
            return await self.bot.fetch_user(user_id_int)
        except (discord.NotFound, discord.HTTPException):
            return None

    @commands.hybrid_command(
        name="ticketaddbutton", description="Add a button to an existing ticket panel (form). Staff only."
    )
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketaddbutton(self, ctx):
        try:
            if errors.is_prefix(ctx):

                def check(m):
                    return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

                await ctx.send("📝 Enter the **panel name**:")
                panel_name_msg = await self.bot.wait_for("message", check=check)
                panel_name = panel_name_msg.content
                await ctx.send("🗂 Enter the **ticket category name**:")
                category_msg = await self.bot.wait_for("message", check=check)
                category_name = category_msg.content
                await ctx.send("🔘 Enter the **button label**:")
                label_msg = await self.bot.wait_for("message", check=check)
                button_label = label_msg.content
                await ctx.send("😎 Enter an **emoji** (optional, type `none` to skip):")
                emoji_msg = await self.bot.wait_for("message", check=check)
                emoji = None if emoji_msg.content.lower() == "none" else emoji_msg.content
                guild = ctx.guild
                panel_data = await state.ticket_panels_col.find_one({"guild": str(guild.id), "panel_name": panel_name})
                if not panel_data:
                    return await ctx.send(f"❌ No panel found with name `{panel_name}`.")
                new_button = {"category_name": category_name, "label": button_label, "emoji": emoji}
                await state.ticket_panels_col.update_one(
                    {"guild": str(guild.id), "panel_name": panel_name}, {"$push": {"buttons": new_button}}
                )
                await ctx.send(
                    f"✅ Added button to panel `{panel_name}`:\n{emoji or ''} **{button_label}** -> Category **{category_name}**"
                )
                return await prompt_ticket_button_staff_choice(
                    ctx, str(guild.id), panel_name, button_label, ctx.author.id
                )
            await ctx.interaction.response.send_modal(TicketAddButtonModal(ctx))
        except Exception as e:
            print("ticketaddbutton ERROR:", traceback.format_exc())
            if ctx.interaction and (not ctx.interaction.response.is_done()):
                await ctx.interaction.response.send_message(f"❌ Error:\n```{e}```", ephemeral=True)

    @commands.command(
        name="ticketremovebutton", description="Remove a button from an existing ticket panel (form). Staff only."
    )
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketremovebutton(self, ctx, *, panel_name: str):
        # Prefix-only (unlike its sibling ticket commands): the bot is already at Discord's
        # 100 global slash-command cap, so this staff-only utility skips slash registration.
        try:
            guild = ctx.guild
            panel_data = await state.ticket_panels_col.find_one({"guild": str(guild.id), "panel_name": panel_name})
            if not panel_data:
                return await ctx.send(f"❌ No panel found with name `{panel_name}`.")
            if not panel_data.get("buttons"):
                return await ctx.send(f"❌ Panel `{panel_name}` has no buttons to remove.")
            embed = discord.Embed(
                title=f"🗑 Remove Button: {panel_name}",
                description="Select a button below to remove it from the panel.",
                color=discord.Color.orange(),
            )
            view = TicketRemoveButtonView(ctx, panel_data)
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            print("ticketremovebutton ERROR:", traceback.format_exc())
            await ctx.send(f"❌ Error:\n```{e}```")

    @commands.hybrid_command(name="ticketsetup", description="Create interactive ticket panel. Staff only.")
    @app_commands.describe(panel_name="Name for the ticket panel (e.g., 'Support', 'Help Desk')")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketsetup(self, ctx, panel_name: str = "Support"):
        try:
            data = await state.settings_col.find_one({"guild": str(ctx.guild.id)})
            staff_role_id = data.get("staff_role") if data else None
            if not staff_role_id or staff_role_id not in [r.id for r in ctx.author.roles]:
                msg = "❌ Only staff members can create a panel."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return
            if errors.is_prefix(ctx):
                return await ctx.send("⚠ This command requires modal interaction. Please use the command properly.")
            await ctx.interaction.response.send_modal(TicketSetupModal(ctx))
        except Exception as e:
            print("ticketsetup ERROR:", traceback.format_exc())
            if ctx.interaction and (not ctx.interaction.response.is_done()):
                await ctx.interaction.response.send_message(f"❌ Error:\n```{e}```", ephemeral=True)

    @commands.hybrid_command(name="ticketpanel", description="Post a saved ticket panel. Staff only.")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketpanel(self, ctx, panel_name: str):
        try:
            panel_data = await state.ticket_panels_col.find_one({"guild": str(ctx.guild.id), "panel_name": panel_name})
            if not panel_data:
                msg = f"❌ No ticket panel found with name `{panel_name}`."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return
            embed = discord.Embed(
                title=panel_data.get("ticket_embed_title", "🎫 Ticket Panel"),
                description=panel_data.get("ticket_embed_desc", "Click a button below to create a ticket."),
                color=discord.Color(int(panel_data.get("ticket_embed_color", 5793266))),
            )
            view = TicketPanelView(panel_data)
            if ctx.interaction:
                msg = await ctx.interaction.response.send_message(embed=embed, view=view)
            else:
                msg = await ctx.send(embed=embed, view=view)
            await state.ticket_panels_col.update_one(
                {"_id": panel_data["_id"]}, {"$set": {"message_id": msg.id, "channel_id": str(msg.channel.id)}}
            )
        except Exception as e:
            print("ticketpanel ERROR:", traceback.format_exc())
            if ctx.interaction:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(f"❌ Error:\n```{e}```", ephemeral=True)
            else:
                await ctx.send(f"❌ Error:\n```{e}```")

    @commands.hybrid_command(name="ticketeditbutton", description="Edit a button in a ticket panel. Staff only.")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketeditbutton(self, ctx, panel_name: str):
        try:
            if ctx.interaction and (not ctx.interaction.response.is_done()):
                await ctx.interaction.response.defer(ephemeral=True)
            panel_data = await state.ticket_panels_col.find_one({"guild": str(ctx.guild.id), "panel_name": panel_name})
            if not panel_data:
                msg = f"❌ No ticket panel found with name `{panel_name}`."
                if ctx.interaction:
                    await ctx.interaction.followup.send(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return
            embed = discord.Embed(
                title=f"📝 Edit Mode: {panel_name}",
                description="Click a button below to edit or delete it.",
                color=discord.Color.orange(),
            )
            view = TicketPanelEditView(ctx, panel_data)
            if ctx.interaction:
                await ctx.interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                await ctx.send(embed=embed, view=view)
        except Exception as e:
            print("ticketeditbutton ERROR:", traceback.format_exc())
            if ctx.interaction:
                await ctx.interaction.followup.send(f"❌ Error:\n```{e}```", ephemeral=True)
            else:
                await ctx.send(f"❌ Error:\n```{e}```")

    @commands.hybrid_command(name="ticketdeletepanel", description="Delete a saved ticket panel. Staff only.")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketdeletepanel(self, ctx, panel_name: str):
        try:
            result = await state.ticket_panels_col.delete_one({"guild": str(ctx.guild.id), "panel_name": panel_name})
            if result.deleted_count == 0:
                msg = f"❌ No panel found with name `{panel_name}`."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return
            msg = f"🗑 Panel `{panel_name}` deleted successfully."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
        except Exception:
            print("ticketdeletepanel ERROR:", traceback.format_exc())

    @commands.hybrid_command(name="ticketlist", description="List all saved ticket panels. Staff only.")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketlist(self, ctx):
        try:
            panels = await state.ticket_panels_col.find({"guild": str(ctx.guild.id)}).to_list(length=50)
            if not panels:
                msg = "❌ No saved ticket panels."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return
            embed = discord.Embed(title="📋 Saved Ticket Panels", color=discord.Color.blurple())
            for panel in panels:
                embed.add_field(
                    name=panel["panel_name"], value=f"{len(panel.get('buttons', []))} button(s)", inline=False
                )
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
        except Exception:
            print("ticketlist ERROR:", traceback.format_exc())

    @commands.hybrid_command(name="ticketclose", description="Request to close the current ticket.")
    async def ticketclose(self, ctx):
        async def send_public(*, content=None, embed=None):
            if errors.is_prefix(ctx):
                return await ctx.send(content=content, embed=embed)
            if ctx.interaction.response.is_done():
                return await ctx.interaction.followup.send(content=content, embed=embed, ephemeral=False)
            return await ctx.interaction.response.send_message(content=content, embed=embed, ephemeral=False)

        async def local_error_handler(func):
            try:
                return await func()
            except Exception as e:
                print(f"[ERROR] ticketclose: {e}")
                traceback.print_exc()
                embed = discord.Embed(
                    title="⚠️ Error", description="An unexpected error occurred.", color=discord.Color.red()
                )
                await errors.send_hybrid_error(ctx, embed=embed)

        async def inner():
            channel = ctx.channel
            ticket_entry = await state.tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})
            if not ticket_entry:
                return await errors.send_hybrid_error(
                    ctx, content="❌ This command can only be used inside a ticket channel."
                )
            opener = await self.resolve_ticket_opener(channel.guild, ticket_entry.get("owner_id"))
            if not opener:
                return await errors.send_hybrid_error(ctx, content="⚠️ Could not find the ticket opener.")
            await state.tickets_col.update_one({"_id": ticket_entry["_id"]}, {"$set": {"close_pending": True}})
            await send_public(
                content=f"{opener.mention}, do you confirm closing this ticket? Type `confirm` to close or `cancel` to keep it open. (This will wait until you reply, no time limit.)"
            )

        await local_error_handler(inner)

    @commands.hybrid_command(name="ticketforceclose", description="Force close the current ticket.")
    @perms.staff_only()
    async def ticketforceclose(self, ctx):
        async def local_error_handler(func):
            try:
                return await func()
            except Exception as e:
                print(f"[ERROR] ticketforceclose: {e}")
                traceback.print_exc()
                embed = discord.Embed(
                    title="⚠️ Error", description="An unexpected error occurred.", color=discord.Color.red()
                )
                await errors.send_hybrid_error(ctx, embed=embed)

        async def inner():
            channel = ctx.channel
            ticket_entry = await state.tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})
            if not ticket_entry:
                return await errors.send_hybrid_error(
                    ctx, content="❌ This command can only be used inside a ticket channel."
                )
            opener = await self.resolve_ticket_opener(channel.guild, ticket_entry.get("owner_id"))
            await actually_close_ticket(ctx, opener, forced=True)
            await state.tickets_col.delete_one({"_id": ticket_entry["_id"]})

        await local_error_handler(inner)

    @commands.hybrid_command(name="transcript", description="Fetch a ticket transcript. Staff only.")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def transcript(self, ctx, ticket_id: str):
        try:
            ticket = await state.tickets_col.find_one({"ticket_id": ticket_id, "guild_id": str(ctx.guild.id)})
            if not ticket:
                msg = "❌ No ticket found with that ID."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return

            def format_time(dt, style="both"):
                if not dt:
                    return "Unknown"
                if isinstance(dt, str):
                    try:
                        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                    except ValueError:
                        return str(dt)
                if not isinstance(dt, datetime):
                    return "Unknown"
                if dt.tzinfo is None:
                    # Mongo strips tzinfo from stored datetimes and always returns UTC wall-clock
                    # values, so a naive dt here is UTC, not local time - without this, .timestamp()
                    # would assume the local system timezone and skew the rendered time by that offset.
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = int(dt.timestamp())
                if style == "full":
                    return f"<t:{ts}:F>"
                elif style == "short":
                    return f"<t:{ts}:f>"
                elif style == "relative":
                    return f"<t:{ts}:R>"
                else:
                    return f"<t:{ts}:f> • <t:{ts}:R>"

            embed = Embed(title=f"🎟 Transcript for {ticket_id}", color=5793266, timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Opened by", value=f"<@{ticket['opener_id']}>", inline=True)
            embed.add_field(name="Closed by", value=f"<@{ticket['closer_id']}>", inline=True)
            embed.add_field(name="Opened at", value=format_time(ticket.get("created_at")), inline=True)
            embed.add_field(name="Closed at", value=format_time(ticket.get("closed_at")), inline=True)
            transcript_file = io.StringIO(ticket["transcript"])
            discord_file = File(fp=transcript_file, filename=f"{ticket_id}_transcript.txt")
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, file=discord_file, ephemeral=True)
            else:
                await ctx.send(embed=embed, file=discord_file)
        except Exception:
            print("transcript ERROR:", traceback.format_exc())

    @commands.hybrid_command(name="transcriptsearch", description="Search tickets by username. Staff only.")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def transcriptsearch(self, ctx, username: str):
        try:
            query = {
                "guild_id": str(ctx.guild.id),
                "$or": [
                    {"opener_name": {"$regex": username, "$options": "i"}},
                    {"closer_name": {"$regex": username, "$options": "i"}},
                ],
            }
            tickets = await state.tickets_col.find(query).to_list(length=20)
            if not tickets:
                msg = f"❌ No tickets found for username containing `{username}`."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return
            embed = discord.Embed(title=f"🔍 Tickets matching '{username}'", color=5763719)
            for t in tickets:
                embed.add_field(
                    name=t["ticket_id"],
                    value=f"Opened by: <@{t.get('opener_id', 'unknown')}> | Closed by: <@{t.get('closer_id', 'unknown')}>",
                    inline=False,
                )
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
        except Exception:
            print("transcriptsearch ERROR:", traceback.format_exc())

    @commands.hybrid_command(
        name="transcriptlist", description="List all tickets (open & closed) with details. Staff only."
    )
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def transcriptlist(self, ctx):
        try:
            guild_id = str(ctx.guild.id)
            closed_tickets = await state.tickets_col.find({"guild_id": guild_id}).to_list(length=200)
            open_tickets = await state.tickets_col.find({"guild": guild_id}).to_list(length=200)
            tickets = closed_tickets + [_normalize_open_ticket(t) for t in open_tickets]
            tickets.sort(key=_ticket_sort_key, reverse=True)
            tickets = tickets[:200]
            if not tickets:
                msg = "❌ No tickets found in this server."
                if errors.is_prefix(ctx):
                    await ctx.send(msg)
                else:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                return
            view = TranscriptPaginationView(ctx, tickets)
            embed = await view.build_embed()
            view.children[0].disabled = True
            view.children[1].disabled = view.max_page == 0
            if errors.is_prefix(ctx):
                msg = await ctx.send(embed=embed, view=view)
            else:
                await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                msg = await ctx.interaction.original_response()
            view.message = msg
        except Exception as e:
            print("transcriptlist ERROR:", traceback.format_exc())
            if not errors.is_prefix(ctx) and ctx.interaction and (not ctx.interaction.response.is_done()):
                await ctx.interaction.response.send_message(f"❌ Error:\n```{e}```", ephemeral=True)
            else:
                await ctx.send(f"❌ Error:\n```{e}```")

    @commands.hybrid_command(name="ticketadduser", description="Add a user to the current ticket.")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketadduser(self, ctx, member: discord.Member):
        channel = ctx.channel
        ticket_entry = await state.tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})
        if not ticket_entry:
            return await ctx.send("❌ This command can only be used inside a ticket channel.")
        try:
            overwrite = channel.overwrites_for(member)
            overwrite.view_channel = True
            overwrite.send_messages = True
            overwrite.read_message_history = True
            overwrite.embed_links = True
            overwrite.attach_files = True
            await channel.set_permissions(member, overwrite=overwrite)
            await ctx.send(f"✅ {member.mention} has been added to this ticket.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to edit channel permissions.")
        except Exception as e:
            await ctx.send(f"⚠️ Failed to add user: `{e}`")

    @commands.hybrid_command(name="ticketremoveuser", description="Remove a user from the current ticket.")
    @perms.staffperm("tickets:admin")
    @perms.staff_only()
    async def ticketremoveuser(self, ctx, member: discord.Member):
        channel = ctx.channel
        ticket_entry = await state.tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})
        if not ticket_entry:
            return await ctx.send("❌ This command can only be used inside a ticket channel.")
        try:
            await channel.set_permissions(member, overwrite=None)
            await ctx.send(f"✅ {member.mention} has been removed from this ticket.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to edit channel permissions.")
        except Exception as e:
            await ctx.send(f"⚠️ Failed to remove user: `{e}`")

    @commands.command()
    @perms.staff_only()
    async def ticketsync(self, ctx, scope: str | None = None):
        try:
            if scope and scope.lower() == "all":
                updated = 0
                docs = await state.tickets_col.find({"guild": str(ctx.guild.id)}).to_list(length=200)
                for t in docs:
                    channel_id = int(t.get("channel_id")) if t.get("channel_id") else None
                    category_name = t.get("category")
                    if not channel_id or not category_name:
                        continue
                    channel = ctx.guild.get_channel(channel_id)
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    desired = []
                    for m in await get_category_support_members(ctx.guild, category_name):
                        if await perms.has_staff_role(m, ctx.guild):
                            desired.append(m)
                    desired_ids = {m.id for m in desired}
                    for target, overwrite in channel.overwrites.items():
                        if (
                            isinstance(target, discord.Member)
                            and await perms.has_staff_role(target, ctx.guild)
                            and overwrite.view_channel
                            and target.id not in desired_ids
                        ):
                            await channel.set_permissions(target, overwrite=None)
                    for m in desired:
                        ow = channel.overwrites_for(m)
                        ow.view_channel = True
                        ow.send_messages = True
                        ow.read_message_history = True
                        ow.embed_links = True
                        ow.attach_files = True
                        await channel.set_permissions(m, overwrite=ow)
                    updated += 1
                return await ctx.send(f"✅ Synced staff access for `{updated}` open tickets.")
            channel = ctx.channel
            t = await state.tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})
            if not t:
                return await ctx.send(
                    "❌ This command can only be used inside an open ticket channel, or use `.ticketsync all`."
                )
            category_name = t.get("category")
            if not category_name:
                return await ctx.send("⚠️ Could not determine ticket category for this channel.")
            desired = []
            for m in await get_category_support_members(ctx.guild, category_name):
                if await perms.has_staff_role(m, ctx.guild):
                    desired.append(m)
            desired_ids = {m.id for m in desired}
            for target, overwrite in channel.overwrites.items():
                if (
                    isinstance(target, discord.Member)
                    and await perms.has_staff_role(target, ctx.guild)
                    and overwrite.view_channel
                    and target.id not in desired_ids
                ):
                    await channel.set_permissions(target, overwrite=None)
            for m in desired:
                ow = channel.overwrites_for(m)
                ow.view_channel = True
                ow.send_messages = True
                ow.read_message_history = True
                ow.embed_links = True
                ow.attach_files = True
                await channel.set_permissions(m, overwrite=ow)
            await ctx.send("✅ Staff access synced for this ticket.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to edit channel permissions.")
        except Exception as e:
            await ctx.send(f"⚠️ Error: `{e}`")

    @commands.Cog.listener()
    async def on_ready(self):
        """Re-attach the persistent ticket panel views after a restart (runs once)."""
        if self._ready_done:
            return
        self._ready_done = True
        panels = await state.ticket_panels_col.find({}).to_list(length=None)
        for panel in panels:
            try:
                view = TicketPanelView(panel)
                if panel.get("message_id") and panel.get("channel_id"):
                    try:
                        channel = self.bot.get_channel(int(panel["channel_id"]))
                        if channel:
                            message = await channel.fetch_message(panel["message_id"])
                            await message.edit(view=view)
                            print(f"✅ Reattached view to panel message {panel['message_id']}")
                            continue
                    except Exception as e:
                        print(f"Could not reattach view to message {panel.get('message_id')}: {e}")
                self.bot.add_view(view)
                print(f"✅ Registered global view for panel {panel.get('panel_name')}")
            except Exception as e:
                print(f"Failed to register view for {panel.get('panel_name')}: {e}")
        guilds = await state.settings_col.distinct("guild")
        for guild_id in guilds:
            panels = await state.ticket_panels_col.find({"guild": str(guild_id)}).to_list(length=50)
            for panel_data in panels:
                try:
                    view = TicketPanelView(panel_data)
                    if panel_data.get("message_id") and panel_data.get("channel_id"):
                        try:
                            channel = self.bot.get_channel(int(panel_data["channel_id"]))
                            if channel:
                                message = await channel.fetch_message(panel_data["message_id"])
                                await message.edit(view=view)
                                continue
                        except Exception as e:
                            print(f"Could not reattach guild view to message {panel_data.get('message_id')}: {e}")
                    self.bot.add_view(view)
                except Exception as e:
                    print(f"Failed to register guild view for {panel_data.get('panel_name')}: {e}")
        print("✅ Persistent ticket panel views loaded.")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle the plain-text `confirm` / `cancel` reply to a pending ticketclose request."""
        if message.author.bot:
            return
        if not message.guild:
            return
        if message.type in cfg.BOOST_MESSAGE_TYPES:
            return
        try:
            ticket_entry = await state.tickets_col.find_one(
                {"guild": str(message.guild.id), "channel_id": str(message.channel.id), "close_pending": True}
            )
            if ticket_entry:
                opener_id = int(ticket_entry.get("owner_id"))
                print(
                    f"[ticket confirmation] Checking message from {message.author.id}, ticket opener: {opener_id}, pending: {ticket_entry.get('close_pending')}"
                )
                if message.author.id == opener_id:
                    if message.content.lower() == "cancel":
                        await state.tickets_col.update_one(
                            {"_id": ticket_entry["_id"]}, {"$set": {"close_pending": False}}
                        )
                        await message.channel.send("❌ Ticket close request canceled.")
                        return
                    if message.content.lower() == "confirm":
                        opener = await self.resolve_ticket_opener(message.guild, opener_id)
                        if opener:

                            class DummyCtx:
                                def __init__(self, channel, author):
                                    self.channel = channel
                                    self.author = message.author
                                    self.guild = message.guild

                            ctx = DummyCtx(message.channel, message.author)
                            await actually_close_ticket(ctx, opener, forced=False)
                            await state.tickets_col.delete_one({"_id": ticket_entry["_id"]})
                            await message.channel.send("✅ Ticket has been closed.")
                            return
                        else:
                            await message.channel.send("⚠️ Could not find ticket opener in server.")
                            print(f"[ticket confirmation] Could not find opener {opener_id} in guild")
                else:
                    print(
                        f"[ticket confirmation] Non opener {message.author.id} tried to confirm ticket {ticket_entry['_id']}"
                    )
        except Exception as e:
            print(f"[ticket confirmation error] {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
