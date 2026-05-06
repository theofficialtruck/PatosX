"""Global error handlers for prefix and slash commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.config.constants import DISCORD_SERVICE_UNAVAILABLE_MESSAGE
from bot.utils.errors import is_discord_service_unavailable_error
from bot.utils.help import find_similar_commands, get_command_syntax


class ErrorEventsCog(commands.Cog, name="ErrorEvents"):
    """Translate framework errors into user-facing messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Register the slash error handler manually because it lives on the
        # tree, not the bot.
        bot.tree.error(self.on_app_command_error)

    @commands.Cog.listener()
    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CheckFailure):
            return await ctx.send("❌ You don't have permission to use this command.")
        if isinstance(error, commands.MissingRequiredArgument):
            command_name = ctx.command.name if ctx.command else "unknown"
            syntax = get_command_syntax(command_name)
            return await ctx.send(
                f"⚠️ **Missing required argument**\n\n**Usage:** {syntax}"
            )
        if isinstance(error, commands.BadArgument):
            command_name = ctx.command.name if ctx.command else "unknown"
            syntax = get_command_syntax(command_name)
            return await ctx.send(
                f"⚠️ **Invalid argument provided**\n\n**Usage:** {syntax}"
            )
        if isinstance(error, commands.CommandNotFound):
            invoked_command = ctx.invoked_with
            similar = find_similar_commands(invoked_command)
            if similar:
                similar_text = "\n".join(f"• `{cmd}`" for cmd in similar)
                return await ctx.send(
                    f"⚠️ **Command not found:** `{invoked_command}`\n\n"
                    f"**Did you mean:**\n{similar_text}\n\n"
                    "Use `.help` to see all available commands."
                )
            return await ctx.send(
                f"⚠️ **Command not found:** `{invoked_command}`\n\n"
                "Use `.help` to see all available commands."
            )
        if isinstance(error, commands.TooManyArguments):
            command_name = ctx.command.name if ctx.command else "unknown"
            syntax = get_command_syntax(command_name)
            return await ctx.send(
                f"⚠️ **Too many arguments provided**\n\n**Usage:** {syntax}"
            )

        print(f"An unexpected error occurred: {error}")

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CommandNotFound):
            similar = find_similar_commands(
                interaction.command.name if interaction.command else ""
            )
            if similar:
                similar_text = "\n".join(f"• `{cmd}`" for cmd in similar)
                embed = discord.Embed(
                    title="⚠️ Command Not Found",
                    description=(
                        f"Command `/{interaction.command.name if interaction.command else ''}`"
                        f" not found.\n\n**Did you mean:**\n{similar_text}\n\n"
                        "Use `/help` to see all available commands."
                    ),
                    color=discord.Color.orange(),
                )
            else:
                embed = discord.Embed(
                    title="⚠️ Command Not Found",
                    description=(
                        f"Command `/{interaction.command.name if interaction.command else ''}`"
                        " not found.\n\nUse `/help` to see all available commands."
                    ),
                    color=discord.Color.orange(),
                )
        elif isinstance(error, app_commands.MissingRole):  # pragma: no cover
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You don't have permission to use this command.",
                color=discord.Color.red(),
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
        else:
            embed = discord.Embed(
                title="❌ Command Error",
                description="An unexpected error occurred. Please try again later.",
                color=discord.Color.red(),
            )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            print(f"[APP COMMAND ERROR HANDLER FAILED] {exc}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ErrorEventsCog(bot))
