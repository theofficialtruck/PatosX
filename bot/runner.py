"""Bot bootstrap and entry-point glue.

Adding ``setup_hook`` here (rather than overriding ``on_ready``) gives us the
Discord-recommended place to load extensions before the gateway reaches
``READY``. That way the persistent views in ``on_ready`` always have their
underlying cogs loaded.
"""

from __future__ import annotations

import asyncio

from bot.client import create_bot
from bot.cogs import EXTENSIONS
from bot.config.secrets import DISCORD_TOKEN
from bot.events import EVENT_EXTENSIONS


async def _setup_hook(bot) -> None:
    """Load every extension before the gateway hands us our first event."""
    for extension in (*EXTENSIONS, *EVENT_EXTENSIONS):
        try:
            await bot.load_extension(extension)
            print(f"✅ Loaded extension: {extension}")
        except Exception as exc:
            print(f"❌ Failed to load extension {extension}: {exc}")
            raise

    print("📊 Checking registered commands...")
    for cmd in bot.tree.walk_commands():
        print(f"📌 Registered command: {cmd.name}, guilds: {cmd._guild_ids}")
    print(
        f"📊 Total commands registered: {len(list(bot.tree.walk_commands()))}"
    )


def run() -> None:
    """Synchronous entry point used by ``main.py`` / ``python -m bot``."""
    bot = create_bot()
    bot.setup_hook = lambda: _setup_hook(bot)  # type: ignore[assignment]
    print("Starting bot...")
    bot.run(DISCORD_TOKEN)


__all__ = ["run"]
