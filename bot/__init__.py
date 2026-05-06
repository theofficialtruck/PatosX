"""DuckParadise Discord bot package."""

__all__ = ["create_bot", "run"]


def create_bot():
    """Lazy import to avoid pulling discord.py at package import time."""
    from bot.client import create_bot as _create_bot

    return _create_bot()


def run() -> None:
    """Entry point for `python -m bot`."""
    from bot.runner import run as _run

    _run()
