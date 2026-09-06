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

"""Bootstrap only: build the Bot, wire up the global hooks that live in core/, load every cog
from cogs/, and run. Nothing feature-specific belongs here - commands go in a cog, shared state
goes in core/state.py.
"""

# sys and types are imported first because the audioop stub below must exist
# before discord.py is loaded. audioop was removed in Python 3.13, but older
# versions of discord.py reference it at import time for optional voice support.
# Inserting a dummy module prevents an ImportError on modern Python versions.
import sys
import types

try:
    __import__("audioop")
except ImportError:
    sys.modules["audioop"] = types.ModuleType("audioop")

import asyncio
import contextlib
import traceback
from pathlib import Path

import discord
from discord.ext import commands

# core.config loads the .env file (python-dotenv) the moment it is imported, so nothing needs to
# run before these imports - keeping them contiguous also keeps main.py free of E402.
from core import config as cfg
from core import errors, state
from core import permsHelperFuncs as perms
from core import xpHelperFuncs as xp

COGS_DIR = Path(__file__).resolve().parent / "cogs"


async def get_prefix(bot, message):
    """Fetch the command prefix for the guild that sent message.
    Returns the default prefix when the guild has no saved setting or the message is a DM."""
    if not message.guild:
        return "?"
    doc = await state.settings_col.find_one({"guild": str(message.guild.id)})
    return doc.get("prefix", "?") if doc else "?"


# All intents are required for member join events, message content, and invite tracking
intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True),
    # The Info cog provides its own paginated help command
    help_command=None,
)

# Global hooks are plain functions in core/ and are *registered* here against the live bot
# (never decorated inside core/), so those modules stay importable without a Bot.
# Order matters: discord.py runs checks in registration order and stops at the first failure.
bot.add_check(perms.global_lock_check)
bot.add_check(perms.ensure_guild_context)
bot.add_check(perms.check_disabled)
bot.add_listener(xp.on_command_completion, "on_command_completion")

# on_ready is dispatched again after every reconnect; the one-time work below must not repeat.
_ready_done = False


@bot.event
async def on_message(message):
    """The ONLY place that dispatches commands. Feature cogs register their own on_message
    listeners purely for side effects (AFK, sticky notes, quack counting, ...) and discord.py
    delivers the same message to all of them independently - calling process_commands anywhere
    else would run every command twice."""
    await bot.process_commands(message)


@bot.event
async def on_ready():
    """Bot-level ready work: presence, the shared aiohttp session, and slash command syncing.
    Every feature cog has its own on_ready listener for the state it owns."""
    global _ready_done
    if _ready_done:
        return
    _ready_done = True
    print(f"Logging in as {bot.user}...")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=cfg.BOT_ADMIN_NAME))
    state.http_session()
    asyncio.create_task(sync_hybrid_commands())


async def sync_hybrid_commands():
    """Sync the hybrid command tree per guild, then globally, backing off on rate limits."""
    await asyncio.sleep(15)
    for guild in bot.guilds:
        if not isinstance(guild, discord.Guild):
            continue
        try:
            await bot.tree.sync(guild=guild)
            print(f"✅ Commands synced for guild {guild.name}")
            await asyncio.sleep(15)
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = e.retry_after if hasattr(e, "retry_after") else 120
                print(f"⚠️ Guild sync rate limited for {guild.name}, waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                try:
                    await bot.tree.sync(guild=guild)
                    print(f"✅ Commands synced for {guild.name} after retry")
                except Exception as retry_e:
                    print(f"❌ Guild sync retry failed for {guild.name}: {retry_e}")
            else:
                print(f"❌ Failed to sync commands for guild {guild.name}: {e}")
        except Exception as e:
            print(f"❌ Failed to sync commands for guild {guild.name}: {e}")
    await asyncio.sleep(30)
    try:
        await bot.tree.sync()
        print("✅ Global commands synced!")
        print(f"🎉 Bot ready! Logged in as {bot.user}")
    except discord.HTTPException as e:
        if e.status == 429:
            retry_after = e.retry_after if hasattr(e, "retry_after") else 300
            print(f"⚠️ Global sync rate limited, waiting {retry_after}s...")
            await asyncio.sleep(retry_after)
            try:
                await bot.tree.sync()
                print("✅ Global commands synced after retry!")
                print(f"🎉 Bot ready! Logged in as {bot.user}")
            except Exception as retry_e:
                print(f"❌ Global sync retry failed: {retry_e}")
        else:
            print(f"❌ Failed to sync global commands: {e}")
    except Exception as e:
        print(f"❌ Failed to sync global commands: {e}")

    if cfg.DEBUG_COMMANDS:
        cmds = list(bot.tree.walk_commands())
        print(f"📊 Total app commands registered: {len(cmds)}")


@bot.event
async def on_disconnect():
    """Fired whenever the bot's WebSocket drops. Discord.py will reconnect automatically."""
    print("⚠️ Bot disconnected from Discord. Will attempt reconnect soon.")


@bot.event
async def on_command_error(ctx, error):
    await errors.handle_command_error(bot, ctx, error)


@bot.event
async def on_app_command_error(interaction: discord.Interaction, error):
    await errors.handle_app_command_error(bot, interaction, error)


@bot.event
async def on_close():
    """Called by discord.py whenever the bot's websocket connection is closing.
    This is a secondary cleanup path. Primary cleanup (extension unloading and
    session close) happens in main()'s finally block, which is always awaited.
    This handler is kept as a belt-and-suspenders fallback for any session that
    was not already closed by the time discord.py fires the close event."""
    if state.session is not None and not state.session.closed:
        await state.session.close()


def discover_cogs() -> list[str]:
    """Return the extension names for every cogs/*.py module, skipping templates (leading '_')."""
    return sorted(f"cogs.{path.stem}" for path in COGS_DIR.glob("*.py") if not path.stem.startswith("_"))


async def load_all_cogs(bot: commands.Bot) -> list[str]:
    """Load every cog extension. One cog failing to load is logged and does not stop the others.
    Returns the names of the extensions that loaded successfully."""
    loaded = []
    for extension in discover_cogs():
        try:
            await bot.load_extension(extension)
            loaded.append(extension)
        except Exception as e:
            print(f"❌ Failed to load extension {extension}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"📦 Loaded {len(loaded)} cog(s): {', '.join(name.removeprefix('cogs.') for name in loaded)}")
    return loaded


async def main() -> None:
    """Async entry point. Using ``async with bot`` guarantees bot.close() is called even if
    start() raises. Closing the bot unloads every extension, which runs each cog's
    cog_unload() and cancels the task loops that cog owns - there is no hardcoded list of
    loops to keep in sync any more. The finally block then makes sure nothing is left behind:

    1. Datadog tracer abandoned-span logs: the short task loops are almost always mid-pymongo
       query when Ctrl+C arrives. Cancelling them (via cog_unload) before Python's atexit hooks
       run stops ddtrace from logging abandoned spans.
    2. Unclosed aiohttp session warning: discord.py dispatches on_close as a fire-and-forget
       task, so it may not complete before asyncio.run tears down the event loop. Closing the
       session here guarantees it is awaited and truly finished."""
    try:
        async with bot:
            await load_all_cogs(bot)
            print(f"📊 Total commands registered: {len(list(bot.tree.walk_commands()))}")
            if cfg.DEBUG_COMMANDS:
                print("🔧 Bot initialized with built in tree")
                print(f"🔧 Bot object: {bot}")
                print(f"🔧 Tree object: {bot.tree}")
            await bot.start(cfg.TOKEN)
    finally:
        # bot.close() (run by `async with bot`) already unloads every extension; this only
        # catches anything that is somehow still registered.
        for extension in tuple(bot.extensions):
            with contextlib.suppress(Exception):
                await bot.unload_extension(extension)
        # Give cancellations a moment to propagate through any iteration that
        # is currently suspended at an await before the event loop closes.
        await asyncio.sleep(0.1)
        if state.session is not None and not state.session.closed:
            await state.session.close()


if __name__ == "__main__":
    print("Starting bot...  (press Ctrl+C to shut down gracefully)")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Shutdown complete.")
