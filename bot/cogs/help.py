"""``/help`` and ``/tutorial`` commands."""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database import (
    config_col,
    guild_config_col,
    invite_config_col,
    settings_col,
    shop_col,
    sticky_col,
    ticket_panels_col,
)
from bot.views.help import CommandPages, TutorialPages


class HelpCog(commands.Cog, name="Help"):
    """Help and tutorial commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Replace the default help command with ours.
        bot.remove_command("help")

    @commands.hybrid_command(
        name="help",
        description="View bot commands.",
        aliases=["commands", "cmds"],
    )
    async def help(self, ctx: commands.Context) -> None:
        doc = await settings_col.find_one({"guild": str(ctx.guild.id)})
        prefix = doc.get("prefix", "?") if doc else "?"
        staff_role = ctx.guild.get_role(doc.get("staff_role")) if doc else None
        is_staff = staff_role in ctx.author.roles if staff_role else False

        def format_field(name: str, value: str) -> tuple[str, str]:
            return name.replace("?", prefix), value

        general_commands = [
            ("?cmds", "Show this help menu"),
            ("?serverinfo", "View server information"),
            ("?userinfo [@user]", "Detailed user info"),
            ("?afk [reason]", "Set your AFK status"),
            ("?duck", "Random picture of a duck"),
            ("?duckquiz", "Standardized Duck Quiz"),
            ("?slap", "Slap another user"),
            ("?duckfact", "Get a random duck fact"),
            ("?quote", "Get a random quote"),
            ("?roles", "Show claimable roles"),
            ("?invitechannel", "Set the channel where invite joins are announced."),
            ("?invites", "Check how many invites a user has."),
            ("?inviteleaderboard", "Show the top inviters in the server."),
        ]

        per_page = 10
        pages: list[discord.Embed] = []

        for i in range(0, len(general_commands), per_page):
            chunk = general_commands[i:i + per_page]
            embed = discord.Embed(
                title=f"💬 General Commands (Page {i // per_page + 1})",
                color=discord.Color.blurple(),
            )
            for name, value in chunk:
                embed.add_field(
                    name=format_field(name, value)[0],
                    value=value,
                    inline=False,
                )
            pages.append(embed)

        economy_commands = [
            ("?balance / ?bal", "Check your balance"),
            ("?daily", "Claim your daily reward"),
            ("?work", "Work to earn coins"),
            ("?beg", "Beg for coins"),
            ("?deposit / ?dep <amount>", "Deposit to bank"),
            ("?withdraw / ?with <amount>", "Withdraw from bank"),
            ("?shop", "View the shop"),
            ("?buy <item> [amount]", "Buy an item from the shop"),
            ("?use <item>", "Use an item from your inventory"),
            ("?inventory / ?inv", "View your items"),
            ("?give / ?pay @user <amount>", "Give coins to another user"),
            ("?leaderboard / ?lb", "View the top users"),
            ("?coinflip / ?cf <amount>", "Coin flip for coins"),
            ("?fish", "Go fishing to earn coins"),
            ("?rob / ?steal @user", "Attempt to rob another user"),
            ("?lottery", "Join the lottery"),
            ("?choosejob", "Choose your dream job"),
            ("?jobstatus", "Check your next promotion"),
            ("?crime <bank/shoplift/payroll>", "Attempt a risky crime to earn coins"),
            ("?sell <item> <amount>", "Sell items from your inventory"),
            ("?invest", "Invest in fake companies for profit"),
            ("?investstatus", "Check your investments"),
            ("?hunt", "Go hunting for animals"),
            ("?mine", "Go mining for ores"),
            ("?doorgame", "Try your luck through multiple doors!"),
            ("?ducktowers", "Play a game of Duck Towers!"),
            ("?mines", "Play Mines and test your luck!"),
        ]

        for i in range(0, len(economy_commands), per_page):
            chunk = economy_commands[i:i + per_page]
            embed = discord.Embed(
                title=f"💰 Economy Commands (Page {i // per_page + 1})",
                color=discord.Color.green(),
            )
            for name, value in chunk:
                embed.add_field(
                    name=format_field(name, value)[0],
                    value=value,
                    inline=False,
                )
            pages.append(embed)

        view = CommandPages(pages, is_staff)
        ctx.bot.help_pages = pages
        await ctx.send(embed=pages[0], view=view)

    @commands.hybrid_command(
        name="tutorial",
        description="Learn how to use each bot system.",
    )
    async def tutorial(self, ctx: commands.Context) -> None:
        settings = await settings_col.find_one({"guild": str(ctx.guild.id)}) or {}
        config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}  # noqa: F841
        guild_cfg = await guild_config_col.find_one(  # noqa: F841
            {"guild": str(ctx.guild.id)}
        ) or {}
        invite_cfg = await invite_config_col.find_one({"guild": str(ctx.guild.id)}) or {}
        prefix = settings.get("prefix", "?") if settings else "?"

        staff_role = settings.get("staff_role") if settings else None
        log_channel = settings.get("log_channel") if settings else None
        invite_log = invite_cfg.get("log_channel") if invite_cfg else None

        ticket_panels = await ticket_panels_col.count_documents(
            {"guild": str(ctx.guild.id)}
        )
        shop_items = await shop_col.count_documents({})
        sticky_notes = await sticky_col.count_documents(
            {"guild": str(ctx.guild.id)}
        )

        missing: list[str] = []
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

        missing_block = (
            "\n".join(missing) if missing else "🎉 All core systems are configured!"
        )

        def bar(index: int, total: int = 10) -> str:
            return f"{'█' * index}{'░' * (total - index)} **{index}/{total}**"

        intro = discord.Embed(
            title="📚 Bot Tutorial — How Everything Works",
            description=(
                f"{bar(1)}\n\n"
                "Welcome to the full system tutorial! This menu guides you through "
                "every bot feature.\n"
                "Use the navigation buttons to browse each category.\n\n"
                f"Your server prefix is: **{prefix}**\n\n"
                f"**Setup Status:**\n{missing_block}\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}help`\n"
                f"• `{prefix}configure`\n"
            ),
            color=discord.Color.blue(),
        )

        setup_order = discord.Embed(
            title="🧭 Recommended Setup Order",
            description=(
                f"{bar(2)}\n\n"
                "**Best setup order for a fresh server:**\n"
                "1. Config → Prefix, staff role, logs\n"
                "2. Moderation → Make sure permissions work\n"
                "3. Tickets → Create panels\n"
                "4. Economy → Add shop items\n"
                "5. Sticky Notes → Channel reminders\n"
                "6. Invites → Set logging\n"
                "7. Vanity → Enable tracking\n"
                "8. Roles → Claimable roles\n"
                "9. Other → Giveaways & misc tools\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}setprefix <prefix>`\n"
                f"• `{prefix}configure`\n"
            ),
            color=discord.Color.purple(),
        )

        econ = discord.Embed(
            title="💰 Economy System",
            description=(
                f"{bar(3)}\n\n"
                "Users earn coins, store cash in bank, gamble, work jobs, "
                "and buy items.\n"
                "Admins can fully customize the shop.\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}work`\n"
                f"• `{prefix}daily`\n"
                f"• `{prefix}balance`\n"
            ),
            color=discord.Color.green(),
        )

        mod = discord.Embed(
            title="⚔️ Moderation System",
            description=(
                f"{bar(4)}\n\n"
                "Kicks, bans, slowmode, warnings, mutes, purges, blacklisting, and more.\n"
                "Everything logs cleanly once configured.\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}warn @user <reason>`\n"
                f"• `{prefix}mute @user <time>`\n"
                f"• `{prefix}purge <amount>`"
            ),
            color=discord.Color.red(),
        )

        ticket = discord.Embed(
            title="🎟 Ticket System",
            description=(
                f"{bar(5)}\n\n"
                "Create custom ticket panels with buttons, categories, "
                "transcripts, and support tools.\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}ticketsetup`\n"
                f"• `{prefix}ticketadd @user`\n"
                f"• `{prefix}ticketclose`"
            ),
            color=discord.Color.blurple(),
        )

        config_page = discord.Embed(
            title="⚙️ Config System",
            description=(
                f"{bar(6)}\n\n"
                "Manage prefix, roles, logs, and system toggles.\n"
                "This is where the bot truly comes alive.\n\n"
                "**Starter commands:**\n"
                f"• `{prefix}configure`\n"
                f"• `{prefix}viewconfig`\n"
                f"• `{prefix}editconfig`"
            ),
            color=discord.Color.orange(),
        )

        sticky = discord.Embed(
            title="🗒 Sticky Notes System",
            description=(
                f"{bar(7)}\n\n"
                "Pin an auto-reposting sticky message to keep rules or reminders "
                "visible.\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}stickynote <channel> <message>`\n"
                f"• `{prefix}unstickynote <id>`"
            ),
            color=discord.Color.yellow(),
        )

        invites_page = discord.Embed(
            title="📨 Invite Tracking System",
            description=(
                f"{bar(8)}\n\n"
                "Tracks who invited who, logs joins, and counts user invites.\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}invites @user`\n"
                f"• `{prefix}invitechannel`\n"
                f"• `{prefix}removeinvites @user <amount>`"
            ),
            color=discord.Color.teal(),
        )

        vanity = discord.Embed(
            title="✨ Vanity System",
            description=(
                f"{bar(9)}\n\n"
                "Reward users who promote the server externally.\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}vanityroles @role #log <status>`\n"
                f"• `{prefix}promoters`\n"
                f"• `{prefix}resetpromoters`"
            ),
            color=discord.Color.magenta(),
        )

        roles_page = discord.Embed(
            title="🎭 Role System",
            description=(
                f"{bar(10)}\n\n"
                "Create claimable roles that members can pick from.\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}roleadd @role`\n"
                f"• `{prefix}roleremove @role`"
            ),
            color=discord.Color.gold(),
        )

        other = discord.Embed(
            title="📦 Other Systems",
            description=(
                f"{bar(10)}\n\n"
                "Giveaways, reaction roles, and more.\n\n"
                "**Starter Commands:**\n"
                f"• `{prefix}giveaway`\n"
                f"• `{prefix}reactionrole <msg_id> <emoji> @role`\n"
                f"• `{prefix}disable <cmd/category>`\n"
                f"• `{prefix}enable <cmd/category>`"
            ),
            color=discord.Color.light_gray(),
        )

        pages = [
            intro,
            setup_order,
            econ,
            mod,
            ticket,
            config_page,
            sticky,
            invites_page,
            vanity,
            roles_page,
            other,
        ]
        view = TutorialPages(pages)
        await ctx.send(embed=pages[0], view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
