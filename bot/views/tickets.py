"""All ticket-system Discord UI components.

Includes setup/edit modals, the public panel view, and the per-ticket
edit-and-delete sub-view. Persistent ``custom_id`` strings are essential
here because we want buttons to keep working after a bot restart.
"""

from __future__ import annotations

import re
import traceback
from datetime import datetime, timezone

import discord

from bot.database import (
    settings_col,
    ticket_panels_col,
    tickets_col,
    tickets_counter_col,
)
from bot.utils.members import get_category_support_members
from bot.utils.tickets import ping_ticket_roles, ticket_error


class TicketSetupModal(discord.ui.Modal, title="Create Ticket Panel"):
    """Modal triggered by ``/ticketsetup`` to create a new panel."""

    panel_name = discord.ui.TextInput(
        label="Panel Name", placeholder="Example: SupportPanel1", required=True
    )
    embed_title = discord.ui.TextInput(
        label="Embed Title", placeholder="Example: 🎫 Need Help?", required=True
    )
    embed_desc = discord.ui.TextInput(
        label="Embed Description",
        placeholder="Click a button below to create a ticket.",
        required=True,
        style=discord.TextStyle.paragraph,
    )
    embed_color = discord.ui.TextInput(
        label="Embed Color (hex)", placeholder="#5865F2", required=False
    )

    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction) -> None:
        guild = self.ctx.guild
        data = await settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None

        if not staff_role_id or staff_role_id not in [r.id for r in self.ctx.author.roles]:
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only staff members can use this command.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        color_value = (
            int(self.embed_color.value.replace("#", ""), 16)
            if self.embed_color.value
            else 0x5865F2
        )
        await ticket_panels_col.insert_one(
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
            description=(
                f"Panel `{self.panel_name.value}` created successfully!\n"
                "Use `/ticketaddbutton` to add buttons."
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class TicketAddButtonModal(discord.ui.Modal, title="Add Ticket Panel Button"):
    panel_name = discord.ui.TextInput(label="Panel Name", placeholder="Example: SupportPanel1", required=True)
    category_name = discord.ui.TextInput(label="Ticket Category", placeholder="Example: Support", required=True)
    button_label = discord.ui.TextInput(label="Button Label", placeholder="Example: Open Support Ticket", required=True)
    emoji = discord.ui.TextInput(label="Emoji (optional)", placeholder="Example: 🎫", required=False)

    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction) -> None:
        guild = self.ctx.guild
        data = await settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None

        if not staff_role_id or staff_role_id not in [r.id for r in self.ctx.author.roles]:
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only staff members can use this command.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        panel_data = await ticket_panels_col.find_one(
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
        await ticket_panels_col.update_one(
            {"guild": str(guild.id), "panel_name": self.panel_name.value},
            {"$push": {"buttons": new_button}},
        )

        embed = discord.Embed(
            title="✅ Button Added",
            description=(
                f"Added button to panel `{self.panel_name.value}`:\n"
                f"{self.emoji.value or ''} **{self.button_label.value}** "
                f"→ Category `{self.category_name.value}`"
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class TicketEditButtonModal(discord.ui.Modal, title="Edit Ticket Panel Button"):
    category_name = discord.ui.TextInput(label="Ticket Category", required=True)
    button_label = discord.ui.TextInput(label="Button Label", required=True)
    emoji = discord.ui.TextInput(label="Emoji (optional)", required=False)

    def __init__(self, ctx, panel_name, btn_data) -> None:
        super().__init__()
        self.ctx = ctx
        self.panel_name = panel_name
        self.btn_data = btn_data
        self.category_name.default = btn_data.get("category_name", "")
        self.button_label.default = btn_data.get("label", "")
        self.emoji.default = btn_data.get("emoji", "")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction) -> None:
        guild = self.ctx.guild
        await ticket_panels_col.update_one(
            {
                "guild": str(guild.id),
                "panel_name": self.panel_name,
                "buttons.label": self.btn_data["label"],
            },
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
            description=(
                f"Updated button in panel `{self.panel_name}`:\n"
                f"{self.emoji.value or ''} **{self.button_label.value}** "
                f"→ Category `{self.category_name.value}`"
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class TicketButtonActionView(discord.ui.View):
    """Edit/Delete buttons shown after picking a panel button to manage."""

    def __init__(self, ctx, panel_name, btn_data) -> None:
        super().__init__(timeout=60)
        self.ctx = ctx
        self.panel_name = panel_name
        self.btn_data = btn_data

    @discord.ui.button(label="✏ Edit", style=discord.ButtonStyle.blurple)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ticket_error(interaction, lambda: self._edit(interaction))

    async def _edit(self, interaction: discord.Interaction) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can edit.",
                ephemeral=True,
            )
        modal = TicketEditButtonModal(self.ctx, self.panel_name, self.btn_data)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑 Delete", style=discord.ButtonStyle.red)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ticket_error(interaction, lambda: self._delete(interaction))

    async def _delete(self, interaction: discord.Interaction) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can delete.",
                ephemeral=True,
            )

        guild = self.ctx.guild
        await ticket_panels_col.update_one(
            {"guild": str(guild.id), "panel_name": self.panel_name},
            {"$pull": {"buttons": {"label": self.btn_data["label"]}}},
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                title="🗑 Button Deleted",
                description=(
                    f"Removed **{self.btn_data['label']}** from panel "
                    f"`{self.panel_name}`."
                ),
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        self.stop()


class TicketEditButton(discord.ui.Button):
    """Button shown inside ``TicketPanelEditView`` for each existing button."""

    def __init__(self, btn_data, panel_data, ctx) -> None:
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

    async def callback(self, interaction: discord.Interaction) -> None:
        await ticket_error(interaction, lambda: self._callback(interaction))

    async def _callback(self, interaction: discord.Interaction) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can manage buttons.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"⚙ Manage Button: {self.btn_data.get('label', 'Unnamed')}",
                description="Choose what you want to do with this button.",
                color=discord.Color.orange(),
            ),
            view=TicketButtonActionView(
                self.ctx, self.panel_data["panel_name"], self.btn_data
            ),
            ephemeral=True,
        )


class TicketPanelEditView(discord.ui.View):
    """One ``TicketEditButton`` per existing button on a panel."""

    def __init__(self, ctx, panel_data) -> None:
        super().__init__(timeout=None)
        self.ctx = ctx
        self.panel_data = panel_data
        for btn in panel_data.get("buttons", []):
            self.add_item(TicketEditButton(btn, panel_data, ctx))


class TicketCategoryButton(discord.ui.Button):
    """Public-facing button that opens a new ticket on click."""

    def __init__(self, btn_data, panel_data) -> None:
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

    async def callback(self, interaction: discord.Interaction) -> None:
        await ticket_error(interaction, lambda: self.create_ticket(interaction))

    async def create_ticket(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        author = interaction.user

        counter_doc = await tickets_counter_col.find_one({"guild": str(guild.id)})
        if not counter_doc:
            ticket_number = 1
            await tickets_counter_col.insert_one(
                {"guild": str(guild.id), "counter": ticket_number}
            )
        else:
            ticket_number = counter_doc["counter"] + 1
            await tickets_counter_col.update_one(
                {"guild": str(guild.id)}, {"$set": {"counter": ticket_number}}
            )

        safe_username = re.sub(r"[^a-zA-Z0-9_-]", "", author.name).lower()
        safe_label = (
            re.sub(r"[^a-zA-Z0-9_-]", "", self.btn_data["label"])
            .replace(" ", "-")
            .lower()
        )
        ticket_name = f"{safe_username}-{safe_label}"

        if len(ticket_name) > 90:
            available = 90 - (len(safe_username) + 1)
            if available < 1:
                safe_username = safe_username[:45]
                safe_label = safe_label[:44]
            else:
                safe_label = safe_label[:available]
            ticket_name = f"{safe_username}-{safe_label}"

        data = await settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None
        if not staff_role_id:
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Staff Role Not Set",
                    description="Use `/staffset` first.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        # Locate or create the parent "Tickets" category.
        category = (
            discord.utils.get(guild.categories, name="Tickets")
            or await guild.create_category("Tickets")
        )

        for c in category.channels:
            if c.name.lower() == ticket_name.lower():
                return await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Duplicate Ticket",
                        description=(
                            f"A ticket with that name already exists: {c.mention}"
                        ),
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )

        category_name = self.btn_data["category_name"].lower()
        category_support_members = await get_category_support_members(
            guild, category_name
        )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False, embed_links=True, attach_files=True
            ),
            author: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            ),
        }

        for member in category_support_members:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            )

        channel = await guild.create_text_channel(
            ticket_name, category=category, overwrites=overwrites
        )

        await tickets_col.insert_one(
            {
                "guild": str(guild.id),
                "channel_id": str(channel.id),
                "owner_id": str(author.id),
                "category": category_name.lower(),
                "created_at": datetime.now(timezone.utc),
            }
        )

        embed = discord.Embed(
            title="🎟️ Ticket Created",
            description=(
                "Please state your concern and the staff team will respond soon."
            ),
            color=discord.Color(self.panel_data.get("ticket_embed_color", 0x5865F2)),
        )

        await channel.send(embed=embed)
        await ping_ticket_roles(channel, guild.id)

        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Ticket Created!",
                description=(
                    f"Your ticket was successfully created!\n"
                    f"Here it is: {channel.mention}"
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class TicketPanelView(discord.ui.View):
    """The public-facing panel — adds one TicketCategoryButton per stored button."""

    def __init__(self, panel_data) -> None:
        super().__init__(timeout=None)
        self.panel_data = panel_data
        for btn in panel_data.get("buttons", []):
            self.add_item(TicketCategoryButton(btn, panel_data))


__all__ = [
    "TicketSetupModal",
    "TicketAddButtonModal",
    "TicketEditButtonModal",
    "TicketButtonActionView",
    "TicketEditButton",
    "TicketPanelEditView",
    "TicketCategoryButton",
    "TicketPanelView",
]
