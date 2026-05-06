"""Permission helpers used by checks, commands, and views.

These functions intentionally avoid using ``commands.check`` so they can be
called from buttons/modals where there is no ``Context`` — only a member or
a guild. The ``check_*`` functions in :mod:`bot.utils.checks` then wrap
these for use as command decorators.
"""

from __future__ import annotations

import discord

from bot.config.constants import THETRUCK_ID
from bot.database import settings_col, staffperms_col


async def has_staff_role(member: discord.Member, guild: discord.Guild | None = None) -> bool:
    """True when ``member`` has the configured staff role for this guild.

    Accepts an optional ``guild`` argument so it works both from cogs (where
    the guild is implicit) and from background tasks/views (where it isn't).
    """
    target_guild = guild or getattr(member, "guild", None)
    if target_guild is None:
        return False

    data = await settings_col.find_one({"guild": str(target_guild.id)})
    if not data or "staff_role" not in data:
        return False

    role = target_guild.get_role(int(data["staff_role"]))
    return bool(role and role in member.roles)


async def check_staff_perm(ctx, perm_name: str) -> bool:
    """Return True if ``ctx.author`` is allowed to use a ``perm_name`` action.

    The hierarchy is: server owner → bot owner override → admin permission →
    explicit grants stored in ``staffperms_col``. Ticket-style permissions
    (``tickets:<x>``) also accept the ``tickets:all`` wildcard.
    """
    if ctx.author == ctx.guild.owner or ctx.author.id == THETRUCK_ID:
        return True

    if ctx.author.guild_permissions.administrator:
        return True

    data = await staffperms_col.find_one(
        {"guild": str(ctx.guild.id), "user": str(ctx.author.id)}
    )
    if not data or "permissions" not in data:
        return False

    perms = data["permissions"]
    if "all" in perms:
        return True

    if perm_name.startswith("tickets:") and "tickets:all" in perms:
        return True

    return perm_name in perms


def check_target_permission(ctx, member: discord.Member) -> str | None:
    """Validate that ``ctx.author`` is allowed to act on ``member``.

    Returns the user-facing error message on failure, or ``None`` if the
    action is permitted. Pure function — no I/O.
    """
    if member == ctx.author:
        return "❌ You can't perform this action on yourself."
    if member == ctx.guild.owner:
        return "❌ You can't perform this action on the server owner."
    if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
        return "❌ You can't perform this action on someone with an equal or higher role."
    return None


async def is_blacklisted(guild: discord.Guild, user: discord.Member) -> bool:
    """Has the user been given the configured blacklist role?"""
    settings = await settings_col.find_one({"guild": str(guild.id)})
    if settings and "blacklist_role" in settings:
        role = discord.utils.get(guild.roles, id=settings["blacklist_role"])
        if role and role in user.roles:
            return True
    return False


async def get_or_create_blacklist_role(
    guild: discord.Guild, settings: dict
) -> discord.Role:
    """Find the blacklist role, creating it if necessary, and persist its id."""
    role: discord.Role | None = None

    if "blacklist_role" in settings:
        role = discord.utils.get(guild.roles, id=settings["blacklist_role"])

    if role is None:
        role = discord.utils.get(guild.roles, name="Blacklist")

    if role is None:
        role = await guild.create_role(
            name="Blacklist",
            colour=discord.Colour(0x000000),
            reason="Blacklist role created automatically by bot",
        )

    await settings_col.update_one(
        {"guild": str(guild.id)},
        {"$set": {"blacklist_role": role.id}},
        upsert=True,
    )
    return role


async def is_maintenance_mode(guild_id) -> bool:
    """Whether maintenance mode is currently enabled for the guild."""
    settings = await settings_col.find_one({"guild": str(guild_id)})
    return bool(settings and settings.get("maintenance_mode", False))


async def is_staff_user(ctx) -> bool:
    """Convenience helper: owner, admin, or has the configured staff role."""
    if ctx.author.id == ctx.guild.owner_id:
        return True
    if ctx.author.guild_permissions.administrator:
        return True
    settings = await settings_col.find_one({"guild": str(ctx.guild.id)})
    if settings and "staff_role" in settings:
        staff_role = ctx.guild.get_role(settings["staff_role"])
        if staff_role and staff_role in ctx.author.roles:
            return True
    return False


__all__ = [
    "has_staff_role",
    "check_staff_perm",
    "check_target_permission",
    "is_blacklisted",
    "get_or_create_blacklist_role",
    "is_maintenance_mode",
    "is_staff_user",
]
