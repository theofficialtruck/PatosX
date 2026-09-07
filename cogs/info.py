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

"""Informational commands: serverinfo, tutorial and the paginated help menu."""

import inspect

import discord
from discord.ext import commands

from core import config as cfg
from core import state


class TutorialPages(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=300)
        self.pages = pages
        self.current = 0

    async def switch(self, interaction, index):
        self.current = index
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @discord.ui.button(label="🏠 Intro", style=discord.ButtonStyle.primary)
    async def intro(self, interaction, button):
        await self.switch(interaction, 0)

    @discord.ui.button(label="🧭 Setup Order", style=discord.ButtonStyle.secondary)
    async def setuporder(self, interaction, button):
        await self.switch(interaction, 1)

    @discord.ui.button(label="💰 Economy", style=discord.ButtonStyle.secondary)
    async def economy(self, interaction, button):
        await self.switch(interaction, 2)

    @discord.ui.button(label="⚔️ Moderation", style=discord.ButtonStyle.secondary)
    async def moderation(self, interaction, button):
        await self.switch(interaction, 3)

    @discord.ui.button(label="🎟 Tickets", style=discord.ButtonStyle.secondary)
    async def tickets(self, interaction, button):
        await self.switch(interaction, 4)

    @discord.ui.button(label="⚙️ Config", style=discord.ButtonStyle.secondary)
    async def config(self, interaction, button):
        await self.switch(interaction, 5)

    @discord.ui.button(label="🗒 StickyNotes", style=discord.ButtonStyle.secondary)
    async def stickynotes(self, interaction, button):
        await self.switch(interaction, 6)

    @discord.ui.button(label="📨 Invites", style=discord.ButtonStyle.secondary)
    async def invites(self, interaction, button):
        await self.switch(interaction, 7)

    @discord.ui.button(label="✨ Vanity", style=discord.ButtonStyle.secondary)
    async def vanity(self, interaction, button):
        await self.switch(interaction, 8)

    @discord.ui.button(label="🎭 Roles", style=discord.ButtonStyle.secondary)
    async def roles(self, interaction, button):
        await self.switch(interaction, 9)

    @discord.ui.button(label="📦 Other", style=discord.ButtonStyle.secondary)
    async def other(self, interaction, button):
        await self.switch(interaction, 10)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class CommandPages(discord.ui.View):
    def __init__(self, embeds, is_staff: bool, prefix: str):
        super().__init__(timeout=300)
        self.embeds = embeds
        self.is_staff = is_staff
        self.prefix = prefix
        self.current = 0
        self.sect = {0: "General", 1: "Economy"}
        if is_staff:
            staff_idx = next((i for i, e in enumerate(embeds) if e.title.startswith("🛠️")), None)
            if staff_idx is not None:
                self.sect[staff_idx] = "Staff"
        self.sect = {}
        for idx, embed in enumerate(self.embeds):
            if embed.title.startswith("💬") and "General" not in self.sect.values():
                self.sect[idx] = "General"
            elif embed.title.startswith("💰") and "Economy" not in self.sect.values():
                self.sect[idx] = "Economy"
            elif embed.title.startswith("🛠️") and self.is_staff and ("Staff" not in self.sect.values()):
                self.sect[idx] = "Staff"

    def get_section_bounds(self):
        starts = sorted(self.sect)
        idx = max(k for k in starts if k <= self.current)
        start = idx
        next_idx = [k for k in starts if k > idx]
        end = next_idx[0] if next_idx else len(self.embeds)
        return (start, end)

    def update_nav_buttons(self):
        existing_ids = {"prev_button_unique", "next_button_unique"}
        for child in list(self.children):
            try:
                cid = getattr(child, "custom_id", None)
            except Exception:
                cid = None
            if cid in existing_ids:
                self.remove_item(child)
        start, end = self.get_section_bounds()
        if end - start <= 1:
            return
        if self.current > start:
            self.add_item(self.prev_button)
        if self.current < end - 1:
            self.add_item(self.next_button)

    @discord.ui.button(label="💬 General", style=discord.ButtonStyle.secondary)
    async def general(self, interaction: discord.Interaction, button: discord.ui.Button):
        general_idx = next((idx for idx, name in self.sect.items() if name == "General"), 0)
        self.current = general_idx
        self.update_nav_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)

    @discord.ui.button(label="💰 Economy", style=discord.ButtonStyle.success)
    async def economy(self, interaction: discord.Interaction, button: discord.ui.Button):
        econ_idx = next((i for i, e in enumerate(self.embeds) if e.title.startswith("💰")), None)
        if econ_idx is not None:
            self.current = econ_idx
            self.update_nav_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
        else:
            await interaction.response.send_message("❌ No economy pages found.", ephemeral=True)

    @discord.ui.button(label="🛠️ Staff", style=discord.ButtonStyle.danger)
    async def staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_staff:
            await interaction.response.send_message(
                "❌ You don't have permission to view staff commands.", ephemeral=True
            )
            return
        staff_idx = next((i for i, e in enumerate(self.embeds) if e.title.startswith("🛠️")), None)
        if staff_idx is None:
            return await interaction.response.send_message("❌ No staff pages found.", ephemeral=True)
        self.current = staff_idx
        self.update_nav_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary, custom_id="prev_button_unique")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        start, _ = self.get_section_bounds()
        if self.current > start:
            self.current -= 1
            self.update_nav_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary, custom_id="next_button_unique")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, end = self.get_section_bounds()
        if self.current < end - 1:
            self.current += 1
            self.update_nav_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
        else:
            await interaction.response.defer()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    async def handle_error(self, interaction, exception):
        try:
            await interaction.response.send_message(
                f"❌ An error occurred: `{type(exception).__name__}: {exception}`", ephemeral=True
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                f"❌ An error occurred: `{type(exception).__name__}: {exception}`", ephemeral=True
            )
        except Exception as e:
            print(f"[app_command_error] fallback failed: {e}")


class Info(commands.Cog):
    """Informational commands: serverinfo, tutorial and the paginated help menu."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="serverinfo", description="View server information")
    async def serverinfo(self, ctx):
        """Display a detailed embed with server statistics including member counts, channels, and boosts."""
        guild = ctx.guild
        embed = discord.Embed(title=f"📜 Server Information: {guild.name}", color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="👥 Members", value=f"{guild.member_count:,}", inline=True)
        embed.add_field(name="🆔 Server ID", value=guild.id, inline=True)
        embed.add_field(name="📅 Created On", value=guild.created_at.strftime("%B %d, %Y"), inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="tutorial", description="Learn how to use each bot system.")
    async def tutorial(self, ctx):
        """Send a paginated tutorial embed showing current server configuration and how to use each feature."""
        settings = await state.settings_col.find_one({"guild": str(ctx.guild.id)}) or {}
        config = await state.config_col.find_one({"guild": str(ctx.guild.id)}) or {}
        invite_cfg = await state.invite_config_col.find_one({"guild_id": str(ctx.guild.id)}) or {}
        prefix = settings.get("prefix", "?") if settings else "?"
        staff_role = settings.get("staff_role") if settings else None
        # .configure saves the log channel in config_col; the settings_col value is legacy
        log_channel = config.get("log_channel") or (settings.get("log_channel") if settings else None)
        invite_log = invite_cfg.get("channel_id") if invite_cfg else None
        ticket_panels = await state.ticket_panels_col.count_documents({"guild": str(ctx.guild.id)})
        shop_items = await state.shop_col.count_documents({})
        sticky_notes = await state.sticky_col.count_documents({"guild": str(ctx.guild.id)})
        missing = []
        if not staff_role:
            missing.append("• **Staff role not set**")
        if not log_channel:
            missing.append("• **Logging channel not set**")
        if ticket_panels == 0:
            missing.append("• **No ticket panels created**")
        if shop_items == 0:
            missing.append("• **Economy shop is empty**")
        if sticky_notes == 0:
            missing.append("• **No sticky notes created**")
        if not invite_log:
            missing.append("• **Invite logging not configured**")
        missing_block = "\n".join(missing) if missing else "🎉 All core systems are configured!"
        pages = []

        def bar(index, total=10):
            filled = "█" * index
            empty = "░" * (total - index)
            return f"{filled}{empty} **{index}/{total}**"

        intro = discord.Embed(
            title="📚 Bot Tutorial - How Everything Works",
            description=f"{bar(1)}\n\nWelcome to the full system tutorial! This menu guides you through every bot feature.\nUse the navigation buttons to browse each category.\n\nYour server prefix is: **{prefix}**\n\n**Setup Status:**\n{missing_block}\n\n**Starter Commands:**\n• `{prefix}help`\n• `{prefix}configure`\n",
            color=discord.Color.blue(),
        )
        pages.append(intro)
        setup_order = discord.Embed(
            title="🧭 Recommended Setup Order",
            description=f"{bar(2)}\n\n**Best setup order for a fresh server:**\n1. Config -> Prefix, staff role, logs\n2. Moderation -> Make sure permissions work\n3. Tickets -> Create panels\n4. Economy -> Add shop items\n5. Sticky Notes -> Channel reminders\n6. Invites -> Set logging\n7. Vanity -> Enable tracking\n8. Roles -> Claimable roles\n9. Other -> Giveaways & misc tools\n\n**Starter Commands:**\n• `{prefix}setprefix <prefix>`\n• `{prefix}configure`\n",
            color=discord.Color.purple(),
        )
        pages.append(setup_order)
        econ_page = discord.Embed(
            title="💰 Economy System",
            description=f"{bar(3)}\n\nUsers earn coins, store cash in bank, gamble, work jobs, and buy items.\nAdmins can fully customize the shop.\n\n**Starter Commands:**\n• `{prefix}work`\n• `{prefix}daily`\n• `{prefix}balance`\n",
            color=discord.Color.green(),
        )
        pages.append(econ_page)
        mod = discord.Embed(
            title="⚔️ Moderation System",
            description=f"{bar(4)}\n\nKicks, bans, slowmode, warnings, mutes, purges, blacklisting, and more.\nEverything logs cleanly once configured.\n\n**Starter Commands:**\n• `{prefix}warn @user <reason>`\n• `{prefix}mute @user <time>`\n• `{prefix}purge <amount>`",
            color=discord.Color.red(),
        )
        pages.append(mod)
        ticket = discord.Embed(
            title="🎟 Ticket System",
            description=f"{bar(5)}\n\nCreate custom ticket panels with buttons, categories, transcripts, and support tools.\n\n**Starter Commands:**\n• `{prefix}ticketsetup`\n• `{prefix}ticketadd @user`\n• `{prefix}ticketclose`",
            color=discord.Color.blurple(),
        )
        pages.append(ticket)
        config = discord.Embed(
            title="⚙️ Config System",
            description=f"{bar(6)}\n\nManage prefix, roles, logs, and system toggles.\nThis is where the bot truly comes alive.\n\n**Starter commands:**\n• `{prefix}configure`\n• `{prefix}viewconfig`\n• `{prefix}editconfig`",
            color=discord.Color.orange(),
        )
        pages.append(config)
        sticky = discord.Embed(
            title="🗒 Sticky Notes System",
            description=f"{bar(7)}\n\nPin an auto reposting sticky message to keep rules or reminders visible.\n\n**Starter Commands:**\n• `{prefix}stickynote <channel> <message>`\n• `{prefix}unstickynote <id>`",
            color=discord.Color.yellow(),
        )
        pages.append(sticky)
        invites_page = discord.Embed(
            title="📨 Invite Tracking System",
            description=f"{bar(8)}\n\nTracks who invited who, logs joins, and counts user invites.\n\n**Starter Commands:**\n• `{prefix}invites @user`\n• `{prefix}invitechannel`\n• `{prefix}removeinvites @user <amount>`",
            color=discord.Color.teal(),
        )
        pages.append(invites_page)
        vanity = discord.Embed(
            title="✨ Vanity System",
            description=f"{bar(9)}\n\nReward users who promote the server externally.\n\n**Starter Commands:**\n• `{prefix}vanityroles @role #log <status>`\n• `{prefix}promoters`\n• `{prefix}resetpromoters`",
            color=discord.Color.magenta(),
        )
        pages.append(vanity)
        roles = discord.Embed(
            title="🎭 Role System",
            description=f"{bar(10)}\n\nCreate claimable roles that members can pick from.\n\n**Starter Commands:**\n• `{prefix}roleadd @role`\n• `{prefix}roleremove @role`",
            color=discord.Color.gold(),
        )
        pages.append(roles)
        other = discord.Embed(
            title="📦 Other Systems",
            description=f"{bar(10)}\n\nGiveaways, reaction roles, and more.\n\n**Starter Commands:**\n• `{prefix}giveaway`\n• `{prefix}reactionrole <msg_id> <emoji> @role`\n• `{prefix}disable <cmd/category>`\n• `{prefix}enable <cmd/category>`",
            color=discord.Color.light_gray(),
        )
        pages.append(other)
        view = TutorialPages(pages)
        await ctx.send(embed=pages[0], view=view)

    @commands.hybrid_command(name="help", description="View bot commands.", aliases=["commands", "cmds"])
    async def help(self, ctx):
        """Display a paginated help embed. Economy commands are shown to everyone,
        and staff commands are shown only when the invoker holds the configured staff role."""
        doc = await state.settings_col.find_one({"guild": str(ctx.guild.id)})
        prefix = doc.get("prefix", "?") if doc else "?"
        staff_role = ctx.guild.get_role(doc.get("staff_role")) if doc else None
        is_staff = staff_role in ctx.author.roles if staff_role else False
        pages = []
        all_commands = [
            cmd for cmd in self.bot.commands if not cmd.hidden and cmd.name not in cfg.HELP_EXCLUDED_COMMANDS
        ]

        def format_params(cmd):
            params = []
            for param_name, param in cmd.clean_params.items():
                name = param_name.replace("_", "-")
                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    params.append(f"<{name}...>")
                elif param.kind == inspect.Parameter.KEYWORD_ONLY:
                    params.append(f"<{name}>")
                elif param.default is not param.empty:
                    params.append(f"[{name}]")
                else:
                    params.append(f"<{name}>")
            return " ".join(params)

        def command_label(cmd):
            param_text = format_params(cmd)
            aliases = [f"{prefix}{alias}" for alias in cmd.aliases or []]
            names = [f"{prefix}{cmd.name}", *aliases]
            formatted_names = []
            for name in dict.fromkeys(names):
                formatted_names.append(f"{name} {param_text}".strip())
            return " / ".join(formatted_names)

        def command_desc(cmd):
            return cmd.description or cmd.help or "No description provided."

        staff_entries = []
        economy_entries = []
        general_entries = []
        for cmd in all_commands:
            entry = (command_label(cmd), command_desc(cmd))
            if cmd.name in cfg.STAFF_HELP_COMMANDS:
                staff_entries.append(entry)
            elif cmd.name in cfg.ECONOMY_HELP_COMMANDS:
                economy_entries.append(entry)
            else:
                general_entries.append(entry)
        general_entries.sort(key=lambda e: e[0].lower())
        economy_entries.sort(key=lambda e: e[0].lower())
        staff_entries.sort(key=lambda e: e[0].lower())
        per_page = 10

        def append_pages(title_prefix, color, entries):
            for i in range(0, len(entries), per_page):
                chunk = entries[i : i + per_page]
                embed = discord.Embed(title=f"{title_prefix} (Page {i // per_page + 1})", color=color)
                for name, value in chunk:
                    embed.add_field(name=name, value=value, inline=False)
                pages.append(embed)

        append_pages("💬 General Commands", discord.Color.blurple(), general_entries)
        append_pages("💰 Economy Commands", discord.Color.green(), economy_entries)
        if is_staff:
            append_pages("🛠️ Staff Commands", discord.Color.orange(), staff_entries)
        view = CommandPages(pages, is_staff, prefix)
        ctx.bot.help_pages = pages
        await ctx.send(embed=pages[0], view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))
