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

"""Access control: staff/permission checks, blacklist and maintenance barriers, the three
global bot checks (registered by main.py via ``bot.add_check``), channel gating, role-hierarchy
checks and the moderation audit log helper.

The global checks are plain coroutines here, never decorated with ``@bot.check`` - main.py owns
the live ``Bot`` and wires them up, which keeps this module importable without a Bot.
"""

import json
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from core import config as cfg
from core import state


async def global_lock_check(ctx):
    """Block all commands (except override) when the guild lock is active.
    The lock is set by the stop command and cleared by override."""
    if ctx.guild is None:
        return True
    if ctx.command.name == "override":
        return True
    if state.bot_locks.get(str(ctx.guild.id)):
        await ctx.send(f"🔒 The bot is locked, only `override` by **{cfg.BOT_ADMIN_NAME}** works.")
        return False
    return True


def staff_only():
    """Return a command check that passes only when the invoker holds the configured staff role."""

    async def predicate(ctx):
        if ctx.guild is None:
            return False
        return await is_staff_user(ctx)

    return commands.check(predicate)


async def check_staff_perm(ctx, perm_name: str):
    """Return True if the invoker is allowed to perform perm_name.
    Guild owner, bot authorized IDs, and server administrators always pass.
    Other users are checked against the granular staffperms collection."""
    if ctx.author == ctx.guild.owner or ctx.author.id in cfg.AUTHORIZED_USER_IDS:
        return True
    if ctx.author.guild_permissions.administrator:
        return True
    data = await state.staffperms_col.find_one({"guild": str(ctx.guild.id), "user": str(ctx.author.id)})
    if not data or "permissions" not in data:
        return False
    perms = data["permissions"]
    if "all" in perms:
        return True
    # tickets:all grants access to every ticket sub permission
    if perm_name.startswith("tickets:"):
        if "tickets:all" in perms:
            return True
        return perm_name in perms
    return perm_name in perms


def staffperm(perm_name: str):
    """Return a command check decorator that calls check_staff_perm with perm_name."""

    async def predicate(ctx):
        return await check_staff_perm(ctx, perm_name)

    return commands.check(predicate)


async def is_blacklisted(guild: discord.Guild, user: discord.Member) -> bool:
    """Return True if the member holds the guild's configured blacklist role."""
    guild_id = str(guild.id)
    settings = await state.settings_col.find_one({"guild": guild_id})
    if settings and "blacklist_role" in settings:
        role = discord.utils.get(guild.roles, id=settings["blacklist_role"])
        if role and role in user.roles:
            return True
    return False


async def is_maintenance_mode(guild_id):
    """Return True when the guild has maintenance mode enabled in its settings."""
    settings = await state.settings_col.find_one({"guild": guild_id})
    return settings.get("maintenance_mode", False) if settings else False


async def is_staff_user(ctx):
    """Return True if the invoker is the guild owner, an administrator, or holds the staff role."""
    if ctx.author.id in cfg.AUTHORIZED_USER_IDS:
        return True
    if ctx.author.id == ctx.guild.owner_id:
        return True
    if ctx.author.guild_permissions.administrator:
        return True
    settings = await state.settings_col.find_one({"guild": str(ctx.guild.id)})
    if settings and "staff_role" in settings:
        staff_role = ctx.guild.get_role(settings["staff_role"])
        if staff_role and staff_role in ctx.author.roles:
            return True
    return False


async def check_maintenance_access(ctx):
    """Return True when the guild is not in maintenance mode, or when the invoker is staff.
    Sends an embed and returns False if a non staff user tries to use commands during maintenance."""
    guild_id = str(ctx.guild.id)
    if not await is_maintenance_mode(guild_id):
        return True
    if await is_staff_user(ctx):
        return True
    embed = discord.Embed(
        title="🔧 Bot Under Maintenance",
        description="The bot is currently in maintenance mode. Only staff can use commands at this time.",
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="⏰ Estimated Downtime",
        value="Maintenance mode will be disabled when staff complete their work.",
        inline=False,
    )
    embed.set_footer(text="Please try again later. Thank you for your patience!")
    if hasattr(ctx, "respond") and ctx.is_interaction():
        await ctx.respond(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)
    return False


def blacklist_barrier():
    """Return a command check that blocks blacklisted users and users during maintenance.
    Works for both prefix commands (ctx.author) and slash interactions (interaction.user)."""

    async def predicate(ctx_or_interaction):
        if hasattr(ctx_or_interaction, "author"):
            user = ctx_or_interaction.author
            guild = ctx_or_interaction.guild
            if await is_blacklisted(guild, user):
                try:
                    await ctx_or_interaction.send("🚫 You are blacklisted and cannot use this command.", delete_after=5)
                    await ctx_or_interaction.message.delete()
                except (discord.HTTPException, discord.Forbidden):
                    pass
                return False
            if not await check_maintenance_access(ctx_or_interaction):
                return False
        else:
            user = ctx_or_interaction.user
            guild = ctx_or_interaction.client.get_guild(int(ctx_or_interaction.guild_id))
            if guild and await is_blacklisted(guild, user):
                try:
                    await ctx_or_interaction.response.send_message(
                        "🚫 You are blacklisted and cannot use this command.", ephemeral=True
                    )
                except (discord.HTTPException, discord.Forbidden):
                    pass
                return False
            if not await check_maintenance_access(ctx_or_interaction):
                return False
        return True

    return commands.check(predicate)


def maintenance_bypass():
    """Return a check that always passes. Applied to commands that must work even in maintenance mode."""

    async def predicate(ctx):
        return True

    return commands.check(predicate)


async def ensure_guild_context(ctx):
    """Block all commands from being used in DMs. All bot functionality requires a guild context."""
    if ctx.guild is None:
        await ctx.send("❌ This bot can only be used in a server, not in DMs.")
        return False
    return True


async def check_disabled(ctx):
    """Block commands or entire categories that a guild admin has disabled via the disable command.
    Categories map to cog class names through cfg.COMMAND_CATEGORIES; the commands listed in
    cfg.UNDISABLEABLE_COMMANDS always run so a guild can never lock itself out."""
    if not ctx.guild:
        return True
    if ctx.command.name in cfg.UNDISABLEABLE_COMMANDS:
        return True
    doc = await state.disabled_col.find_one({"guild": str(ctx.guild.id)})
    if not doc:
        return True
    if ctx.command.name in doc.get("disabled_commands", []):
        return False
    category = command_category(ctx.command)
    return not (category and category in doc.get("disabled_categories", []))


def command_category(command) -> str | None:
    """Return the disable/enable category a command belongs to (looked up by its cog's class name),
    or None for commands outside every category."""
    cog_name = getattr(command, "cog_name", None)
    if not cog_name:
        return None
    for category, cog_names in cfg.COMMAND_CATEGORIES.items():
        if cog_name in cog_names:
            return category
    return None


async def is_category_disabled(guild_id, category: str) -> bool:
    """Return True when a guild has disabled the given command category (used by features that are
    not commands, such as the DuckGPT mention trigger)."""
    doc = await state.disabled_col.find_one({"guild": str(guild_id)})
    return bool(doc and category in doc.get("disabled_categories", []))


def check_target_permission(ctx, member: discord.Member):
    """Return an error string if the invoker cannot act on member due to role hierarchy rules.
    Returns None when the action is allowed."""
    if member == ctx.author:
        return "❌ You can't perform this action on yourself."
    if member == ctx.guild.owner:
        return "❌ You can't perform this action on the server owner."
    if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
        return "❌ You can't perform this action on someone with an equal or higher role."
    return None


async def check_channel(ctx, config_key: str, friendly_name: str | None = None) -> bool:
    """Return True when the command is invoked in an allowed channel.
    Staff members bypass the channel restriction. If no channel is configured the check passes.
    Sends a denial message and returns False when the channel is not permitted."""
    settings = await state.settings_col.find_one({"guild": str(ctx.guild.id)}) or {}
    staff_role_id = settings.get("staff_role")
    if staff_role_id and discord.utils.get(ctx.author.roles, id=staff_role_id):
        return True
    config = await state.config_col.find_one({"guild": str(ctx.guild.id)}) or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception:
            config = {}
    value = config.get(config_key)
    if not value:
        return True
    if isinstance(value, int):
        allowed_channels = [value]
    elif isinstance(value, str):
        if value.lower() == "all":
            return True
        if value.isdigit():
            allowed_channels = [int(value)]
        else:
            ids = [int(x) for x in re.findall("\\\\d+", value)]
            allowed_channels = ids
    elif isinstance(value, list):
        allowed_channels = [int(x) for x in value if str(x).isdigit()]
    else:
        return True
    if allowed_channels and ctx.channel.id not in allowed_channels:
        mention = f"<#{allowed_channels[0]}>" if allowed_channels else "`a configured channel`"
        fname = friendly_name or config_key.replace("_", " ").title()
        await ctx.send(f"🚫 {fname} commands can only be used in {mention}.")
        return False
    return True


async def log_action(ctx, message, user_id=None, action_type=None, *, guild: discord.Guild | None = None):
    """Post a moderation log embed to the configured log channel and persist the record to MongoDB.

    ``ctx`` may be a Context, an Interaction, or None for automated actions (mute expiry) - pass
    ``guild`` explicitly in that case. The log channel is the ``log_channel`` saved by
    ``.configure`` / ``.editconfig`` (config_col); the legacy settings_col value is a fallback."""
    try:
        guild = guild or getattr(ctx, "guild", None)
        if guild is None:
            print("[log_action ERROR] no guild available for the log entry")
            return
        guild_id = str(guild.id)
        actor = getattr(ctx, "author", None) or getattr(ctx, "user", None)
        config = await state.config_col.find_one({"guild": guild_id}) or {}
        if not isinstance(config, dict):
            config = {}
        log_channel_id = config.get("log_channel")
        if not log_channel_id:
            settings = await state.settings_col.find_one({"guild": guild_id})
            log_channel_id = settings.get("log_channel") if settings else None
        if log_channel_id:
            try:
                log_channel = guild.get_channel(int(log_channel_id))
            except (TypeError, ValueError):
                log_channel = None
            if log_channel:
                embed = discord.Embed(
                    title="📋 Moderation Log",
                    description=message,
                    color=discord.Color.dark_blue(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text=f"By {actor} • {actor.id}" if actor else "Automated action")
                await log_channel.send(embed=embed)
        if user_id and action_type:
            log_doc = {
                "guild": guild_id,
                "user_id": str(user_id),
                "action": action_type,
                "by": {"name": str(actor), "id": str(actor.id)} if actor else {"name": "System", "id": None},
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await state.logs_col.insert_one(log_doc)
    except Exception as e:
        print(f"[log_action ERROR] {e}")


async def has_staff_role(member: discord.Member, guild: discord.Guild) -> bool:
    """Return True when member holds the configured staff role in guild."""
    doc = await state.settings_col.find_one({"guild": str(guild.id)})
    rid = doc.get("staff_role") if doc else None
    if not rid:
        return False
    role = guild.get_role(int(rid))
    return bool(role and role in member.roles)
