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

"""Bot control and diagnostics: lock/override, one-time message channels, the debug command,
and the watchdog heartbeat loop."""

import ast
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

from core import config as cfg
from core import permsHelperFuncs as perms
from core import state

# Maps guild_id -> channel_id -> True for channels that delete messages after one read
onetime_channels = {}


# heartbeat.txt lives in the project root (healthcheck.sh looks for it there), one level above cogs/
HEARTBEAT_FILE = str(Path(__file__).resolve().parent.parent / "heartbeat.txt")


def _write_heartbeat_file() -> None:
    with open(HEARTBEAT_FILE, "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())


HEARTBEAT_ERROR_LOG_INTERVAL = timedelta(minutes=5)


async def run_flake8_lint(base_dir):
    """Run flake8 on base_dir as a subprocess and return a list of issue strings.
    Returns an empty list when the code is clean or flake8 is unavailable."""
    try:
        config_path = os.path.join(base_dir, "flake8_config.txt")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "flake8",
            base_dir,
            "--config",
            config_path,
            "--exclude=.venv,__pycache__,build,dist",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return ["⚠️ `flake8` lint check timed out (30s)."]
        if process.returncode != 0 and stdout:
            return [f"❗ {line}" for line in stdout.decode().strip().splitlines()]
        return []
    except FileNotFoundError:
        return ["⚠️ `flake8` module not found; make sure it's in your `requirements.txt`."]


_DEBUG_RED_SEP = "🔴" * 20


class Admin(commands.Cog):
    """Bot control and diagnostics: lock/override, one-time message channels, the debug command,
    and the watchdog heartbeat loop."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False
        # Last time a heartbeat write failure was logged (rate-limits the error message)
        self._last_heartbeat_error_log: datetime | None = None

    @tasks.loop(seconds=15)
    async def write_heartbeat(self):
        """Write the current UTC time to a heartbeat file so an external watchdog can
        detect a hung event loop (process alive under systemd but no longer servicing
        async tasks), which a plain 'is the process running' check would miss. Keeps
        retrying every 15s even after a write failure (e.g. a transient full disk can
        resolve itself), but only logs about it once per HEARTBEAT_ERROR_LOG_INTERVAL
        so a persistent failure doesn't flood stdout/journald."""
        try:
            await asyncio.to_thread(_write_heartbeat_file)
        except OSError as e:
            now = datetime.now(timezone.utc)
            if (
                self._last_heartbeat_error_log is None
                or now - self._last_heartbeat_error_log >= HEARTBEAT_ERROR_LOG_INTERVAL
            ):
                print(f"[write_heartbeat error] {e}")
                self._last_heartbeat_error_log = now

    async def load_onetime_channels(self):
        """Load one time read channel configuration from the database into the onetime_channels dict."""
        try:
            cursor = state.settings_col.find({"onetime_channels": {"$exists": True}})
            async for doc in cursor:
                guild_id = doc["guild"]
                onetime_data = doc.get("onetime_channels", {})
                if onetime_data:
                    if guild_id not in onetime_channels:
                        onetime_channels[guild_id] = {}
                    onetime_channels[guild_id].update(onetime_data)
            print(f"[One-Time Channels] Loaded one-time channels for {len(onetime_channels)} guilds")
        except Exception as e:
            print(f"[One-Time Channels] Error loading one-time channels: {e}")

    @commands.command()
    @perms.staff_only()
    async def debug(self, ctx):
        """Run syntax checks, flake8 lint, and recent error summary. Detailed output goes to the console.
        Only a brief result count is posted to Discord to avoid leaking internal error strings."""
        await ctx.send("🧪 Running debug checks - full details are printed to the **bot console**.")

        async def run_debug_checks():
            # Scan the whole project (one level above cogs/), not just this package
            base_dir = str(Path(__file__).resolve().parent.parent)

            def syntax_check():
                syntax_errors = []
                for root, _, files in os.walk(base_dir):
                    if any(skip in root for skip in (".venv", "__pycache__", "build", "dist")):
                        continue
                    for file in files:
                        if not file.endswith(".py"):
                            continue
                        file_path = os.path.join(root, file)
                        short_path = os.path.relpath(file_path, base_dir)
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                source = f.read()
                            ast.parse(source, filename=file_path)
                            compile(source, file_path, "exec")
                        except SyntaxError as e:
                            syntax_errors.append(f"SyntaxError in {short_path} at line {e.lineno}: {e.msg}")
                        except Exception as e:
                            syntax_errors.append(f"{type(e).__name__} in {short_path}: {e}")
                return syntax_errors

            syntax_errors = await asyncio.to_thread(syntax_check)
            lint_errors = await run_flake8_lint(base_dir)
            return syntax_errors + lint_errors

        code_issues = await run_debug_checks()

        if state.recent_errors:
            print(f"\n{_DEBUG_RED_SEP}")
            print(
                f"❌❌❌  DEBUG: {len(state.recent_errors)} RECENT RUNTIME ERROR(S) - triggered by {ctx.author} ({ctx.author.id})  ❌❌❌"
            )
            print(_DEBUG_RED_SEP)
            for err in state.recent_errors:
                time_str = err["time"].strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n  [{time_str}] Command: {err['command']}")
                print(f"  Error:   {err['error']}")
                print(f"  Traceback:\n{err['traceback']}")
            print(f"{_DEBUG_RED_SEP}\n")
            await ctx.send(
                f"🔴 **{len(state.recent_errors)} recent runtime error(s) found.** Full tracebacks are in the **bot console**."
            )

        if code_issues:
            print(f"\n{_DEBUG_RED_SEP}")
            print(
                f"❌❌❌  DEBUG: {len(code_issues)} CODE ISSUE(S) - triggered by {ctx.author} ({ctx.author.id})  ❌❌❌"
            )
            print(_DEBUG_RED_SEP)
            for err in code_issues:
                print(f"  {err}")
            print(f"{_DEBUG_RED_SEP}\n")
            await ctx.send(f"❗ **{len(code_issues)} code issue(s) found.** Details are in the **bot console**.")

        if not code_issues and not state.recent_errors:
            await ctx.send("✅ No syntax, lint, or recent runtime issues found.")

    @commands.command()
    @perms.staffperm("stopbot")
    @perms.staff_only()
    async def stop(self, ctx):
        """Lock the bot for this guild so only the override command works. Lists authorized users."""
        state.bot_locks[str(ctx.guild.id)] = True
        names = []
        for uid in cfg.AUTHORIZED_USER_IDS:
            m = ctx.guild.get_member(uid)
            if m:
                names.append(m.display_name)
            else:
                u = self.bot.get_user(uid)
                if u:
                    names.append(u.name)
        if names:
            name_list = "\n".join(f"• {n}" for n in sorted(names))
            await ctx.send(f"🔒 Bot locked. Use 'override' by an authorized user listed below:\n{name_list}")
        else:
            await ctx.send("🔒 Bot locked. Use 'override' by an authorized user to unlock.")

    @commands.command()
    @perms.staffperm("config")
    @perms.staff_only()
    async def onetime(self, ctx, channel: discord.TextChannel = None):
        """Configure a channel so non staff members can only send one message before losing send permissions."""
        target_channel = channel or ctx.channel
        guild_id = str(ctx.guild.id)
        channel_id = str(target_channel.id)
        if guild_id not in onetime_channels:
            onetime_channels[guild_id] = {}
        if channel_id not in onetime_channels[guild_id]:
            onetime_channels[guild_id][channel_id] = {}
            await state.settings_col.update_one(
                {"guild": guild_id}, {"$set": {f"onetime_channels.{channel_id}": {}}}, upsert=True
            )
            embed = discord.Embed(
                title="✅ One Time Message Channel Set Up",
                description=f"**{target_channel.mention}** is now a one time message channel.\n\nNon staff members can send **only one message** in this channel. After their first message, they will lose permission to send more messages.\n\nStaff members are exempt and can continue messaging normally.\n\nUse `.restore <user>` to give a user back their messaging permissions.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
            try:
                await target_channel.send(
                    "🔔 **This is now a one time message channel!**\nNon staff members can send only one message here. Staff can restore permissions with `.restore <user>`."
                )
            except (discord.HTTPException, discord.Forbidden):
                pass
        else:
            await ctx.send(f"⚠️ {target_channel.mention} is already a one time message channel.")

    @commands.command()
    @perms.staffperm("config")
    @perms.staff_only()
    async def restore(self, ctx, member: discord.Member, channel: discord.TextChannel = None):
        """Restore send permissions for a member in a one time channel so they can message again."""
        target_channel = channel or ctx.channel
        guild_id = str(ctx.guild.id)
        channel_id = str(target_channel.id)
        user_id = str(member.id)
        if guild_id not in onetime_channels or channel_id not in onetime_channels[guild_id]:
            return await ctx.send(f"⚠️ {target_channel.mention} is not a one time message channel.")
        if user_id in onetime_channels[guild_id][channel_id]:
            del onetime_channels[guild_id][channel_id][user_id]
            await state.settings_col.update_one(
                {"guild": guild_id}, {"$unset": {f"onetime_channels.{channel_id}.{user_id}": ""}}
            )
        try:
            await target_channel.set_permissions(
                member, send_messages=None, reason="One time message permission restored"
            )
            embed = discord.Embed(
                title="✅ Permissions Restored",
                description=f"{member.mention} can now send messages in {target_channel.mention} again.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
        except Exception as e:
            print(f"[ERROR] restore command: {type(e).__name__}: {e}")
            await ctx.send("❌ Failed to restore permissions. Please try again.")

    @commands.command()
    @perms.staffperm("config")
    @perms.staff_only()
    async def disableonetime(self, ctx, channel: discord.TextChannel = None):
        """Remove one time channel restrictions and restore send permissions for all affected members."""
        target_channel = channel or ctx.channel
        guild_id = str(ctx.guild.id)
        channel_id = str(target_channel.id)
        if guild_id not in onetime_channels or channel_id not in onetime_channels[guild_id]:
            return await ctx.send(f"⚠️ {target_channel.mention} is not a one time message channel.")
        del onetime_channels[guild_id][channel_id]
        await state.settings_col.update_one({"guild": guild_id}, {"$unset": {f"onetime_channels.{channel_id}": ""}})
        try:
            for target, overwrite in target_channel.overwrites.items():
                if (
                    isinstance(target, discord.Member)
                    and (not await perms.has_staff_role(target, target_channel.guild))
                    and overwrite.send_messages is False
                ):
                    await target_channel.set_permissions(target, send_messages=None)
            embed = discord.Embed(
                title="✅ One Time Channel Disabled",
                description=f"{target_channel.mention} is no longer a one time message channel.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
        except Exception as e:
            print(f"[ERROR] disableonetime command: {type(e).__name__}: {e}")
            await ctx.send("❌ Failed to disable one time restrictions. Please try again.")

    @commands.command()
    async def override(self, ctx):
        """Unlock the bot for this guild. Only authorized user IDs from cfg.AUTHORIZED_USER_IDS may use this."""
        if ctx.author.id in cfg.AUTHORIZED_USER_IDS:
            state.bot_locks[str(ctx.guild.id)] = False
            await ctx.send("🚀 Bot unlocked!")
        else:
            await ctx.send("❌ You don't have permission.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the watchdog heartbeat and load one-time channel state from MongoDB (runs once)."""
        if self._ready_done:
            return
        self._ready_done = True
        if not self.write_heartbeat.is_running():
            self.write_heartbeat.start()
        await self.load_onetime_channels()

    @commands.Cog.listener()
    async def on_message(self, message):
        """Enforce one-time message channels: a non-staff member loses send permission after one message."""
        if message.author.bot:
            return
        if not message.guild:
            return
        if message.type in cfg.BOOST_MESSAGE_TYPES:
            return
        try:
            guild_id = str(message.guild.id)
            channel_id = str(message.channel.id)
            if (
                guild_id in onetime_channels
                and channel_id in onetime_channels[guild_id]
                and (not await perms.has_staff_role(message.author, message.guild))
            ):
                user_id = str(message.author.id)
                if user_id not in onetime_channels[guild_id][channel_id]:
                    now = datetime.now(timezone.utc)
                    onetime_channels[guild_id][channel_id][user_id] = now
                    await state.settings_col.update_one(
                        {"guild": guild_id}, {"$set": {f"onetime_channels.{channel_id}.{user_id}": now}}, upsert=True
                    )
                    try:
                        await message.channel.set_permissions(
                            message.author, send_messages=False, reason="One time message used"
                        )
                        await message.channel.send(
                            f"⚠️ {message.author.mention} has used their one time message in this channel. Staff can restore permissions with `.restore`."
                        )
                    except Exception as perm_error:
                        print(f"[One time permission error] {perm_error}")
        except Exception as e:
            print(f"[One time message error] {e}")

    def cog_unload(self):
        """Cancel the heartbeat loop this cog owns."""
        if self.write_heartbeat.is_running():
            self.write_heartbeat.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
