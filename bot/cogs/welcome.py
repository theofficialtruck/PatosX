"""Test commands for the welcome and boost messages, plus one-time channels."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import commands

from bot.database import config_col, settings_col
from bot.utils.checks import staff_only, staffperm
from bot.utils.permissions import has_staff_role
from bot.utils.stickies import onetime_channels


class WelcomeCog(commands.Cog, name="Welcome"):
    """Welcome/boost test messages and one-time channel admin."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command()
    @staffperm("config")
    @staff_only()
    async def testwelcome(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        member = member or ctx.author
        config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}

        channel_id = config.get("welcome_channel")
        msg_template = config.get("welcome_message")
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ No welcome channel set.")

        msg_template = msg_template or (
            "👋 Welcome {mention} to **{server}**! You are member #{membercount}!"
        )
        text = (
            msg_template.replace("{username}", member.name)
            .replace("{mention}", member.mention)
            .replace("{server}", ctx.guild.name)
            .replace("{membercount}", str(ctx.guild.member_count))
        )

        embed = discord.Embed(description=text, color=discord.Color.blue())
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)
        await ctx.send("✅ Sent test welcome message.")

    @commands.command()
    @staffperm("config")
    @staff_only()
    async def testboost(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        member = member or ctx.author
        config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}

        channel_id = config.get("boost_channel")
        msg_template = config.get("boost_message")
        react_emoji = config.get("boost_react_emoji")
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ No boost channel set.")

        msg_template = msg_template or (
            "🚀 {mention} just boosted **{server}**! "
            "We’re now at {boostcount} boosts! 🎉"
        )
        text = (
            msg_template.replace("{username}", member.name)
            .replace("{mention}", member.mention)
            .replace("{server}", ctx.guild.name)
            .replace(
                "{boostcount}", str(ctx.guild.premium_subscription_count or 0)
            )
        )

        embed = discord.Embed(
            description=text,
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name="Boost Alert!", icon_url=member.display_avatar.url)

        try:
            sent_message = await channel.send(embed=embed)
            await ctx.send("✅ Sent test boost message.")
            if react_emoji:
                try:
                    await sent_message.add_reaction(react_emoji)
                except discord.HTTPException:
                    await ctx.send(
                        "⚠️ Could not react with the configured emoji "
                        "(invalid or deleted)."
                    )
        except Exception as exc:
            await ctx.send(f"⚠️ Failed to send test boost message: `{exc}`")

    # ------------------------------------------------------------------
    # One-time channel admin
    # ------------------------------------------------------------------

    @commands.command()
    @staffperm("config")
    @staff_only()
    async def onetime(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target_channel = channel or ctx.channel
        guild_id = str(ctx.guild.id)
        channel_id = str(target_channel.id)

        onetime_channels.setdefault(guild_id, {})

        if channel_id not in onetime_channels[guild_id]:
            onetime_channels[guild_id][channel_id] = {}
            await settings_col.update_one(
                {"guild": guild_id},
                {"$set": {f"onetime_channels.{channel_id}": {}}},
                upsert=True,
            )

            embed = discord.Embed(
                title="✅ One-Time Message Channel Set Up",
                description=(
                    f"**{target_channel.mention}** is now a one-time message "
                    "channel.\n\n"
                    "Non-staff members can send **only one message** in this "
                    "channel. After their first message, they will lose "
                    "permission to send more messages.\n\n"
                    "Staff members are exempt and can continue messaging "
                    "normally.\n\n"
                    "Use `.restore <user>` to give a user back their messaging "
                    "permissions."
                ),
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
            try:
                await target_channel.send(
                    "🔔 **This is now a one-time message channel!**\n"
                    "Non-staff members can send only one message here. "
                    "Staff can restore permissions with `.restore <user>`."
                )
            except Exception:
                pass
        else:
            await ctx.send(
                f"⚠️ {target_channel.mention} is already a one-time message channel."
            )

    @commands.command()
    @staffperm("config")
    @staff_only()
    async def restore(
        self,
        ctx: commands.Context,
        member: discord.Member,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target_channel = channel or ctx.channel
        guild_id = str(ctx.guild.id)
        channel_id = str(target_channel.id)
        user_id = str(member.id)

        if (
            guild_id not in onetime_channels
            or channel_id not in onetime_channels[guild_id]
        ):
            return await ctx.send(
                f"⚠️ {target_channel.mention} is not a one-time message channel."
            )

        if user_id in onetime_channels[guild_id][channel_id]:
            del onetime_channels[guild_id][channel_id][user_id]
            await settings_col.update_one(
                {"guild": guild_id},
                {"$unset": {f"onetime_channels.{channel_id}.{user_id}": ""}},
            )

        try:
            await target_channel.set_permissions(
                member,
                send_messages=None,
                reason="One-time message permission restored",
            )
            embed = discord.Embed(
                title="✅ Permissions Restored",
                description=(
                    f"{member.mention} can now send messages in "
                    f"{target_channel.mention} again."
                ),
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
        except Exception as exc:
            await ctx.send(f"❌ Failed to restore permissions: `{exc}`")

    @commands.command()
    @staffperm("config")
    @staff_only()
    async def disableonetime(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target_channel = channel or ctx.channel
        guild_id = str(ctx.guild.id)
        channel_id = str(target_channel.id)

        if (
            guild_id not in onetime_channels
            or channel_id not in onetime_channels[guild_id]
        ):
            return await ctx.send(
                f"⚠️ {target_channel.mention} is not a one-time message channel."
            )

        del onetime_channels[guild_id][channel_id]
        await settings_col.update_one(
            {"guild": guild_id},
            {"$unset": {f"onetime_channels.{channel_id}": ""}},
        )

        try:
            for target, overwrite in target_channel.overwrites.items():
                if isinstance(target, discord.Member) and not await has_staff_role(
                    target, target_channel.guild
                ):
                    if overwrite.send_messages is False:
                        await target_channel.set_permissions(
                            target, send_messages=None
                        )

            embed = discord.Embed(
                title="✅ One-Time Channel Disabled",
                description=(
                    f"{target_channel.mention} is no longer a one-time message channel."
                ),
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
        except Exception as exc:
            await ctx.send(f"❌ Failed to disable one-time restrictions: `{exc}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
