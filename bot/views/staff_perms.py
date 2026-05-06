"""Views and modals for the staff-permissions admin UI."""

from __future__ import annotations

import asyncio

import discord
from discord import SelectOption, ui

from bot.config.constants import PERMISSION_COMMAND_MAP
from bot.database import staffperms_col, ticket_panels_col


def format_permission_details(permissions: list[str]) -> str:
    """Render a permission list with the commands each grant covers."""
    if not permissions:
        return "No permissions"

    final = ""
    for permission in permissions:
        cmds = PERMISSION_COMMAND_MAP.get(permission, ["Unknown"])
        cmds_text = ", ".join(cmds)
        final += f"**• {permission}** — `{cmds_text}`\n"
    return final


class StaffPermissionSelect(ui.Select):
    """Multi-select dropdown that writes the choice back to ``staffperms_col``."""

    def __init__(
        self,
        member: discord.Member,
        staffperms_collection,
        guild_id: int,
        author_id: int,
        parent_view: ui.View,
    ) -> None:
        self.member = member
        self.staffperms_col = staffperms_collection
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

    async def load_options(self, message: discord.Message | None = None) -> None:
        base_options = [
            SelectOption(label="Kick", value="kick", description="Use the kick command"),
            SelectOption(label="Ban", value="ban", description="Use the ban command"),
            SelectOption(label="Mute", value="mute", description="Use the mute/unmute commands"),
            SelectOption(label="Stop Bot", value="stopbot", description="Lock the bot from responding"),
            SelectOption(label="Money Drop", value="money_drop", description="Use the drop command"),
            SelectOption(label="Other Moderation", value="other_moderation", description="warn / purge / slowmode / fine etc."),
        ]

        ticket_options = [
            SelectOption(
                label="Ticket Admin",
                value="tickets:admin",
                description="Manage ticket panels and admin actions",
            )
        ]

        categories: dict[str, dict] = {}
        cursor = ticket_panels_col.find({"guild": str(self.guild_id)})
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
                    label="All Ticket Types",
                    value="tickets:all",
                    description="Access to ALL ticket categories",
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
            except Exception:
                pass

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who ran the command can use this menu.",
                ephemeral=True,
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
            description=(
                f"{self.member.mention} has been updated:\n"
                f"{perms_text}\n\n"
                "You can change selections at any time; this menu does not expire."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Configured by {interaction.user} • User ID: {self.member.id}")

        try:
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
        except Exception:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class StaffPermissionView(ui.View):
    """Wrap the select so callers only need to add this view."""

    def __init__(self, member, staffperms_collection, guild_id, author_id) -> None:
        super().__init__(timeout=None)
        self.message: discord.Message | None = None
        self.select = StaffPermissionSelect(
            member, staffperms_collection, guild_id, author_id, self
        )
        self.add_item(self.select)

    async def initialize(self, message: discord.Message) -> None:
        self.message = message
        await self.select.load_options(message)


class ViewPermsSearchModal(ui.Modal, title="Search User"):
    """Modal triggered from the pagination view to jump to a specific user."""

    def __init__(self, view_ref) -> None:
        super().__init__()
        self.view_ref = view_ref
        self.username = ui.TextInput(
            label="Enter user ID",
            placeholder="Example: 1234567890123",
            required=True,
        )
        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = self.username.value.lower()

        for index, embed in enumerate(self.view_ref.pages):
            user_field = embed.fields[0].value
            if query in user_field.lower():
                self.view_ref.index = index
                await interaction.response.edit_message(
                    embed=self.view_ref.pages[index], view=self.view_ref
                )
                return

        await interaction.response.send_message(
            "❌ Could not find that user.", ephemeral=True
        )


class ViewPermsView(discord.ui.View):
    """Pagination buttons + a search button for the ``viewperms`` listing."""

    def __init__(self, pages: list[discord.Embed], author_id: int) -> None:
        super().__init__(timeout=120)
        self.pages = pages
        self.author_id = author_id
        self.index = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the command user can use this menu.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = 0
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.index > 0:
            self.index -= 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.index < len(self.pages) - 1:
            self.index += 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.primary)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ViewPermsSearchModal(self))


__all__ = [
    "format_permission_details",
    "StaffPermissionSelect",
    "StaffPermissionView",
    "ViewPermsView",
    "ViewPermsSearchModal",
]
