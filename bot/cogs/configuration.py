"""Server configuration commands: ``configure``, ``editconfig``, etc."""

from __future__ import annotations

import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import config_col
from bot.utils.checks import staff_only, staffperm
from bot.utils.errors import send_hybrid_error
from bot.utils.logging import log_action

PROMPTS = {
    "welcome_channel": "Enter the **welcome channel ID** (required for welcome system):",
    "welcome_message": "Enter the **welcome message** (required):",
    "boost_channel": "Enter the **boost channel ID** (required for boost system):",
    "boost_message": "Enter the **boost message** (required):",
    "ALLOWED_DUCK_CHANNELS": "Enter allowed channel IDs for `.duck` (comma/space separated, required):",
    "ROLE_ID": "Enter role IDs to award for passing `.duckquiz` (required):",
    "QUIZ_CHANNEL": "Enter channel IDs where `.duckquiz` can run (required):",
    "allowed_channel_id": "Enter channel IDs where DuckGPT is allowed (required):",
    "economy_channel": "Enter the channel ID where the economy game is allowed (required):",
    "log_channel": "Enter the log channel ID for moderation logs (optional, type `skip` to disable):",
    "DROP_CHANNELS": "Enter channel IDs where `.drop` can be used by members (comma/space separated, required):",
    "QUACK_CHANNELS": "Enter channel IDs where the quack counter should activate (comma/space separated, optional, type `skip` to disable):",
}

VALID_SETTINGS = {
    "welcome_channel": {"desc": "Welcome channel", "key": "welcome_channel"},
    "welcome_message": {"desc": "Welcome message", "key": "welcome_message"},
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
}


def _norm(value: str) -> str:
    return re.sub(r"\s+", "_", value.strip().lower())


class ConfigurationCog(commands.Cog, name="Configuration"):
    """Server configuration commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(
        name="configure",
        aliases=["config"],
        description="Make server configuration.",
    )
    @staffperm("config")
    @staff_only()
    async def configure(self, ctx: commands.Context) -> None:
        config_data: dict = {"guild": str(ctx.guild.id)}

        def check(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        await ctx.send("🛠 Starting configuration. Type `cancel` to abort at any time.")

        for key, question in PROMPTS.items():
            await ctx.send(question)
            try:
                msg = await self.bot.wait_for("message", timeout=90, check=check)
            except asyncio.TimeoutError:
                return await ctx.send("⌛ Timed out. Configuration cancelled.")

            content = msg.content.strip()
            if content.lower() == "cancel":
                return await ctx.send("❌ Configuration cancelled.")

            if key == "log_channel" and content.lower() == "skip":
                continue
            if key == "QUACK_CHANNELS" and content.lower() == "skip":
                config_data[key] = []
                continue

            if not content:
                return await ctx.send(
                    f"❌ `{key}` cannot be blank. Please run `.configure` again."
                )

            try:
                if key in {
                    "log_channel",
                    "economy_channel",
                    "welcome_channel",
                    "boost_channel",
                }:
                    if not content.isdigit():
                        return await ctx.send(
                            f"❌ Please provide a valid channel ID for `{key}`."
                        )
                    config_data[key] = int(content)
                elif key in {"welcome_message", "boost_message"}:
                    config_data[key] = content
                else:
                    if content.lower() == "all":
                        config_data[key] = "all"
                    else:
                        ids = [
                            int(x) for x in re.split(r"[,\s]+", content) if x.isdigit()
                        ]
                        if not ids:
                            return await ctx.send(
                                f"❌ No valid IDs entered for `{key}`."
                            )
                        config_data[key] = ids
            except ValueError:
                return await ctx.send(f"❌ Couldn't parse IDs for `{key}`.")

            await msg.delete()

        await config_col.update_one(
            {"guild": config_data["guild"]},
            {"$set": config_data},
            upsert=True,
        )
        await ctx.send("✅ Configuration saved successfully!", delete_after=7)
        await log_action(
            ctx,
            f"Configuration updated for {ctx.guild.name}",
            action_type="configure",
        )

    @configure.error
    async def configure_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_hybrid_error(
                ctx,
                content="❌ You don't have permission to use this command.",
                delete_after=7,
            )
        elif isinstance(error, commands.CheckFailure):
            await send_hybrid_error(
                ctx, content="❌ Only staff members can use this command.", delete_after=7
            )
        else:
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ An unexpected error occurred, please contact thetruck: "
                    f"`{type(error).__name__} - {error}`"
                ),
                delete_after=10,
            )

    @commands.command(
        name="editconfig",
        aliases=["editconfiguration"],
        description="Edit one configuration setting.",
    )
    @app_commands.describe(args="Configuration arguments")
    @staffperm("config")
    @staff_only()
    async def editconfig(
        self, ctx: commands.Context, args: str | None = None
    ) -> None:
        if not args:
            return await ctx.send(
                "❌ Please specify a setting and value, e.g. "
                "`editconfig welcome_channel #general`"
            )

        parts = args.split()
        idx = len(parts)
        for i in range(1, len(parts)):
            piece = parts[i]
            if (
                piece.isdigit()
                or piece.startswith("<#")
                or piece.startswith("<@&")
                or piece.lower() in {"none", "null", "remove", "delete", "all"}
            ):
                idx = i
                break
        raw_setting = " ".join(parts[:idx]).strip()
        setting_norm = _norm(raw_setting)
        value = " ".join(parts[idx:]).strip() if idx < len(parts) else None

        if setting_norm not in VALID_SETTINGS:
            pretty_list = "\n".join(
                f"• `{info['key']}` - {info['desc']}"
                for info in VALID_SETTINGS.values()
            )
            embed = discord.Embed(
                title="⚙️ Invalid Setting",
                description=(
                    f"❌ **`{raw_setting}`** is not a valid configuration key.\n\n"
                    f"**Available settings:**\n{pretty_list}"
                ),
                color=discord.Color.red(),
            )
            embed.set_footer(
                text="Tip: You can type settings with spaces (e.g. 'welcome message')"
            )
            return await ctx.send(embed=embed)

        config = await config_col.find_one(
            {"guild": str(ctx.guild.id)}
        ) or {"guild": str(ctx.guild.id)}

        canonical_key = VALID_SETTINGS[setting_norm]["key"]
        desc = VALID_SETTINGS[setting_norm]["desc"]

        if value and value.lower() in {"none", "null", "remove", "delete"}:
            await config_col.update_one(
                {"guild": config["guild"]}, {"$unset": {canonical_key: ""}}
            )
            await ctx.send(
                f"🗑 **{desc}** has been removed from the configuration."
            )
            await log_action(
                ctx,
                f"{desc} removed from {ctx.guild.name}",
                action_type="editconfig",
            )
            return

        try:
            if canonical_key in {"welcome_message", "boost_message"} and not value:
                placeholder_info = (
                    "🧩 You can use these placeholders in your message:\n"
                    "`{username}` - Booster's username\n"
                    "`{mention}` - Mention the booster\n"
                    "`{server}` - Server name\n"
                    "`{boostcount}` - Current server boost count\n\n"
                )
                await ctx.send(
                    placeholder_info
                    + f"📝 Please enter the new {desc.lower()} below.\n"
                    "You can type `cancel` to abort or `none` to remove it."
                )

                def check(message: discord.Message) -> bool:
                    return (
                        message.author == ctx.author
                        and message.channel == ctx.channel
                    )

                try:
                    msg = await self.bot.wait_for("message", timeout=180, check=check)
                except asyncio.TimeoutError:
                    return await ctx.send("⌛ Timed out. Configuration cancelled.")

                content = msg.content.strip()
                if content.lower() == "cancel":
                    return await ctx.send("❌ Edit cancelled.")
                if content.lower() in {"none", "null", "remove", "delete"}:
                    await config_col.update_one(
                        {"guild": config["guild"]},
                        {"$unset": {canonical_key: ""}},
                    )
                    await ctx.send(
                        f"🗑 **{desc}** has been removed from the configuration."
                    )
                    await log_action(
                        ctx,
                        f"{desc} removed from {ctx.guild.name}",
                        action_type="editconfig",
                    )
                    return

                config[canonical_key] = content
                await msg.delete()

                if canonical_key == "boost_message":
                    await ctx.send(
                        "✨ Would you like me to react to each boost message with a "
                        "custom emoji?\n"
                        "React to **this message** with the emoji you want, or type "
                        "`none` to skip."
                    )

                    def emoji_check(reaction, user):
                        return (
                            user == ctx.author
                            and reaction.message.channel == ctx.channel
                        )

                    try:
                        await ctx.send("⏳ Waiting for your emoji reaction or text reply...")
                        reaction_task = asyncio.create_task(
                            self.bot.wait_for(
                                "reaction_add", timeout=30, check=emoji_check
                            )
                        )
                        message_task = asyncio.create_task(
                            self.bot.wait_for("message", timeout=30, check=check)
                        )
                        done, pending = await asyncio.wait(
                            [reaction_task, message_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in pending:
                            task.cancel()
                        result = list(done)[0].result()

                        if isinstance(result, tuple):
                            reaction, _ = result
                            emoji = str(reaction.emoji)
                            config["boost_react_emoji"] = emoji
                            await ctx.send(f"✅ Set boost reaction emoji to {emoji}")
                        elif isinstance(result, discord.Message):
                            if result.content.lower().strip() != "none":
                                await ctx.send(
                                    "⚠️ Invalid input, skipping emoji reaction setup."
                                )
                            else:
                                await ctx.send(
                                    "✅ No emoji reaction will be added to boost messages."
                                )
                                config["boost_react_emoji"] = None

                    except asyncio.TimeoutError:
                        await ctx.send("⌛ No emoji selected, skipping reaction setup.")
                    except Exception as exc:
                        await ctx.send(f"⚠️ Error while setting emoji: `{exc}`")

            elif canonical_key in {
                "log_channel",
                "economy_channel",
                "welcome_channel",
                "boost_channel",
            }:
                match = re.search(r"\d+", value or "")
                if not match:
                    return await ctx.send(
                        f"❌ Please mention a valid channel or provide its ID for `{desc}`."
                    )
                config[canonical_key] = int(match.group())

            elif canonical_key in {"welcome_message", "boost_message"}:
                config[canonical_key] = value

            else:
                if value and value.lower() == "all":
                    config[canonical_key] = "all"
                else:
                    ids = [int(x) for x in re.findall(r"\d+", value or "")]
                    if not ids:
                        return await ctx.send(
                            f"❌ No valid IDs found for `{desc}`."
                        )
                    config[canonical_key] = ids

        except Exception as exc:
            return await ctx.send(f"⚠️ Error updating config: `{exc}`")

        await config_col.update_one(
            {"guild": config["guild"]}, {"$set": config}, upsert=True
        )
        await ctx.send(f"✅ **{desc}** updated successfully!")
        await log_action(
            ctx,
            f"{desc} updated in {ctx.guild.name}",
            action_type="editconfig",
        )

    @editconfig.error
    async def editconfig_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_hybrid_error(
                ctx, content="❌ You don't have permission to use this command."
            )
        elif isinstance(error, commands.CheckFailure):
            await send_hybrid_error(
                ctx, content="❌ Only staff members can use this command."
            )
        else:
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ An unexpected error occurred, please contact thetruck: "
                    f"`{type(error).__name__} - {error}`"
                ),
            )

    @commands.command(
        name="viewconfig",
        description="View the current server configuration.",
    )
    @staffperm("config")
    @staff_only()
    async def viewconfig(self, ctx: commands.Context) -> None:
        config = await config_col.find_one({"guild": str(ctx.guild.id)})
        if not config:
            return await ctx.send(
                "⚠️ No configuration found for this server.", ephemeral=True
            )

        def format_ids(key: str) -> str:
            value = config.get(key)
            if value == "all":
                return "All channels"
            if not value:
                return "All channels" if "channel" in key.lower() else "Not set"
            if isinstance(value, list):
                return ", ".join(
                    f"<#{i}>" if "channel" in key.lower() else f"<@&{i}>"
                    for i in value
                )
            if isinstance(value, int):
                return (
                    f"<#{value}>"
                    if "channel" in key.lower()
                    else f"<@&{value}>"
                )
            return str(value)

        embed = discord.Embed(
            title="🔧 Server Configuration", color=discord.Color.blurple()
        )
        embed.add_field(name="👋 Welcome Channel", value=format_ids("welcome_channel"), inline=False)
        embed.add_field(
            name="👋 Welcome Message",
            value=config.get("welcome_message", "Not set"),
            inline=False,
        )
        embed.add_field(name="🚀 Boost Channel", value=format_ids("boost_channel"), inline=False)
        embed.add_field(
            name="🚀 Boost Message",
            value=config.get("boost_message", "Not set"),
            inline=False,
        )
        embed.add_field(name="Duck Command Channels", value=format_ids("ALLOWED_DUCK_CHANNELS"), inline=False)
        embed.add_field(name="Quiz Role", value=format_ids("ROLE_ID"), inline=False)
        embed.add_field(name="Quiz Channel", value=format_ids("QUIZ_CHANNEL"), inline=False)
        embed.add_field(name="DuckGPT Allowed Channel", value=format_ids("allowed_channel_id"), inline=False)
        embed.add_field(name="Drop Channels", value=format_ids("DROP_CHANNELS"), inline=False)
        embed.add_field(name="Quack Counter Channels", value=format_ids("QUACK_CHANNELS"), inline=False)
        embed.add_field(name="Economy Channel", value=format_ids("economy_channel"), inline=False)
        embed.add_field(name="Log Channel", value=format_ids("log_channel"), inline=False)

        await ctx.send(embed=embed, ephemeral=True)

    @viewconfig.error
    async def viewconfig_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_hybrid_error(
                ctx, content="❌ You don't have permission to use this command."
            )
        elif isinstance(error, commands.CheckFailure):
            await send_hybrid_error(
                ctx, content="❌ Only staff members can use this command."
            )
        else:
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ An unexpected error occurred, please contact thetruck: "
                    f"`{type(error).__name__} - {error}`"
                ),
            )

    @commands.command()
    @staffperm("config")
    @staff_only()
    async def resetconfig(self, ctx: commands.Context) -> None:
        await config_col.delete_one({"guild": str(ctx.guild.id)})
        await ctx.send("🗑 Configuration has been completely reset for this server.")

    @commands.hybrid_command(
        name="setprefix",
        description="Change the bot prefix. Staff-only.",
    )
    @staffperm("config")
    @staff_only()
    async def setprefix(self, ctx: commands.Context, new: str) -> None:
        from bot.database import settings_col

        await settings_col.update_one(
            {"guild": str(ctx.guild.id)},
            {"$set": {"prefix": new}},
            upsert=True,
        )
        await ctx.send(f"✅ Prefix updated to `{new}`.")
        await log_action(
            ctx, f"Prefix changed to {new}", action_type="setprefix"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ConfigurationCog(bot))
