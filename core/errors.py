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

"""Error handling: error-chain unwrapping, Discord-outage detection, the command-syntax and
"did you mean" helpers, the global prefix/slash error handler bodies (main.py delegates to
them), and send_hybrid_error/is_prefix for per-command error replies.
"""

import traceback
from datetime import datetime, timezone
from typing import Union

import discord
from discord import (
    app_commands,
)
from discord.ext import commands

from core import state

DISCORD_SERVICE_UNAVAILABLE_MESSAGE = "Discord is having trouble right now. Please try again in a moment."


def unwrap_command_error(error: Exception) -> Exception:
    """Walk the error chain produced by discord.py's command invocation wrapper
    and return the innermost original exception. Prevents double wrapping from
    hiding the real cause in error handlers."""
    invoke_error_types = (commands.CommandInvokeError,)
    app_invoke_error = getattr(app_commands, "CommandInvokeError", None)
    if app_invoke_error is not None:
        invoke_error_types = invoke_error_types + (app_invoke_error,)
    current = error
    seen = set()
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
    """Return True when the root cause of error is a Discord 503 outage.
    Checked before showing generic error messages so transient outages get
    a friendlier, more specific response."""
    root = unwrap_command_error(error)
    if isinstance(root, discord.DiscordServerError):
        return True
    status = getattr(root, "status", None)
    if status == 503:
        return True
    text = str(root).lower()
    return "503 service unavailable" in text or "upstream connect error" in text


def get_command_syntax(bot, command_name: str) -> str:
    """Build a human readable usage string for a command, including aliases and annotated parameters."""
    command = bot.get_command(command_name)
    if not command:
        return f"Command `{command_name}` not found."
    syntax_parts = [f"**{command.name}**"]
    if command.aliases:
        syntax_parts[0] += f" (aliases: {', '.join(f'`{alias}`' for alias in command.aliases)})"
    params = []
    for param_name, param in command.clean_params.items():
        if param_name in ("ctx", "interaction"):
            continue
        param_str = param_name
        if param.default is not param.empty:
            param_str = f"[{param_name}]"
        else:
            param_str = f"<{param_name}>"
        if param.annotation and param.annotation != param.empty:
            if hasattr(param.annotation, "__name__"):
                param_str += f" ({param.annotation.__name__})"
            elif hasattr(param.annotation, "__origin__") and param.annotation.__origin__ is Union:
                types = [t.__name__ for t in param.annotation.__args__ if t is not type(None)]
                param_str += f" ({'|'.join(types)})"
        params.append(param_str)
    if params:
        syntax_parts.append(" ".join(params))
    description = command.description or command.help
    if description:
        syntax_parts.append(f"\n*{description}*")
    return " ".join(syntax_parts)


def find_similar_commands(bot, command_name: str, limit: int = 3) -> list:
    """Return up to limit command names that contain command_name as a substring or share a 3 character prefix.
    Used to suggest corrections when a user types an unknown command."""
    command_name = command_name.lower()
    similar_commands = []
    for cmd in bot.walk_commands():
        if cmd.name.lower() == command_name:
            continue
        cmd_names = [cmd.name.lower()] + [alias.lower() for alias in cmd.aliases]
        found_similar = False
        for name in cmd_names:
            if command_name in name or name in command_name:
                similar_commands.append(cmd.name)
                found_similar = True
                break
        if not found_similar and len(command_name) >= 3:
            for name in cmd_names:
                if command_name[:3] in name:
                    similar_commands.append(cmd.name)
                    break
    return similar_commands[:limit]


async def handle_command_error(bot, ctx, error):
    """Central handler for prefix command errors, called from main.py's on_command_error.
    Provides user friendly messages for common error types and logs unexpected errors to the
    console and the state.recent_errors buffer. Takes the live bot as a parameter (for command
    lookups) instead of closing over a module global."""
    if isinstance(error, commands.CheckFailure):
        return await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        command_name = ctx.command.name if ctx.command else "unknown"
        syntax = get_command_syntax(bot, command_name)
        return await ctx.send(f"⚠️ **Missing required argument**\n\n**Usage:** {syntax}")
    elif isinstance(error, commands.BadArgument):
        command_name = ctx.command.name if ctx.command else "unknown"
        syntax = get_command_syntax(bot, command_name)
        return await ctx.send(f"⚠️ **Invalid argument provided**\n\n**Usage:** {syntax}")
    elif isinstance(error, commands.CommandNotFound):
        invoked_command = ctx.invoked_with
        similar = find_similar_commands(bot, invoked_command)
        if similar:
            similar_text = "\n".join(f"• `{cmd}`" for cmd in similar)
            return await ctx.send(
                f"⚠️ **Command not found:** `{invoked_command}`\n\n**Did you mean:**\n{similar_text}\n\nUse `.help` to see all available commands."
            )
        else:
            return await ctx.send(
                f"⚠️ **Command not found:** `{invoked_command}`\n\nUse `.help` to see all available commands."
            )
    elif isinstance(error, commands.TooManyArguments):
        command_name = ctx.command.name if ctx.command else "unknown"
        syntax = get_command_syntax(bot, command_name)
        return await ctx.send(f"⚠️ **Too many arguments provided**\n\n**Usage:** {syntax}")
    elif isinstance(error, commands.CommandOnCooldown):
        return await ctx.send(f"❌ You are on cooldown. Try again in {error.retry_after:.2f}s")
    else:
        root_error = unwrap_command_error(error)
        if isinstance(root_error, commands.CommandOnCooldown):
            return await ctx.send(f"❌ You are on cooldown. Try again in {root_error.retry_after:.2f}s")
        error_msg = f"An unexpected error occurred: {root_error}"
        print(error_msg)
        traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
        state.recent_errors.append(
            {
                "command": ctx.command.name if ctx.command else "unknown",
                "error": str(root_error),
                "traceback": traceback.format_exc(),
                "time": datetime.now(timezone.utc),
            }
        )
        if len(state.recent_errors) > 5:
            state.recent_errors.pop(0)
        try:
            await ctx.send("❌ **An unexpected error occurred**")
        except (discord.HTTPException, discord.Forbidden):
            pass


async def handle_app_command_error(bot, interaction: discord.Interaction, error):
    """Central handler for slash command errors, called from main.py's on_app_command_error.
    Mirrors handle_command_error but responds via interaction (ephemeral embed) rather than a
    plain channel message."""
    if isinstance(error, app_commands.CommandNotFound):
        similar = find_similar_commands(bot, interaction.command.name if interaction.command else "")
        if similar:
            similar_text = "\n".join(f"• `{cmd}`" for cmd in similar)
            embed = discord.Embed(
                title="⚠️ Command Not Found",
                description=f"Command `/{interaction.command.name}` not found.\n\n**Did you mean:**\n{similar_text}\n\nUse `/help` to see all available commands.",
                color=discord.Color.orange(),
            )
        else:
            embed = discord.Embed(
                title="⚠️ Command Not Found",
                description=f"Command `/{interaction.command.name}` not found.\n\nUse `/help` to see all available commands.",
                color=discord.Color.orange(),
            )
    elif isinstance(error, app_commands.MissingRequiredArgument):
        command_name = interaction.command.name if interaction.command else "unknown"
        syntax = get_command_syntax(bot, command_name)
        embed = discord.Embed(
            title="⚠️ Missing Required Argument", description=f"**Usage:** {syntax}", color=discord.Color.orange()
        )
    elif isinstance(error, app_commands.BadArgument):
        command_name = interaction.command.name if interaction.command else "unknown"
        syntax = get_command_syntax(bot, command_name)
        embed = discord.Embed(
            title="⚠️ Invalid Argument", description=f"**Usage:** {syntax}", color=discord.Color.orange()
        )
    elif isinstance(error, app_commands.CheckFailure):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You don't have permission to use this command.",
            color=discord.Color.red(),
        )
    elif is_discord_service_unavailable_error(error):
        embed = discord.Embed(
            title="⚠️ Temporary Discord Issue",
            description=DISCORD_SERVICE_UNAVAILABLE_MESSAGE,
            color=discord.Color.orange(),
        )
    elif isinstance(error, app_commands.CommandOnCooldown):
        embed = discord.Embed(
            title="❌ On Cooldown",
            description=f"You are on cooldown. Try again in {error.retry_after:.2f}s",
            color=discord.Color.red(),
        )
    else:
        root_error = unwrap_command_error(error)
        if isinstance(root_error, app_commands.CommandOnCooldown):
            embed = discord.Embed(
                title="❌ On Cooldown",
                description=f"You are on cooldown. Try again in {root_error.retry_after:.2f}s",
                color=discord.Color.red(),
            )
        else:
            error_msg = f"An unexpected error occurred in app command: {root_error}"
            print(error_msg)
            traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
            state.recent_errors.append(
                {
                    "command": interaction.command.name if interaction.command else "unknown",
                    "error": str(root_error),
                    "traceback": traceback.format_exc(),
                    "time": datetime.now(timezone.utc),
                }
            )
            if len(state.recent_errors) > 5:
                state.recent_errors.pop(0)
            embed = discord.Embed(
                title="❌ Command Error", description="An unexpected error occurred.", color=discord.Color.red()
            )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"[APP COMMAND ERROR HANDLER FAILED] {e}")


def is_prefix(ctx):
    """Return True when the context originated from a prefix command rather than a slash interaction."""
    return not hasattr(ctx, "interaction") or ctx.interaction is None


async def send_hybrid_error(ctx, *, content=None, embed=None, delete_after=None):
    """Send an error response that works for both prefix commands and slash interactions.
    Also marks the context so XP is not awarded for this invocation."""
    ctx._skip_xp_award = True
    if is_prefix(ctx):
        if embed is None and delete_after is None:
            return await ctx.send(content)
        kwargs = {}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if delete_after is not None:
            kwargs["delete_after"] = delete_after
        return await ctx.send(**kwargs)
    if ctx.interaction.response.is_done():
        return await ctx.interaction.followup.send(content=content, embed=embed, ephemeral=True)
    return await ctx.interaction.response.send_message(content=content, embed=embed, ephemeral=True)
