"""Help and tutorial views.

The help system has three layers:

* ``CommandPages`` — tabs for General/Economy/Staff plus prev/next paging
  inside each section.
* ``StaffSections`` — secondary view listing every staff command, opened
  from the Staff button on the main view.
* ``TutorialPages`` — guided walkthrough for new admins.
"""

from __future__ import annotations

import discord
from discord.ui import View

from bot.database import settings_col


class StaffSections(discord.ui.View):
    """Per-category staff command listing."""

    def __init__(self, prefix: str) -> None:
        super().__init__(timeout=300)
        self.prefix = prefix

    @discord.ui.button(label="⚔️ Moderation", style=discord.ButtonStyle.secondary)
    async def moderation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="⚔️ Moderation Commands", color=discord.Color.red())
        embed.add_field(name=f"{self.prefix}kick @user [reason]", value="Kick a member from the server.", inline=False)
        embed.add_field(name=f"{self.prefix}ban @user [reason]", value="Ban a member from the server.", inline=False)
        embed.add_field(name=f"{self.prefix}unban <user_id>", value="Unban a previously banned user.", inline=False)
        embed.add_field(name=f"{self.prefix}mute @user <time> [reason]", value="Temporarily mute a member.", inline=False)
        embed.add_field(name=f"{self.prefix}unmute @user", value="Unmute a previously muted member.", inline=False)
        embed.add_field(name=f"{self.prefix}warn @user [reason]", value="Issue a warning to a user.", inline=False)
        embed.add_field(name=f"{self.prefix}clearwarns @user", value="Clear all warnings for a user.", inline=False)
        embed.add_field(name=f"{self.prefix}purge <amount>", value="Bulk delete a number of messages.", inline=False)
        embed.add_field(name=f"{self.prefix}slowmode <seconds>", value="Set a slowmode timer for the current channel.", inline=False)
        embed.add_field(name=f"{self.prefix}blacklist @user", value="Blacklist a user from using bot commands.", inline=False)
        embed.add_field(name=f"{self.prefix}whitelist @user", value="Remove a user from the blacklist.", inline=False)
        embed.set_footer(text="Moderation - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🎟 Tickets", style=discord.ButtonStyle.secondary)
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="🎟 Ticket Commands", color=discord.Color.blurple())
        embed.add_field(name=f"{self.prefix}ticketsetup", value="Create an interactive ticket panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketdeletepanel <name>", value="Delete a saved panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketlist", value="List saved panels.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketforceclose", value="Force close ticket.", inline=False)
        embed.add_field(name=f"{self.prefix}transcriptsearch <username>", value="Search transcripts by username.", inline=False)
        embed.add_field(name=f"{self.prefix}transcriptlist", value="List all tickets (open & closed) with details.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketaddbutton", value="Add a ticket button to an existing panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketeditbutton", value="Edit a ticket button from an existing panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketpanel <name>", value="Post a saved ticket panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketclose", value="Request to close a ticket.", inline=False)
        embed.add_field(name=f"{self.prefix}transcript <id>", value="Fetch a saved transcript of a ticket.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketadduser @user", value="Add a user to the current ticket.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketremoveuser @user", value="Remove a user from the current ticket.", inline=False)
        embed.set_footer(text="Tickets - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🗒 StickyNotes", style=discord.ButtonStyle.secondary)
    async def stickynotes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="🗒 Sticky Note Commands", color=discord.Color.yellow())
        embed.add_field(name=f"{self.prefix}stickynote", value="Set a sticky note in the current channel.", inline=False)
        embed.add_field(name=f"{self.prefix}unstickynote", value="Remove the sticky note from this channel.", inline=False)
        embed.set_footer(text="StickyNotes - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="💰 Economy", style=discord.ButtonStyle.secondary)
    async def economy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="💰 Economy Commands", color=discord.Color.green())
        embed.add_field(
            name=f"{self.prefix}additem \"<name>\" <price>",
            value="Add a new item to the shop. `/additem` uses separate name and price boxes.",
            inline=False,
        )
        embed.add_field(name=f"{self.prefix}edititem <item> <price> <desc>", value="Edit an existing shop item.", inline=False)
        embed.add_field(name=f"{self.prefix}delitem <item>", value="Delete a shop item.", inline=False)
        embed.add_field(name=f"{self.prefix}drop <amount> <message>", value="Drop a random coin reward in chat.", inline=False)
        embed.add_field(
            name=f"{self.prefix}addmoney @user <amount>",
            value="Add money to a user’s balance. PREFIX ONLY.",
            inline=False,
        )
        embed.add_field(
            name=f"{self.prefix}removemoney @user <amount>",
            value="Remove money from a user's balance. PREFIX ONLY.",
            inline=False,
        )
        embed.set_footer(text="Economy - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="✨ Vanity", style=discord.ButtonStyle.secondary)
    async def vanity(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="✨ Vanity Commands", color=discord.Color.magenta())
        embed.add_field(name=f"{self.prefix}vanityroles @role #log <status>", value="Set up vanity roles.", inline=False)
        embed.add_field(name=f"{self.prefix}promoters", value="View vanity users.", inline=False)
        embed.add_field(name=f"{self.prefix}resetpromoters", value="Clear all vanity users.", inline=False)
        embed.set_footer(text="Vanity - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🎭 Roles", style=discord.ButtonStyle.secondary)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="🎭 Role Commands", color=discord.Color.magenta())
        embed.add_field(name=f"{self.prefix}roleadd @role", value="Add a claimable role to /roles.", inline=False)
        embed.add_field(name=f"{self.prefix}roleremove @role", value="Remove a claimable role from /roles.", inline=False)
        embed.set_footer(text="Roles - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⚙️ Config", style=discord.ButtonStyle.secondary)
    async def config(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="⚙️ Config Commands", color=discord.Color.orange())
        embed.add_field(name=f"{self.prefix}configure", value="Open configuration setup menu.", inline=False)
        embed.add_field(name=f"{self.prefix}viewconfig", value="View current configuration settings.", inline=False)
        embed.add_field(name=f"{self.prefix}editconfig", value="Edit specific configuration values.", inline=False)
        embed.add_field(
            name=f"{self.prefix}resetconfig",
            value="Reset configuration values to default. PREFIX ONLY.",
            inline=False,
        )
        embed.add_field(name=f"{self.prefix}setprefix <prefix>", value="Change the bot’s command prefix.", inline=False)
        embed.set_footer(text="Config - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📨 Invites", style=discord.ButtonStyle.secondary)
    async def invites(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="📨 Invite Commands", color=discord.Color.teal())
        embed.add_field(name=f"{self.prefix}invitechannel #channel", value="Set the invite logging channel.", inline=False)
        embed.add_field(name=f"{self.prefix}invites @user", value="Check how many invites a user has.", inline=False)
        embed.add_field(
            name=f"{self.prefix}removeinvites @user <amount>",
            value="Remove a specific number of invites from a user.",
            inline=False,
        )
        embed.set_footer(text="Invites - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📦 Other", style=discord.ButtonStyle.secondary)
    async def other(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title="📦 Other Staff Commands", color=discord.Color.light_gray())
        embed.add_field(name=f"{self.prefix}giveaway", value="Start a giveaway setup wizard.", inline=False)
        embed.add_field(name=f"{self.prefix}reroll <id>", value="Reroll a completed giveaway winner.", inline=False)
        embed.add_field(name=f"{self.prefix}disable <cmd/category>", value="Disable a specific command or category.", inline=False)
        embed.add_field(name=f"{self.prefix}enable <cmd/category>", value="Enable a disabled command or category.", inline=False)
        embed.add_field(name=f"{self.prefix}listdisabled", value="List all currently disabled commands.", inline=False)
        embed.add_field(
            name=f"{self.prefix}stop",
            value="Lock the bot, only custom prefix, only thetruck and server owner can unlock.",
            inline=False,
        )
        embed.add_field(
            name=f"{self.prefix}testwelcome @user",
            value="Test welcome channel from a specified user. PREFIX ONLY.",
            inline=False,
        )
        embed.add_field(
            name=f"{self.prefix}testboost @user",
            value="Test boost channel from a specified user. PREFIX ONLY.",
            inline=False,
        )
        embed.add_field(name=f"{self.prefix}reactionrole <msg_id> <emoji> @role", value="Set up a reaction role.", inline=False)
        embed.add_field(
            name=f"{self.prefix}onetime #channel",
            value="Set up a one-time message channel. PREFIX ONLY.",
            inline=False,
        )
        embed.add_field(
            name=f"{self.prefix}restore @user #channel",
            value="Restore messaging permissions for a user in a one-time channel. PREFIX ONLY.",
            inline=False,
        )
        embed.add_field(
            name=f"{self.prefix}disableonetime #channel",
            value="Disable one-time message restrictions for a channel. PREFIX ONLY.",
            inline=False,
        )
        embed.add_field(
            name=f"{self.prefix}performance",
            value="View staff performance analytics and statistics.",
            inline=False,
        )
        embed.set_footer(text="Other - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="↩️ Back", style=discord.ButtonStyle.danger)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="🦆 DuckParadise Help Menu",
            description=(
                "Use the buttons below to navigate through command categories.\n\n"
                "💬 **General** - Normal commands for everyone\n"
                "💰 **Economy** - Fun currency commands\n"
                "🛠️ **Staff** - Admin & mod tools (staff only)"
            ),
            color=discord.Color.orange(),
        )

        pages = getattr(interaction.client, "help_pages", None)
        if not pages:
            await interaction.response.send_message(
                "❌ Help menu data not found. Please report this to staff or thetruck.",
                ephemeral=True,
            )
            return

        doc = await settings_col.find_one({"guild": str(interaction.guild.id)})
        staff_role = (
            interaction.guild.get_role(doc.get("staff_role")) if doc else None
        )
        is_staff = staff_role in interaction.user.roles if staff_role else False

        view = CommandPages(pages, is_staff)
        await interaction.response.edit_message(embed=pages[0], view=view)


class CommandPages(discord.ui.View):
    """Top-level help view with section selectors and prev/next paging."""

    def __init__(self, embeds: list[discord.Embed], is_staff: bool) -> None:
        super().__init__(timeout=300)
        self.embeds = embeds
        self.is_staff = is_staff
        self.current = 0

        # Map section start indices to a label so paging knows the bounds.
        self.sect: dict[int, str] = {}
        for index, embed in enumerate(self.embeds):
            title = embed.title or ""
            if title.startswith("💬") and "General" not in self.sect.values():
                self.sect[index] = "General"
            elif title.startswith("💰") and "Economy" not in self.sect.values():
                self.sect[index] = "Economy"
            elif title.startswith("🛠️") and self.is_staff and "Staff" not in self.sect.values():
                self.sect[index] = "Staff"

    def _get_section_bounds(self) -> tuple[int, int]:
        starts = sorted(self.sect)
        idx = max(k for k in starts if k <= self.current)
        start = idx
        next_idx = [k for k in starts if k > idx]
        end = next_idx[0] if next_idx else len(self.embeds)
        return start, end

    def _update_nav_buttons(self) -> None:
        for btn in (self.prev_button, self.next_button):
            if btn in self.children:
                self.remove_item(btn)
        start, end = self._get_section_bounds()
        if end - start <= 1:
            return
        if self.current > start:
            self.add_item(self.prev_button)
        if self.current < end - 1:
            self.add_item(self.next_button)

    @discord.ui.button(label="💬 General", style=discord.ButtonStyle.secondary)
    async def general(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        general_idx = next(
            (idx for idx, name in self.sect.items() if name == "General"), 0
        )
        self.current = general_idx
        self._update_nav_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.current], view=self
        )

    @discord.ui.button(label="💰 Economy", style=discord.ButtonStyle.success)
    async def economy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        econ_idx = next(
            (i for i, e in enumerate(self.embeds) if (e.title or "").startswith("💰")),
            None,
        )
        if econ_idx is not None:
            self.current = econ_idx
            self._update_nav_buttons()
            await interaction.response.edit_message(
                embed=self.embeds[self.current], view=self
            )
        else:
            await interaction.response.send_message(
                "❌ No economy pages found.", ephemeral=True
            )

    @discord.ui.button(label="🛠️ Staff", style=discord.ButtonStyle.danger)
    async def staff(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.is_staff:
            await interaction.response.send_message(
                "❌ You don’t have permission to view staff commands.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="🛠️ Staff Command Sections",
            description="Select a category below to view its commands.",
            color=discord.Color.orange(),
        )
        await interaction.response.edit_message(embed=embed, view=StaffSections("?"))

    @discord.ui.button(
        label="⏮ Prev",
        style=discord.ButtonStyle.secondary,
        custom_id="prev_button_unique",
    )
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        start, _ = self._get_section_bounds()
        if self.current > start:
            self.current -= 1
            self._update_nav_buttons()
            await interaction.response.edit_message(
                embed=self.embeds[self.current], view=self
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(
        label="⏭ Next",
        style=discord.ButtonStyle.secondary,
        custom_id="next_button_unique",
    )
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _, end = self._get_section_bounds()
        if self.current < end - 1:
            self.current += 1
            self._update_nav_buttons()
            await interaction.response.edit_message(
                embed=self.embeds[self.current], view=self
            )
        else:
            await interaction.response.defer()

    async def on_timeout(self) -> None:  # pragma: no cover
        for child in self.children:
            child.disabled = True


class TutorialPages(discord.ui.View):
    """Multi-section tutorial walkthrough."""

    def __init__(self, pages: list[discord.Embed]) -> None:
        super().__init__(timeout=300)
        self.pages = pages
        self.current = 0

    async def _switch(self, interaction: discord.Interaction, index: int) -> None:
        self.current = index
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @discord.ui.button(label="🏠 Intro", style=discord.ButtonStyle.primary)
    async def intro(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 0)

    @discord.ui.button(label="🧭 Setup Order", style=discord.ButtonStyle.secondary)
    async def setuporder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 1)

    @discord.ui.button(label="💰 Economy", style=discord.ButtonStyle.secondary)
    async def economy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 2)

    @discord.ui.button(label="⚔️ Moderation", style=discord.ButtonStyle.secondary)
    async def moderation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 3)

    @discord.ui.button(label="🎟 Tickets", style=discord.ButtonStyle.secondary)
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 4)

    @discord.ui.button(label="⚙️ Config", style=discord.ButtonStyle.secondary)
    async def config(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 5)

    @discord.ui.button(label="🗒 StickyNotes", style=discord.ButtonStyle.secondary)
    async def stickynotes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 6)

    @discord.ui.button(label="📨 Invites", style=discord.ButtonStyle.secondary)
    async def invites(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 7)

    @discord.ui.button(label="✨ Vanity", style=discord.ButtonStyle.secondary)
    async def vanity(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 8)

    @discord.ui.button(label="🎭 Roles", style=discord.ButtonStyle.secondary)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 9)

    @discord.ui.button(label="📦 Other", style=discord.ButtonStyle.secondary)
    async def other(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._switch(interaction, 10)

    async def on_timeout(self) -> None:  # pragma: no cover
        for child in self.children:
            child.disabled = True


__all__ = ["StaffSections", "CommandPages", "TutorialPages"]
