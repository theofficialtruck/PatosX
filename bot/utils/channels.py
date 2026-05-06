"""Per-guild channel-restriction checks.

Most user-facing commands honour two kinds of channel restrictions:

* ``settings_col`` keys (``economy_channel``, ``welcome_channel`` …) hold a
  *single* allowed channel ID.
* ``config_col`` keys (``DROP_CHANNELS``, ``ALLOWED_DUCK_CHANNELS`` …) hold a
  *list* of allowed channel IDs (or the string ``"all"``).

Two ``check_channel`` helpers exist because the original code grew them in
parallel; both are kept here so cogs can pick the right one without
behaviour changing.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import discord

from bot.database import config_col, settings_col
from bot.utils.permissions import is_maintenance_mode, is_staff_user
from bot.utils.state import has_bot, get_bot


async def check_channel_setting(
    ctx, setting_key: str, channel_type: str
) -> bool:
    """Single-channel variant — used for log/welcome/boost-style settings.

    Returns ``False`` and replies with an embed when the user is in the
    wrong channel; staff in maintenance mode bypass the check.
    """
    guild_id = str(ctx.guild.id)

    if await is_maintenance_mode(guild_id) and await is_staff_user(ctx):
        return True

    settings = await settings_col.find_one({"guild": guild_id})
    if not settings:
        return False

    channel_id = settings.get(setting_key)
    if not channel_id:
        return False

    if ctx.channel.id != channel_id:
        embed = discord.Embed(
            title="❌ Wrong Channel",
            description=f"This command can only be used in the {channel_type} channel.",
            color=discord.Color.red(),
        )
        if has_bot():
            channel = get_bot().get_channel(channel_id)
            if channel:
                embed.add_field(
                    name="Correct Channel",
                    value=f"{channel.mention}",
                    inline=False,
                )

        if hasattr(ctx, "respond") and ctx.is_interaction():
            await ctx.respond(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return False

    return True


async def check_channel(
    ctx, config_key: str, friendly_name: Optional[str] = None
) -> bool:
    """List-channel variant — used for ``DROP_CHANNELS`` and similar keys.

    Staff with the configured staff role always pass. The stored value can
    be an int, a list of ints, the string ``"all"``, or a comma/space
    separated list of digits.
    """
    settings = await settings_col.find_one({"guild": str(ctx.guild.id)}) or {}
    staff_role_id = settings.get("staff_role")

    if staff_role_id and discord.utils.get(ctx.author.roles, id=staff_role_id):
        return True

    config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception:
            config = {}
    value = config.get(config_key)

    if not value:
        return True

    if isinstance(value, int):
        allowed_channels = [value]
    elif isinstance(value, str):
        if value.lower() == "all":
            return True
        if value.isdigit():
            allowed_channels = [int(value)]
        else:
            allowed_channels = [int(x) for x in re.findall(r"\d+", value)]
    elif isinstance(value, list):
        allowed_channels = [int(x) for x in value if str(x).isdigit()]
    else:
        return True

    if allowed_channels and ctx.channel.id not in allowed_channels:
        mention = (
            f"<#{allowed_channels[0]}>" if allowed_channels else "`a configured channel`"
        )
        fname = friendly_name or config_key.replace("_", " ").title()
        await ctx.send(f"🚫 {fname} commands can only be used in {mention}.")
        return False

    return True


async def check_maintenance_access(ctx) -> bool:
    """Block non-staff use when maintenance mode is on."""
    guild_id = str(ctx.guild.id)
    if not await is_maintenance_mode(guild_id):
        return True
    if await is_staff_user(ctx):
        return True

    embed = discord.Embed(
        title="🔧 Bot Under Maintenance",
        description=(
            "The bot is currently in maintenance mode. Only staff can use "
            "commands at this time."
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="⏰ Estimated Downtime",
        value="Maintenance mode will be disabled when staff complete their work.",
        inline=False,
    )
    embed.set_footer(text="Please try again later. Thank you for your patience!")

    if hasattr(ctx, "respond") and ctx.is_interaction():
        await ctx.respond(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)

    return False


__all__ = [
    "check_channel",
    "check_channel_setting",
    "check_maintenance_access",
]
