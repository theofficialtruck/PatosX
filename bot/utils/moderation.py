"""Helpers used by the moderation cog and modview UI.

Pulled out so the long modview embed stays readable inside the cog.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord
from discord import NotFound

from bot.database import mod_col, mutes_col


async def schedule_unmute(guild: discord.Guild, member: discord.Member, remaining: float) -> None:
    """Sleep ``remaining`` seconds, then remove the Muted role and clean up."""
    try:
        await asyncio.sleep(remaining)

        if not guild:
            print("[schedule_unmute] Guild not found, skipping.")
            return

        member = guild.get_member(member.id)
        if not member:
            print(f"[schedule_unmute] Member not found, likely left the server.")
            await mutes_col.delete_one({"guild_id": guild.id, "user_id": member.id})
            return

        mute_role = discord.utils.get(guild.roles, name="Muted")
        if mute_role and mute_role in member.roles:
            try:
                await member.remove_roles(mute_role, reason="Mute expired")
                print(f"[schedule_unmute] Auto-unmuted {member} in {guild.name}")
            except NotFound:
                print(f"[schedule_unmute] Member not found during unmute.")
            except Exception as inner_exc:
                print(f"[schedule_unmute role removal error] {inner_exc}")

        await mutes_col.delete_one({"guild_id": guild.id, "user_id": member.id})

    except asyncio.CancelledError:  # pragma: no cover
        print("[schedule_unmute] Task cancelled.")
    except Exception as exc:  # pragma: no cover
        print(f"[schedule_unmute error] {exc}")


async def fetch_punishments(guild_id: int, user_id: int) -> str:
    """Render a multi-line summary of every recorded action against a user."""
    data = await mod_col.find_one({"guild": str(guild_id), "user": str(user_id)})
    if not data:
        return "No recorded punishments."

    punishments: list[str] = []
    for key, records in data.items():
        if isinstance(records, list) and key != "notes":
            for record in records:
                ts = ""
                tval = record.get("time")
                if tval:
                    try:
                        dt = datetime.fromisoformat(tval)
                        ts = f" (on <t:{int(dt.timestamp())}:f>)"
                    except Exception:
                        ts = f" ({tval})"
                punishments.append(
                    f"**{key.title()}** - {record.get('reason', 'No reason')} "
                    f"*(by {record.get('by', 'Unknown')})*{ts}"
                )

    notes = data.get("notes", [])
    if notes:
        last_note = notes[-1]
        nts = ""
        nt = last_note.get("time")
        if nt:
            try:
                ndt = datetime.fromisoformat(nt)
                nts = f" (on <t:{int(ndt.timestamp())}:f>)"
            except Exception:
                nts = f" ({nt})"
        punishments.append(
            f"📝 **Note:** {last_note.get('note')} "
            f"*(by {last_note.get('by', 'Unknown')})*{nts}"
        )

    if not punishments:
        return "No past recorded punishments with this bot."
    if len(punishments) > 10:
        return "\n".join(punishments[:10]) + f"\n…(+{len(punishments) - 10} more)"
    return "\n".join(punishments)


def format_permissions(member: discord.Member) -> str:
    """Render the member's effective guild permissions as five-per-line."""
    perms = [perm.replace("_", " ").title() for perm, val in member.guild_permissions if val]
    if not perms:
        return "None"
    lines = [", ".join(perms[i:i + 5]) for i in range(0, len(perms), 5)]
    result = "\n".join(lines)
    return result if len(result) <= 1024 else result[:1000] + "…"


def format_roles(member: discord.Member) -> str:
    """Comma-joined role mentions, capped at 10."""
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    if not roles:
        return "None"
    if len(roles) > 10:
        return ", ".join(roles[:10]) + f"… (+{len(roles) - 10} more)"
    return ", ".join(roles)


def format_flags(member: discord.Member) -> str:
    """Comma-joined Discord flags (HypeSquad, Early Supporter, …)."""
    try:
        flags = [
            flag.name.replace("_", " ").title()
            for flag in member.public_flags.all()
        ]
    except Exception:
        flags = []
    if not flags:
        return "None"
    if len(flags) > 10:
        return ", ".join(flags[:10]) + f"… (+{len(flags) - 10} more)"
    return ", ".join(flags)


def format_activity(member: discord.Member) -> str:
    """Truncate the member's current activity name for the modview embed."""
    if not member.activity:
        return "None"
    return str(member.activity.name)[:100]


__all__ = [
    "schedule_unmute",
    "fetch_punishments",
    "format_permissions",
    "format_roles",
    "format_flags",
    "format_activity",
]
