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

"""Guild configuration: the configure wizard, editconfig/viewconfig/resetconfig, prefix, command
toggles, maintenance mode, welcome/boost message testing, and the guild-join / welcome / boost listeners."""

import asyncio
import re
import traceback
from datetime import datetime, timezone

import discord
from discord.ext import commands

from core import config as cfg
from core import errors, state
from core import permsHelperFuncs as perms
from core import xpHelperFuncs as xp


class _ConfigAborted(Exception):
    """Raised to unwind out of the .configure wizard (timeout/cancel/attempts exhausted)."""


_CONFIG_SKIP = object()


async def _prompt_config_value(ctx, check, question, parse_fn, *, allow_skip=True, max_attempts=3):
    """Ask `question` and wait for a reply, retrying invalid input up to `max_attempts` times.

    parse_fn(content) -> (True, value) on success or (False, error_message) on
    invalid input. Invalid input re-asks the same question (up to max_attempts
    total) instead of aborting the wizard on the first bad reply. Timeout and
    `cancel` still abort immediately - only bad input is retried.
    """
    await ctx.send(question)
    attempts_left = max_attempts
    while True:
        try:
            msg = await ctx.bot.wait_for("message", timeout=90, check=check)
        except asyncio.TimeoutError:
            await ctx.send("⌛ Timed out. Configuration cancelled.")
            raise _ConfigAborted
        content = msg.content.strip()
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
        if content.lower() == "cancel":
            await ctx.send("❌ Configuration cancelled.")
            raise _ConfigAborted
        if allow_skip and content.lower() == "skip":
            return _CONFIG_SKIP
        ok, result = parse_fn(content)
        if ok:
            return result
        attempts_left -= 1
        if attempts_left <= 0:
            await ctx.send(f"{result} No attempts remaining - configuration cancelled.")
            raise _ConfigAborted
        plural = "s" if attempts_left != 1 else ""
        await ctx.send(f"{result} You have {attempts_left} attempt{plural} left.\n{question}")


class GuildConfig(commands.Cog):
    """Guild configuration: the configure wizard, editconfig/viewconfig/resetconfig, prefix, command
    toggles, maintenance mode, welcome/boost message testing, and the guild-join / welcome / boost listeners."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    @commands.command(name="configure", aliases=["config"])
    async def configure(self, ctx):
        """Interactive setup wizard that walks guild admins through all bot configuration options."""
        settings = await state.settings_col.find_one({"guild": str(ctx.guild.id)}) or {}
        staff_role_id = settings.get("staff_role")
        if not staff_role_id:
            if ctx.author != ctx.guild.owner:
                return await ctx.send("❌ Only the **server owner** can run `.configure` until a staff role is set.")
        elif ctx.author != ctx.guild.owner and (not ctx.author.guild_permissions.administrator):
            if not await perms.is_staff_user(ctx):
                return await ctx.send("❌ Only staff members can use this command.")
            if not await perms.check_staff_perm(ctx, "config"):
                return await ctx.send("❌ You don't have permission to configure the self.bot.")
        prompts = {
            "welcome_channel": "Enter the **welcome channel ID** (type `skip` to skip - must be paired with a welcome message):",
            "welcome_message": "Enter the **welcome message** (supports `{mention}`, `{username}`, `{server}`, `{membercount}` - type `skip` to skip):",
            "boost_channel": "Enter the **boost channel ID** (type `skip` to skip - must be paired with a boost message):",
            "boost_message": "Enter the **boost message** (supports `{mention}`, `{username}`, `{server}`, `{boostcount}` - type `skip` to skip):",
            "ALLOWED_DUCK_CHANNELS": "Enter **allowed channel IDs for `.duck`** (comma/space separated - type `skip` to allow everywhere):",
            "ROLE_ID": "Enter **role IDs to award for passing `.duckquiz`** (comma/space separated - type `skip` to skip):",
            "QUIZ_CHANNEL": "Enter **channel IDs where `.duckquiz` can run** (comma/space separated - type `skip` to allow everywhere):",
            "allowed_channel_id": "Enter **channel IDs where DuckGPT is allowed** (comma/space separated - type `skip` to allow everywhere):",
            "economy_channel": "Enter the **channel ID where economy commands are allowed** (type `skip` to allow everywhere):",
            "log_channel": "Enter the **log channel ID** for moderation logs (type `skip` to disable):",
            "DROP_CHANNELS": "Enter **channel IDs where `.drop` can be used by members** (comma/space separated - type `skip` to allow everywhere):",
            "QUACK_CHANNELS": "Enter **channel IDs where the quack counter should activate** (comma/space separated - type `skip` to count everywhere):",
            "pond_royalty_role": "Enter the role for the **Pond Royalty** shop item (mention `@Role` or paste the role ID, type `skip` to disable Pond Royalty in this server):",
        }
        config_data = {"guild": str(ctx.guild.id)}

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send(
            "🛠 Starting configuration. Type `cancel` to abort at any time. Invalid answers get up to 3 tries."
        )

        def _parse_staff_role(content):
            match = re.search(r"\d+", content)
            if not match:
                return False, "❌ Please mention a valid role or provide its ID."
            staff_role = ctx.guild.get_role(int(match.group()))
            if not staff_role:
                return False, "❌ That role wasn't found in this server."
            return True, staff_role

        try:
            staff_role = await _prompt_config_value(
                ctx,
                check,
                "Enter the **staff role** (mention it like `@Staff` or paste the role ID). **Required**:",
                _parse_staff_role,
                allow_skip=False,
            )
        except _ConfigAborted:
            return
        await state.settings_col.update_one(
            {"guild": str(ctx.guild.id)}, {"$set": {"staff_role": staff_role.id}}, upsert=True
        )

        for key, question in prompts.items():

            def _parse_value(content, key=key):
                if not content:
                    return False, f"❌ `{key}` cannot be blank. Type `skip` to skip."
                if key in ["log_channel", "economy_channel", "welcome_channel", "boost_channel"]:
                    if not content.isdigit():
                        return False, f"❌ Please provide a valid channel ID for `{key}`."
                    return True, int(content)
                if key in ["welcome_message", "boost_message"]:
                    return True, content
                if key == "pond_royalty_role":
                    role_match = re.search(r"\d+", content)
                    if not role_match:
                        return False, "❌ Please mention a valid role or provide its ID."
                    pond_role = ctx.guild.get_role(int(role_match.group()))
                    if not pond_role:
                        return False, "❌ That role wasn't found in this server."
                    return True, pond_role.id
                if content.lower() == "all":
                    return True, "all"
                try:
                    ids = [int(x) for x in re.split(r"[,\s]+", content) if x.isdigit()]
                except ValueError:
                    return False, f"❌ Couldn't parse IDs for `{key}`."
                if not ids:
                    return False, f"❌ No valid IDs entered for `{key}`."
                return True, ids

            try:
                value = await _prompt_config_value(ctx, check, question, _parse_value)
            except _ConfigAborted:
                return
            if value is not _CONFIG_SKIP:
                config_data[key] = value
        # Welcome and boost each require both channel and message
        welcome_ch_set = "welcome_channel" in config_data
        welcome_msg_set = "welcome_message" in config_data
        if welcome_ch_set != welcome_msg_set:
            return await ctx.send(
                "❌ Welcome channel and welcome message must both be provided or both skipped. Please run `.configure` again."
            )
        boost_ch_set = "boost_channel" in config_data
        boost_msg_set = "boost_message" in config_data
        if boost_ch_set != boost_msg_set:
            return await ctx.send(
                "❌ Boost channel and boost message must both be provided or both skipped. Please run `.configure` again."
            )
        await state.config_col.update_one({"guild": config_data["guild"]}, {"$set": config_data}, upsert=True)
        await ctx.send(f"✅ Configuration saved successfully! Staff role set to {staff_role.mention}.", delete_after=7)
        await perms.log_action(ctx, f"Configuration updated for {ctx.guild.name}", action_type="configure")

    @configure.error
    async def configure_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await errors.send_hybrid_error(
                ctx, content="❌ You don't have permission to use this command.", delete_after=7
            )
        elif isinstance(error, commands.CheckFailure):
            await errors.send_hybrid_error(ctx, content="❌ Only staff members can use this command.", delete_after=7)
        else:
            root_error = errors.unwrap_command_error(error)
            if isinstance(root_error, commands.CommandOnCooldown):
                return await errors.send_hybrid_error(
                    ctx, content=f"❌ You are on cooldown. Try again in {root_error.retry_after:.2f}s"
                )
            print(f"[ERROR] configure_error: {root_error}")
            traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
            await errors.send_hybrid_error(
                ctx,
                content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + ".",
                delete_after=10,
            )

    @commands.command(name="editconfig", aliases=["editconfiguration"])
    @perms.staffperm("config")
    @perms.staff_only()
    async def editconfig(self, ctx, *, args: str | None = None):
        """Edit a single configuration value by name without running the full configure wizard."""

        def norm(s):
            return re.sub("\\s+", "_", s.strip().lower())

        valid_settings = {
            "welcome_channel": {"desc": "Welcome channel", "key": "welcome_channel"},
            "welcome_message": {"desc": "Welcome message", "key": "welcome_message"},
            "welcome_image": {"desc": "Welcome image URL", "key": "welcome_image"},
            "boost_channel": {"desc": "Boost channel", "key": "boost_channel"},
            "boost_message": {"desc": "Boost message", "key": "boost_message"},
            "allowed_duck_channels": {"desc": "Duck command allowed channels", "key": "ALLOWED_DUCK_CHANNELS"},
            "role_id": {"desc": "Quiz reward role", "key": "ROLE_ID"},
            "quiz_channel": {"desc": "Quiz allowed channels", "key": "QUIZ_CHANNEL"},
            "allowed_channel_id": {"desc": "DuckGPT allowed channels", "key": "allowed_channel_id"},
            "economy_channel": {"desc": "Economy channel", "key": "economy_channel"},
            "log_channel": {"desc": "Log channel", "key": "log_channel"},
            "drop_channels": {"desc": "Drop allowed channels", "key": "DROP_CHANNELS"},
            "quack_channels": {"desc": "Quack Counter Channels", "key": "QUACK_CHANNELS"},
            "pond_royalty_role": {"desc": "Pond Royalty shop role", "key": "pond_royalty_role"},
        }
        if not args:
            return await ctx.send("❌ Please specify a setting and value, e.g. `editconfig welcome_channel #general`")
        parts = args.split()
        idx = len(parts)
        for i in range(1, len(parts)):
            p = parts[i]
            if p.isdigit() or p.startswith(("<#", "<@&")) or (p.lower() in ("none", "null", "remove", "delete", "all")):
                idx = i
                break
        raw_setting = " ".join(parts[:idx]).strip()
        setting_norm = norm(raw_setting)
        value = " ".join(parts[idx:]).strip() if idx < len(parts) else None
        if setting_norm not in valid_settings:
            pretty_list = "\n".join(f"• `{info['key']}` - {info['desc']}" for info in valid_settings.values())
            embed = discord.Embed(
                title="⚙️ Invalid Setting",
                description=f"❌ **`{raw_setting}`** is not a valid configuration key.\n\n**Available settings:**\n"
                + pretty_list,
                color=discord.Color.red(),
            )
            embed.set_footer(text="Tip: You can type settings with spaces (e.g. 'welcome message')")
            return await ctx.send(embed=embed)
        config = await state.config_col.find_one({"guild": str(ctx.guild.id)}) or {"guild": str(ctx.guild.id)}
        canonical_key = valid_settings[setting_norm]["key"]
        desc = valid_settings[setting_norm]["desc"]
        if value and value.lower() in ["none", "null", "remove", "delete"]:
            await state.config_col.update_one({"guild": config["guild"]}, {"$unset": {canonical_key: ""}})
            await ctx.send(f"🗑 **{desc}** has been removed from the configuration.")
            await perms.log_action(ctx, f"{desc} removed from {ctx.guild.name}", action_type="editconfig")
            return
        try:
            if canonical_key in ["welcome_message", "boost_message"] and (not value):
                if canonical_key == "welcome_message":
                    placeholder_info = "🧩 You can use these placeholders in your welcome message:\n`{username}` - Member's username\n`{mention}` - Mention the member\n`{server}` - Server name\n`{membercount}` - Current member count\n\n"
                else:
                    placeholder_info = "🧩 You can use these placeholders in your boost message:\n`{username}` - Booster's username\n`{mention}` - Mention the booster\n`{server}` - Server name\n`{boostcount}` - Current server boost count\n\n"
                await ctx.send(
                    placeholder_info
                    + f"📝 Please enter the new {desc.lower()} below.\nYou can type `cancel` to abort or `none` to remove it."
                )

                def check(m):
                    return m.author == ctx.author and m.channel == ctx.channel

                try:
                    msg = await self.bot.wait_for("message", timeout=180, check=check)
                except asyncio.TimeoutError:
                    return await ctx.send("⌛ Timed out. Configuration cancelled.")
                content = msg.content.strip()
                if content.lower() == "cancel":
                    return await ctx.send("❌ Edit cancelled.")
                elif content.lower() in ["none", "null", "remove", "delete"]:
                    await state.config_col.update_one({"guild": config["guild"]}, {"$unset": {canonical_key: ""}})
                    await ctx.send(f"🗑 **{desc}** has been removed from the configuration.")
                    await perms.log_action(ctx, f"{desc} removed from {ctx.guild.name}", action_type="editconfig")
                    return
                config[canonical_key] = content
                await msg.delete()
                if canonical_key == "boost_message":
                    await ctx.send(
                        "✨ Would you like me to react to each boost message with a custom emoji?\nReact to **this message** with the emoji you want, or type `none` to skip."
                    )

                    def emoji_check(reaction, user):
                        return user == ctx.author and reaction.message.channel == ctx.channel

                    try:
                        await ctx.send("⏳ Waiting for your emoji reaction or text reply...")
                        reaction_task = asyncio.create_task(
                            self.bot.wait_for("reaction_add", timeout=30, check=emoji_check)
                        )
                        message_task = asyncio.create_task(self.bot.wait_for("message", timeout=30, check=check))
                        done, pending = await asyncio.wait(
                            [reaction_task, message_task], return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in pending:
                            task.cancel()
                        result = next(iter(done)).result()
                        if isinstance(result, tuple):
                            reaction, _ = result
                            emoji = str(reaction.emoji)
                            config["boost_react_emoji"] = emoji
                            await ctx.send(f"✅ Set boost reaction emoji to {emoji}")
                        elif isinstance(result, discord.Message):
                            if result.content.lower().strip() != "none":
                                await ctx.send("⚠️ Invalid input, skipping emoji reaction setup.")
                            else:
                                await ctx.send("✅ No emoji reaction will be added to boost messages.")
                                config["boost_react_emoji"] = None
                    except asyncio.TimeoutError:
                        await ctx.send("⌛ No emoji selected, skipping reaction setup.")
                    except Exception as e:
                        await ctx.send(f"⚠️ Error while setting emoji: `{e}`")
            elif canonical_key == "pond_royalty_role":
                role_match = re.search(r"\d+", value or "")
                if not role_match:
                    return await ctx.send(f"❌ Please mention a valid role or provide its ID for `{desc}`.")
                pond_role = ctx.guild.get_role(int(role_match.group()))
                if not pond_role:
                    return await ctx.send("❌ That role wasn't found in this server.")
                config[canonical_key] = pond_role.id
            elif canonical_key in ["log_channel", "economy_channel", "welcome_channel", "boost_channel"]:
                match = re.search("\\d+", value or "")
                if not match:
                    return await ctx.send(f"❌ Please mention a valid channel or provide its ID for `{desc}`.")
                config[canonical_key] = int(match.group())
            elif canonical_key in ["welcome_message", "boost_message"]:
                config[canonical_key] = value
            elif canonical_key == "welcome_image":
                if not value or not value.startswith("http"):
                    return await ctx.send("❌ Please provide a valid image URL starting with `http`.")
                config[canonical_key] = value
            elif value and value.lower() == "all":
                config[canonical_key] = "all"
            else:
                ids = [int(x) for x in re.findall("\\d+", value or "")]
                if not ids:
                    return await ctx.send(f"❌ No valid IDs found for `{desc}`.")
                config[canonical_key] = ids
        except Exception as e:
            return await ctx.send(f"⚠️ Error updating config: `{e}`")
        await state.config_col.update_one({"guild": config["guild"]}, {"$set": config}, upsert=True)
        await ctx.send(f"✅ **{desc}** updated successfully!")
        await perms.log_action(ctx, f"{desc} updated in {ctx.guild.name}", action_type="editconfig")

    @editconfig.error
    async def editconfig_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await errors.send_hybrid_error(ctx, content="❌ You don't have permission to use this command.")
        elif isinstance(error, commands.CheckFailure):
            await errors.send_hybrid_error(ctx, content="❌ Only staff members can use this command.")
        else:
            root_error = errors.unwrap_command_error(error)
            if isinstance(root_error, commands.CommandOnCooldown):
                return await errors.send_hybrid_error(
                    ctx, content=f"❌ You are on cooldown. Try again in {root_error.retry_after:.2f}s"
                )
            print(f"[ERROR] editconfig_error: {root_error}")
            traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.command(name="viewconfig")
    @perms.staffperm("config")
    @perms.staff_only()
    async def viewconfig(self, ctx: commands.Context):
        """Display all current bot configuration values for this guild in an embed."""
        config = await state.config_col.find_one({"guild": str(ctx.guild.id)})
        if not config:
            return await ctx.send("⚠️ No configuration found for this server.")

        def format_ids(key):
            value = config.get(key)
            if value == "all":
                return "All channels"
            if not value:
                return "All channels" if "channel" in key.lower() else "Not set"
            if isinstance(value, list):
                return ", ".join(f"<#{i}>" if "channel" in key.lower() else f"<@&{i}>" for i in value)
            elif isinstance(value, int):
                return f"<#{value}>" if "channel" in key.lower() else f"<@&{value}>"
            return str(value)

        settings = await state.settings_col.find_one({"guild": str(ctx.guild.id)}) or {}
        staff_role_id = settings.get("staff_role")
        staff_role = ctx.guild.get_role(staff_role_id) if isinstance(staff_role_id, int) else None
        embed = discord.Embed(title="🔧 Server Configuration", color=discord.Color.blurple())
        embed.add_field(
            name="🛡 Staff Role",
            value=staff_role.mention if staff_role else "Not set" if not staff_role_id else str(staff_role_id),
            inline=False,
        )
        embed.add_field(name="👋 Welcome Channel", value=format_ids("welcome_channel"), inline=False)
        embed.add_field(
            name="👋 Welcome Message", value=config.get("welcome_message", "Not set (uses default)"), inline=False
        )
        embed.add_field(
            name="👋 Welcome Image URL", value=config.get("welcome_image", "Not set (no image)"), inline=False
        )
        embed.add_field(name="🚀 Boost Channel", value=format_ids("boost_channel"), inline=False)
        embed.add_field(name="🚀 Boost Message", value=config.get("boost_message", "Not set"), inline=False)
        embed.add_field(name="Duck Command Channels", value=format_ids("ALLOWED_DUCK_CHANNELS"), inline=False)
        embed.add_field(name="Quiz Role", value=format_ids("ROLE_ID"), inline=False)
        embed.add_field(name="Quiz Channel", value=format_ids("QUIZ_CHANNEL"), inline=False)
        embed.add_field(name="DuckGPT Allowed Channel", value=format_ids("allowed_channel_id"), inline=False)
        embed.add_field(name="Drop Channels", value=format_ids("DROP_CHANNELS"), inline=False)
        embed.add_field(name="Quack Counter Channels", value=format_ids("QUACK_CHANNELS"), inline=False)
        embed.add_field(name="Economy Channel", value=format_ids("economy_channel"), inline=False)
        embed.add_field(name="Log Channel", value=format_ids("log_channel"), inline=False)
        pond_royalty_role_id = config.get("pond_royalty_role")
        if pond_royalty_role_id:
            pond_role = ctx.guild.get_role(pond_royalty_role_id)
            pond_role_display = pond_role.mention if pond_role else f"<@&{pond_royalty_role_id}> (not found)"
        else:
            pond_role_display = "Not set (Pond Royalty hidden from shop)"
        embed.add_field(name="👑 Pond Royalty Role", value=pond_role_display, inline=False)
        await ctx.send(embed=embed)

    @viewconfig.error
    async def viewconfig_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("❌ Only staff members can use this command.")
        else:
            root_error = errors.unwrap_command_error(error)
            if isinstance(root_error, commands.CommandOnCooldown):
                return await errors.send_hybrid_error(
                    ctx, content=f"❌ You are on cooldown. Try again in {root_error.retry_after:.2f}s"
                )
            print(f"[ERROR] viewconfig_error: {root_error}")
            traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
            await ctx.send("⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + ".")

    @commands.command()
    @perms.staffperm("config")
    @perms.staff_only()
    async def resetconfig(self, ctx):
        """Delete all stored configuration for this guild and revert to defaults."""
        await state.config_col.delete_one({"guild": str(ctx.guild.id)})
        await ctx.send("🗑 Configuration has been completely reset for this server.")

    @commands.hybrid_command(name="setprefix", description="Change the bot prefix. Staff only.")
    @perms.staffperm("config")
    @perms.staff_only()
    async def setprefix(self, ctx, new: str):
        await state.settings_col.update_one({"guild": str(ctx.guild.id)}, {"$set": {"prefix": new}}, upsert=True)
        await ctx.send(f"✅ Prefix updated to `{new}`.")
        await perms.log_action(ctx, f"Prefix changed to {new}", action_type="setprefix")

    @commands.hybrid_command(name="disable", description="Disable a command or a category. Staff only.")
    @perms.staffperm("toggle_commands")
    @perms.staff_only()
    async def disable(self, ctx, target: str):
        guild_id = str(ctx.guild.id)
        doc = await state.disabled_col.find_one({"guild": guild_id}) or {
            "disabled_commands": [],
            "disabled_categories": [],
        }
        commands_set = set(doc["disabled_commands"])
        categories_set = set(doc["disabled_categories"])
        target = target.lower()
        # Resolve aliases (e.g. `bal`) to the canonical command name that check_disabled compares against
        command = self.bot.get_command(target)
        if command is not None:
            target = command.name
        if target in cfg.UNDISABLEABLE_COMMANDS:
            return await ctx.send(f"❌ `{target}` can't be disabled - it's needed to manage the bot.")
        all_cmds = [c.name for c in self.bot.commands]
        all_cats = list(cfg.COMMAND_CATEGORIES)
        if target in all_cmds:
            if target in commands_set:
                return await ctx.send(f"❌ `{target}` is already disabled.")
            commands_set.add(target)
            await ctx.send(f"✅ Disabled command `{target}`.")
        elif target in all_cats:
            if target in categories_set:
                return await ctx.send(f"❌ Category `{target}` is already disabled.")
            categories_set.add(target)
            await ctx.send(f"✅ Disabled category `{target}`.")
        else:
            return await ctx.send("⚠️ Unknown command or category.")
        await state.disabled_col.update_one(
            {"guild": guild_id},
            {"$set": {"disabled_commands": list(commands_set), "disabled_categories": list(categories_set)}},
            upsert=True,
        )

    @commands.hybrid_command(name="enable", description="Enable a disabled command or category. Staff only.")
    @perms.staffperm("toggle_commands")
    @perms.staff_only()
    async def enable(self, ctx, target: str):
        guild_id = str(ctx.guild.id)
        doc = await state.disabled_col.find_one({"guild": guild_id}) or {
            "disabled_commands": [],
            "disabled_categories": [],
        }
        commands_set = set(doc["disabled_commands"])
        categories_set = set(doc["disabled_categories"])
        target = target.lower()
        if target in commands_set:
            commands_set.remove(target)
            await ctx.send(f"✅ Enabled command `{target}`.")
        elif target in categories_set:
            categories_set.remove(target)
            await ctx.send(f"✅ Enabled category `{target}`.")
        else:
            return await ctx.send("❌ That wasn't disabled.")
        await state.disabled_col.update_one(
            {"guild": guild_id},
            {"$set": {"disabled_commands": list(commands_set), "disabled_categories": list(categories_set)}},
            upsert=True,
        )

    @commands.hybrid_command(
        name="listdisabled", description="List currently disabled commands and categories. Staff only."
    )
    @perms.staffperm("toggle_commands")
    @perms.staff_only()
    async def listdisabled(self, ctx):
        doc = await state.disabled_col.find_one({"guild": str(ctx.guild.id)})
        disabled_cmds = doc.get("disabled_commands", []) if doc else []
        disabled_cats = doc.get("disabled_categories", []) if doc else []
        if not disabled_cmds and not disabled_cats:
            return await ctx.send("✅ No commands or categories are currently disabled.")
        embed = discord.Embed(title="🔒 Disabled Features", color=discord.Color.red())
        if disabled_cmds:
            embed.add_field(name="Commands", value="\n".join(f"`{cmd}`" for cmd in disabled_cmds), inline=False)
        if disabled_cats:
            embed.add_field(name="Categories", value="\n".join(f"`{cat}`" for cat in disabled_cats), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="maintenance", description="Toggle maintenance mode (staff only access).")
    @perms.staffperm("config")
    @perms.staff_only()
    async def maintenance(self, ctx, action: str | None = None):
        guild_id = str(ctx.guild.id)
        if action is None:
            settings = await state.settings_col.find_one({"guild": guild_id})
            is_maintenance = settings.get("maintenance_mode", False) if settings else False
            embed = discord.Embed(
                title="🔧 Maintenance Status",
                description=f"Maintenance mode is currently: {('**ON**' if is_maintenance else '**OFF**')}",
                color=discord.Color.orange() if is_maintenance else discord.Color.green(),
            )
            if is_maintenance:
                embed.add_field(
                    name="⚠️ Current Status",
                    value="• Only staff can use bot commands\n• Channel restrictions are bypassed for staff\n• Regular users cannot use any commands",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="✅ Current Status",
                    value="• All users can use bot commands\n• Channel restrictions are enforced\n• Normal operation mode",
                    inline=False,
                )
            embed.add_field(
                name="📝 Usage",
                value="`.maintenance on` - Enable maintenance mode\n`.maintenance off` - Disable maintenance mode",
                inline=False,
            )
            await ctx.send(embed=embed)
            return
        action = action.lower()
        if action not in ["on", "off"]:
            await ctx.send("❌ Invalid action. Use `on`, `off`, or no argument to check status.")
            return
        await state.settings_col.update_one(
            {"guild": guild_id}, {"$set": {"maintenance_mode": action == "on"}}, upsert=True
        )
        if action == "on":
            embed = discord.Embed(
                title="🔧 Maintenance Mode Enabled",
                description="**Bot is now in maintenance mode!**",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="⚠️ What Changed",
                value="• Only staff members can use bot commands\n• Channel restrictions are ignored for staff\n• Regular users see maintenance messages",
                inline=False,
            )
            embed.add_field(
                name="👤 Who Can Use Commands",
                value="• Server Owner\n• Staff members (with configured staff role)\n• Users with admin permissions",
                inline=False,
            )
            embed.set_footer(text="Use `.maintenance off` to disable maintenance mode")
        else:
            embed = discord.Embed(
                title="✅ Maintenance Mode Disabled",
                description="**Bot is back to normal operation!**",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="🔄 What Changed",
                value="• All users can use bot commands again\n• Channel restrictions are enforced\n• Normal operation resumed",
                inline=False,
            )
            embed.set_footer(text="Use `.maintenance on` to enable maintenance mode")
        await ctx.send(embed=embed)

    @commands.command()
    @perms.staffperm("config")
    @perms.staff_only()
    async def testwelcome(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        config = await state.config_col.find_one({"guild": str(ctx.guild.id)}) or {}
        channel_id = config.get("welcome_channel")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        if not channel:
            return await ctx.send("❌ No welcome channel set. Use `.editconfig welcome_channel #channel`.")
        _DEFAULT_WELCOME = "👋 Welcome {mention} to **{server}**! 🎉\nYou are our **{membercount}**th member. We're happy to have you here!"
        msg_template = config.get("welcome_message") or _DEFAULT_WELCOME
        text = (
            msg_template.replace("{username}", member.name)
            .replace("{mention}", member.mention)
            .replace("{server}", ctx.guild.name)
            .replace("{membercount}", str(ctx.guild.member_count))
        )
        welcome_image_url = config.get("welcome_image")
        embed = discord.Embed(
            title=f"Welcome to {ctx.guild.name}! 🎉", description=text, color=discord.Color.from_str("#2f3136")
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if welcome_image_url:
            embed.set_image(url=welcome_image_url)
        embed.set_footer(text=f"They are our {ctx.guild.member_count}th member!")
        await channel.send(f"Welcome, {member.mention}! 🐥", embed=embed)
        await ctx.send("✅ Sent test welcome message.")

    @commands.command()
    @perms.staffperm("config")
    @perms.staff_only()
    async def testboost(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        config = await state.config_col.find_one({"guild": str(ctx.guild.id)}) or {}
        channel_id = config.get("boost_channel")
        msg_template = config.get("boost_message")
        react_emoji = config.get("boost_react_emoji")
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ No boost channel set.")
        msg_template = msg_template or "🚀 {mention} just boosted **{server}**! We're now at {boostcount} boosts! 🎉"
        text = (
            msg_template.replace("{username}", member.name)
            .replace("{mention}", member.mention)
            .replace("{server}", ctx.guild.name)
            .replace("{boostcount}", str(ctx.guild.premium_subscription_count or 0))
        )
        embed = discord.Embed(description=text, color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
        embed.set_author(name="Boost Alert!", icon_url=member.display_avatar.url)
        try:
            sent_message = await channel.send(embed=embed)
            await ctx.send("✅ Sent test boost message.")
            if react_emoji:
                try:
                    await sent_message.add_reaction(react_emoji)
                except discord.HTTPException:
                    await ctx.send("⚠️ Could not react with the configured emoji (invalid or deleted).")
        except Exception as e:
            await ctx.send(f"⚠️ Failed to send test boost message: `{e}`")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await state.settings_col.update_one({"guild": str(guild.id)}, {"$setOnInsert": {"prefix": "?"}}, upsert=True)
        if isinstance(guild, discord.Guild):
            try:
                await xp.ensure_badge_roles_for_guild(guild)
            except Exception as e:
                print(f"[Badge Roles] Error setting up badge roles for new guild {guild.id}: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Create any badge roles that are missing in the guilds the bot is in (runs once)."""
        if self._ready_done:
            return
        self._ready_done = True
        await xp.ensure_all_badge_roles(self.bot)
        print("✅ Badge roles ensured.")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Post the configured boost thank-you when Discord emits a server-boost system message."""
        if message.author.bot:
            return
        if not message.guild:
            return
        try:
            if message.type in cfg.BOOST_MESSAGE_TYPES:
                guild = message.guild
                config = await state.config_col.find_one({"guild": str(guild.id)})
                if config:
                    boost_channel_id = config.get("boost_channel")
                    boost_message = config.get("boost_message")
                    if boost_channel_id and boost_message:
                        channel = guild.get_channel(boost_channel_id)
                        if channel:
                            booster = message.author
                            msg_content = (
                                boost_message.replace("{username}", booster.name)
                                .replace("{mention}", booster.mention)
                                .replace("{server}", guild.name)
                                .replace("{boostcount}", str(guild.premium_subscription_count or 0))
                            )
                            embed = discord.Embed(
                                description=msg_content,
                                color=discord.Color.fuchsia(),
                                timestamp=datetime.now(timezone.utc),
                            )
                            embed.set_author(name="Boost Alert!", icon_url=booster.display_avatar.url)
                            embed.set_thumbnail(url=booster.display_avatar.url)
                            try:
                                sent = await channel.send(embed=embed)
                                emoji = config.get("boost_react_emoji")
                                if emoji:
                                    try:
                                        await sent.add_reaction(emoji)
                                    except (discord.HTTPException, discord.Forbidden):
                                        pass
                            except Exception as e:
                                print(f"⚠️ Error sending boost thank you in {guild.name}: {e}")
        except Exception as e:
            print(f"[boost message handler error] {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Send the configured welcome message and, for members who arrive already boosting,
        the boost thank-you (invite attribution and mute re-application live in their own cogs)."""
        guild = member.guild
        try:
            doc = await state.guild_config_col.find_one({"guild_id": str(guild.id)}) or {}
            guild_cfg = await state.config_col.find_one({"guild": str(guild.id)}) or {}
            welcome_channel_id = guild_cfg.get("welcome_channel") or doc.get("welcome_channel")
            welcome_message_template = guild_cfg.get("welcome_message") or doc.get("welcome_message")
            welcome_ch = guild.get_channel(welcome_channel_id) if welcome_channel_id else None
            if welcome_ch and welcome_message_template:
                msg_template = welcome_message_template
                welcome_msg = (
                    msg_template.replace("{username}", member.name)
                    .replace("{mention}", member.mention)
                    .replace("{server}", guild.name)
                    .replace("{membercount}", str(guild.member_count))
                )
                welcome_image_url = guild_cfg.get("welcome_image") or doc.get("welcome_image")
                embed = discord.Embed(
                    title=f"Welcome to {guild.name}! 🎉",
                    description=welcome_msg,
                    color=discord.Color.from_str("#2f3136"),
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                if welcome_image_url:
                    embed.set_image(url=welcome_image_url)
                embed.set_footer(text=f"You are our {guild.member_count}th member!")
                msg = await welcome_ch.send(f"Welcome, {member.mention}! 🐥", embed=embed)
                duck_emoji = discord.utils.get(guild.emojis, name="duckwave2")
                if duck_emoji:
                    await msg.add_reaction(duck_emoji)
                else:
                    print("Custom emoji 'duckwave2' not found in guild.")
            if member.premium_since:
                boost_key = f"{guild.id}-{member.id}"
                boost_record = await state.boost_col.find_one({"_id": boost_key})
                if not boost_record or boost_record.get("last_thanked") != member.premium_since.isoformat():
                    boost_channel_id = guild_cfg.get("boost_channel") or doc.get("boost_channel")
                    boost_msg_template = guild_cfg.get("boost_message") or doc.get("boost_message")
                    boost_ch = guild.get_channel(boost_channel_id) if boost_channel_id else None
                    if boost_ch and boost_msg_template:
                        text = (
                            boost_msg_template.replace("{username}", member.name)
                            .replace("{mention}", member.mention)
                            .replace("{server}", guild.name)
                            .replace("{boostcount}", str(guild.premium_subscription_count or 0))
                        )
                        boost_embed = discord.Embed(
                            title="🚀 Boost Alert!",
                            description=text,
                            color=discord.Color.fuchsia(),
                            timestamp=datetime.now(timezone.utc),
                        )
                        boost_embed.set_thumbnail(url=member.display_avatar.url)
                        sent_msg = await boost_ch.send(embed=boost_embed)
                        emoji = guild_cfg.get("boost_react_emoji") or doc.get("boost_react_emoji")
                        if emoji:
                            try:
                                await sent_msg.add_reaction(emoji)
                            except (discord.HTTPException, discord.Forbidden):
                                pass
                    await state.boost_col.update_one(
                        {"_id": boost_key}, {"$set": {"last_thanked": member.premium_since.isoformat()}}, upsert=True
                    )
        except Exception as e:
            print("on_member_join ERROR:", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildConfig(bot))
