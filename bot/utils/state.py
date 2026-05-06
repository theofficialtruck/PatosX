"""Tiny module that lets utility code reach the live ``Bot`` instance.

Several helpers (XP earner decorator, ``check_disabled``, error handlers) need
to call methods on the bot — for example ``bot.get_command`` or
``bot.wait_for``. Rather than threading the bot through every signature, we
expose a getter that the runner sets exactly once at startup.

Why a getter and not a module-level variable?  Lazy access means a unit test
or a documentation tool can import any helper without first instantiating a
``Bot``.  A clear ``RuntimeError`` from ``get_bot`` is also easier to debug
than an ``AttributeError`` on ``None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from discord.ext import commands

_bot: "commands.Bot | None" = None


def set_bot(bot: "commands.Bot") -> None:
    """Register the live bot instance — called once during startup."""
    global _bot
    _bot = bot


def get_bot() -> "commands.Bot":
    """Return the registered bot instance.

    Raises ``RuntimeError`` when called before ``set_bot`` — that's almost
    always a sign that someone is using a helper at import time.
    """
    if _bot is None:
        raise RuntimeError(
            "Bot instance not initialised. Did you forget to call "
            "bot.utils.state.set_bot(bot) during startup?"
        )
    return _bot


def has_bot() -> bool:
    """Useful for tasks that may run before bootstrap completes."""
    return _bot is not None


__all__ = ["set_bot", "get_bot", "has_bot"]
