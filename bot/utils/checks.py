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
    """Award random XP after a *successful* command invocation.

    Skips:
    * staff helper commands (kick/ban/etc.) that shouldn't farm XP
    * commands that set ``ctx._skip_xp_award`` to flag an early exit
    * non-guild contexts (DMs / no Context resolved)

    Awards XP exactly once and sends a single chat announcement so users
    can see the system is working. Both the DB update and the message
    send are guarded with their own ``ctx._xp_already_awarded`` sentinel,
    which makes the decorator idempotent — even if discord.py somehow
    invokes the callback twice for one user message (the historical
    duplicate-``process_commands`` bug, for example) the second pass
    becomes a no-op.

    Works on both free-standing functions and cog methods. The wrapper
    introspects ``args`` to find the ``Context`` regardless of whether
    ``self`` is also present.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 1. Resolve the Context without depending on a concrete subclass.
            #    Cog methods get `(self, ctx, ...)`; free functions get
            #    `(ctx, ...)`. We treat the first arg with both `.guild` and
            #    `.author` as the context.
            ctx = kwargs.get("ctx")
            if ctx is None and args:
                first = args[0]
                if hasattr(first, "guild") and hasattr(first, "author"):
                    ctx = first
                elif len(args) > 1:
                    second = args[1]
                    if hasattr(second, "guild") and hasattr(second, "author"):
                        ctx = second

            # If we couldn't find a context, just run the inner function.
            if ctx is None:
                return await func(*args, **kwargs)

            # 2. Run the underlying command. If the command raises (or
            #    discord.py blocks it via cooldown / check failure before we
            #    even get here), XP is *not* awarded — that's the whole point
            #    of putting the award after the await.
            result = await func(*args, **kwargs)

            # 3. Idempotency guard: if this Context already had XP awarded
            #    once during the same invocation, do nothing. This protects
            #    against a stale duplicate-``process_commands`` regression.
            if getattr(ctx, "_xp_already_awarded", False):
                return result
            ctx._xp_already_awarded = True

            guild = getattr(ctx, "guild", None)
            author = getattr(ctx, "author", None)
            if not guild or not author:
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
            user_id = str(author.id)
            guild_id = str(guild.id)
            key = f"{guild_id}-{user_id}"

            # 4. Persist the XP. A flaky Mongo write should never crash the
            #    invocation that just succeeded; log and continue.
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
                    f"[XP] Awarded {xp_gained} xp to {author} "
                    f"({author.id}) for /{command_name}"
                )
            except Exception as exc:  # pragma: no cover
                print(
                    f"[XP Decorator] DB update failed for {author.id} "
                    f"on /{command_name}: {type(exc).__name__}: {exc}"
                )

            # 5. Announce the award. ``ctx.send`` is hybrid-aware in
            #    discord.py 2.x and routes through the interaction followup
            #    automatically when needed. We try it first and only fall
            #    back to the raw followup if it raises (e.g. permissions or
            #    expired interaction).
            try:
                mention = getattr(author, "mention", f"<@{author.id}>")
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
                                f"{author.id} on /{command_name}: "
                                f"{type(followup_exc).__name__}: {followup_exc}"
                            )
                    print(
                        f"[XP Decorator] Could not send XP message for "
                        f"{author.id} on /{command_name}: "
                        f"{type(primary_exc).__name__}: {primary_exc}"
                    )
            except Exception as exc:  # pragma: no cover
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
