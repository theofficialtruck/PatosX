"""Loops that auto-unmute and re-apply Muted-role permission overwrites."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import tasks

from bot.database import mod_col, mutes_col
from bot.utils.logging import log_action
from bot.utils.state import has_bot, get_bot


@tasks.loop(seconds=15)
async def check_expired_mutes() -> None:
    """Walk both mute collections and auto-unmute when ``mute_end`` has passed."""
    if not has_bot():
        return
    bot = get_bot()

    count = await mutes_col.count_documents({})
    if count == 0:
        mod_count = await mod_col.count_documents(
            {"muted_until": {"$exists": True}}
        )
        if mod_count == 0:
            return

    now = datetime.now(timezone.utc)

    async for doc in mutes_col.find({"mute_end": {"$exists": True}}):
        try:
            mute_end = doc["mute_end"]
            if isinstance(mute_end, str):
                try:
                    mute_end = datetime.fromisoformat(mute_end)
                except ValueError:
                    mute_end = datetime.strptime(
                        mute_end, "%Y-%m-%d %H:%M:%S"
                    )
            if mute_end.tzinfo is None:
                mute_end = mute_end.replace(tzinfo=timezone.utc)
            if mute_end > now:
                continue

            guild = bot.get_guild(int(doc["guild_id"]))
            if not guild:
                continue
            member = guild.get_member(int(doc["user_id"]))
            if not member:
                await mutes_col.delete_one({"_id": doc["_id"]})
                continue

            mute_role = discord.utils.get(guild.roles, name="Muted")
            if mute_role and mute_role in member.roles:
                try:
                    await member.remove_roles(mute_role, reason="Mute expired")
                    await log_action(
                        ctx=None,
                        message=f"Auto-unmuted {member}",
                        user_id=member.id,
                        action_type="unmute",
                    )
                except Exception as exc:
                    print(f"[Auto-unmute role removal error] {exc}")

            await mutes_col.delete_one({"_id": doc["_id"]})
        except Exception as exc:
            print(f"[Auto-unmute error - mutes_col] {exc}")

    async for doc in mod_col.find({"muted_until": {"$exists": True}}):
        try:
            mute_until = doc["muted_until"]
            if isinstance(mute_until, str):
                mute_until = datetime.fromisoformat(mute_until)
            if mute_until.tzinfo is None:
                mute_until = mute_until.replace(tzinfo=timezone.utc)
            if mute_until > now:
                continue

            guild = bot.get_guild(int(doc["guild"]))
            if not guild:
                continue
            member = guild.get_member(int(doc["user"]))
            if not member:
                await mod_col.update_one(
                    {"guild": doc["guild"], "user": doc["user"]},
                    {"$unset": {"muted_until": ""}},
                )
                continue

            mute_role = discord.utils.get(guild.roles, name="Muted")
            if mute_role and mute_role in member.roles:
                try:
                    await member.remove_roles(mute_role, reason="Mute expired")
                    await log_action(
                        ctx=None,
                        message=f"Auto-unmuted {member}",
                        user_id=member.id,
                        action_type="unmute",
                    )
                except Exception as exc:
                    print(f"[Auto-unmute role removal error] {exc}")

            await mod_col.update_one(
                {"guild": doc["guild"], "user": doc["user"]},
                {"$unset": {"muted_until": ""}},
            )
        except Exception as exc:
            print(f"[Auto-unmute error - mod_col] {exc}")


@tasks.loop(minutes=1)
async def check_muted_role_permissions() -> None:
    """Re-apply restrictive permission overwrites to the Muted role."""
    if not has_bot():
        return
    bot = get_bot()

    for guild in bot.guilds:
        mute_role = discord.utils.get(guild.roles, name="Muted")
        if not mute_role:
            continue

        for channel in guild.channels:
            perms = channel.overwrites_for(mute_role)
            needs_update = False

            if isinstance(channel, discord.TextChannel):
                if (
                    perms.send_messages is not False
                    or perms.add_reactions is not False
                    or perms.create_public_threads is not False
                ):
                    needs_update = True
            elif isinstance(channel, discord.VoiceChannel):
                if (
                    perms.speak is not False
                    or perms.stream is not False
                    or perms.connect is not False
                ):
                    needs_update = True
            elif isinstance(channel, discord.CategoryChannel):
                if (
                    perms.send_messages is not False
                    or perms.speak is not False
                ):
                    needs_update = True

            if not needs_update:
                continue

            try:
                if isinstance(channel, discord.TextChannel):
                    await channel.set_permissions(
                        mute_role,
                        send_messages=False,
                        add_reactions=False,
                        create_public_threads=False,
                        create_private_threads=False,
                        send_messages_in_threads=False,
                    )
                elif isinstance(channel, discord.VoiceChannel):
                    await channel.set_permissions(
                        mute_role,
                        speak=False,
                        stream=False,
                        connect=False,
                    )
                elif isinstance(channel, discord.StageChannel):
                    await channel.set_permissions(
                        mute_role, request_to_speak=False
                    )
                elif isinstance(channel, discord.CategoryChannel):
                    await channel.set_permissions(
                        mute_role,
                        send_messages=False,
                        add_reactions=False,
                        speak=False,
                    )
                print(
                    f"Updated Muted role permissions for #{channel.name} in "
                    f"{guild.name}"
                )
            except Exception as exc:
                print(
                    f"Failed to update permissions for #{channel.name} in "
                    f"{guild.name}: {exc}"
                )


@check_expired_mutes.before_loop
@check_muted_role_permissions.before_loop
async def _wait_until_ready() -> None:
    if not has_bot():
        return
    await get_bot().wait_until_ready()


__all__ = ["check_expired_mutes", "check_muted_role_permissions"]
