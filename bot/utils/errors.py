"""Error helpers shared between command callbacks and event handlers.

Two responsibilities live here:

* **Detection** — peeling Discord/discord.py error wrappers so a 503 from the
  Discord API doesn't get reported as an opaque ``CommandInvokeError``.
* **Presentation** — making sure errors raised by *slash* commands are sent
  ephemerally (so they're hidden from other users) while *prefix* commands
  still surface them in-channel.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


def unwrap_command_error(error: Exception) -> Exception:
    """Walk through ``CommandInvokeError`` wrappers to the real cause.

    discord.py wraps user-callback exceptions in ``CommandInvokeError`` (and
    sometimes the app-commands variant) which hides the actual class. We
    walk both ``original`` and ``__cause__`` chains and stop at the first
    non-wrapper exception so callers can ``isinstance`` against it directly.
    """
    invoke_error_types: tuple[type[Exception], ...] = (commands.CommandInvokeError,)
    app_invoke_error = getattr(app_commands, "CommandInvokeError", None)
    if app_invoke_error is not None:
        invoke_error_types = invoke_error_types + (app_invoke_error,)

    current = error
    seen: set[int] = set()
    while current and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, invoke_error_types):
            nested = getattr(current, "original", None)
            if nested is not None:
                current = nested
                continue
        nested = getattr(current, "__cause__", None)
        if nested is not None:
            current = nested
            continue
        break
    return current


def is_discord_service_unavailable_error(error: Exception) -> bool:
    """True when the underlying error is a Discord 503 outage.

    We look at the exception class, the HTTP status if present, and as a
    last resort the textual message (since some libraries swallow the
    response and leave only a string).
    """
    root = unwrap_command_error(error)
    if isinstance(root, discord.DiscordServerError):
        return True

    status = getattr(root, "status", None)
    if status == 503:
        return True

    text = str(root).lower()
    return "503 service unavailable" in text or "upstream connect error" in text


def is_prefix(ctx) -> bool:
    """Whether ``ctx`` came from a prefix invocation rather than a slash one.

    A hybrid context with no ``interaction`` attached is a regular text
    command; if ``interaction`` is set, the user typed ``/foo`` instead.
    """
    return not hasattr(ctx, "interaction") or ctx.interaction is None


async def send_hybrid_error(ctx, *, content=None, embed=None, delete_after=None):
    """Send an error message with sensible visibility per invocation mode.

    Slash invocations get an *ephemeral* response so other channel members
    don't see the failure. Prefix invocations behave as before.
    """
    if is_prefix(ctx):
        return await ctx.send(content=content, embed=embed, delete_after=delete_after)

    if ctx.interaction.response.is_done():
        return await ctx.interaction.followup.send(
            content=content, embed=embed, ephemeral=True
        )

    return await ctx.interaction.response.send_message(
        content=content, embed=embed, ephemeral=True
    )


__all__ = [
    "unwrap_command_error",
    "is_discord_service_unavailable_error",
    "is_prefix",
    "send_hybrid_error",
]
