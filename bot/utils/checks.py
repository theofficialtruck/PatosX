"""Reusable ``commands.check`` decorators wired up to the helpers in
:mod:`bot.utils.permissions` and :mod:`bot.utils.channels`.

Putting all of the decorator factories in one place means cogs can pull a
single import and the call sites read uniformly: ``@staff_only()`` next to
``@staffperm("kick")`` next to ``@blacklist_barrier()``.
"""

from __future__ import annotations

import functools
import random

import discord
from discord.ext import commands

from bot.config.constants import STAFF_HELP_COMMANDS
from bot.database import xp_col
from bot.utils.channels import check_maintenance_access
from bot.utils.permissions import (
    check_staff_perm,
    is_blacklisted,
)


def staff_only():
    """Allow only members with the configured staff role."""

    async def predicate(ctx):
        from bot.database import settings_col  # local import: avoid cycles

        guild_id = str(ctx.guild.id)
        settings = await settings_col.find_one({"guild": guild_id})
        if not settings or "staff_role" not in settings:
            return False
        role = discord.utils.get(ctx.guild.roles, id=settings["staff_role"])
        return bool(role and role in ctx.author.roles)

    return commands.check(predicate)


def staffperm(perm_name: str):
    """Require a specific staff permission key (see ``staffperms_col``)."""

    async def predicate(ctx):
        return await check_staff_perm(ctx, perm_name)

    return commands.check(predicate)


def blacklist_barrier():
    """Reject the command when the user is blacklisted or maintenance is on."""

    async def predicate(ctx_or_interaction):
        from bot.utils.state import has_bot, get_bot

        if hasattr(ctx_or_interaction, "author"):
            user = ctx_or_interaction.author
            guild = ctx_or_interaction.guild
            if guild and await is_blacklisted(guild, user):
                try:
                    await ctx_or_interaction.send(
                        "🚫 You are blacklisted and cannot use this command.",
                        delete_after=5,
                    )
                    await ctx_or_interaction.message.delete()
                except Exception:
                    pass
                return False

            if not await check_maintenance_access(ctx_or_interaction):
                return False
        else:
            user = ctx_or_interaction.user
            guild = None
            if has_bot():
                guild = get_bot().get_guild(int(ctx_or_interaction.guild_id))
            if guild and await is_blacklisted(guild, user):
                try:
                    await ctx_or_interaction.response.send_message(
                        "🚫 You are blacklisted and cannot use this command.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                return False

            if not await check_maintenance_access(ctx_or_interaction):
                return False

        return True

    return commands.check(predicate)


def maintenance_bypass():
    """No-op check kept for symmetry — used as a documentation marker.

    Some commands (notably the ``maintenance`` toggle itself) need to remain
    runnable while maintenance mode is on. The original codebase uses this
    decorator as a marker; we keep the marker so visual scanning is easy.
    """

    async def predicate(ctx):
        return True

    return commands.check(predicate)


def xp_earn(min_xp: int, max_xp: int):
    """Award random XP after a successful command invocation.

    Skips:
    * staff helper commands (kick/ban/etc.) that shouldn't farm XP
    * commands that set ``ctx._skip_xp_award`` to flag an early exit

    Works on both free-standing functions and cog methods. The wrapper
    introspects ``args`` to find the ``Context`` regardless of whether
    ``self`` is also present.

    Robustness notes
    ----------------
    The decorator is intentionally fault-tolerant — a flaky Mongo write or
    a Discord permission error must never crash the underlying command
    callback. We:

    * wrap the DB update in its own try/except so the message still fires
      even if Mongo is slow or briefly unavailable;
    * try ``ctx.send`` first (which is hybrid-aware in discord.py 2.x and
      will route through the interaction followup when needed) and only
      fall back to the raw interaction followup if it fails;
    * print a one-line console marker on every award so production logs
      show whether the system is actually running.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Cog methods receive `(self, ctx, ...)`; free functions receive
            # `(ctx, ...)`. Detect which case we're in by looking for a
            # commands.Cog at args[0].
            if args and isinstance(args[0], commands.Cog):
                ctx = args[1]
            else:
                ctx = args[0]

            result = await func(*args, **kwargs)

            if not getattr(ctx, "guild", None):
                return result

            command = getattr(ctx, "command", None)
            command_name = (
                command.name if command else func.__name__
            ).lower()
            if command_name in STAFF_HELP_COMMANDS:
                return result
            if getattr(ctx, "_skip_xp_award", False):
                ctx._skip_xp_award = False
                return result

            xp_gained = random.randint(min_xp, max_xp)
            user_id = str(ctx.author.id)
            guild_id = str(ctx.guild.id)
            key = f"{guild_id}-{user_id}"

            # 1. Persist the XP award. If Mongo briefly fails we still want
            #    the user-facing message to fire.
            try:
                await xp_col.update_one(
                    {"_id": key},
                    {
                        "$inc": {"xp": xp_gained},
                        "$set": {"guild": guild_id, "user": user_id},
                    },
                    upsert=True,
                )
                print(
                    f"[XP] Awarded {xp_gained} xp to {ctx.author} "
                    f"({ctx.author.id}) for /{command_name}"
                )
            except Exception as exc:
                print(
                    f"[XP Decorator] DB update failed for {ctx.author.id} "
                    f"on /{command_name}: {type(exc).__name__}: {exc}"
                )

            # 2. Send the announcement. ``ctx.send`` is hybrid-aware and will
            #    route through interaction followup when needed, so it's the
            #    safest first attempt. Fall back to the raw followup channel
            #    only if ``ctx.send`` raises.
            try:
                mention = getattr(ctx.author, "mention", f"<@{ctx.author.id}>")
                xp_msg = (
                    f"{mention}, you earned **{xp_gained} xp** "
                    f"by using `/{command_name}`"
                )
                try:
                    await ctx.send(xp_msg)
                except Exception as primary_exc:
                    interaction = getattr(ctx, "interaction", None)
                    if interaction is not None:
                        try:
                            await interaction.followup.send(xp_msg)
                            return result
                        except Exception as followup_exc:
                            print(
                                f"[XP Decorator] Followup send failed for "
                                f"{ctx.author.id} on /{command_name}: "
                                f"{type(followup_exc).__name__}: {followup_exc}"
                            )
                    print(
                        f"[XP Decorator] Could not send XP message for "
                        f"{ctx.author.id} on /{command_name}: "
                        f"{type(primary_exc).__name__}: {primary_exc}"
                    )
            except Exception as exc:  # last-resort safety net
                print(
                    f"[XP Decorator] Unexpected error building XP message "
                    f"for /{command_name}: {type(exc).__name__}: {exc}"
                )

            return result

        return wrapper

    return decorator


__all__ = [
    "staff_only",
    "staffperm",
    "blacklist_barrier",
    "maintenance_bypass",
    "xp_earn",
]
