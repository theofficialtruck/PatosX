"""Member resolution helpers used by moderation, tickets, and giveaways."""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database import staffperms_col


async def resolve_member(
    ctx: commands.Context, member_str: str
) -> discord.Member | None:
    """Resolve a member by ID, mention, or exact name.

    Returns ``None`` when no candidate matches; callers handle messaging.
    """
    try:
        member_id = int(member_str)
        member = ctx.guild.get_member(member_id)
        if member:
            return member
    except ValueError:
        pass

    if member_str.startswith("<@") and member_str.endswith(">"):
        try:
            member_id = int(
                member_str.replace("<@", "").replace("!", "").replace(">", "")
            )
        except ValueError:
            return None
        member = ctx.guild.get_member(member_id)
        if member:
            return member

    return discord.utils.get(ctx.guild.members, name=member_str)


async def get_category_support_members(
    guild: discord.Guild, category_name: str
) -> list[discord.Member]:
    """Members granted access to a particular ticket category.

    A staff member is matched if any of these permissions appear:
    ``tickets:<category_name>``, ``tickets:all``, or ``all``.
    """
    category_key = f"tickets:{category_name.lower()}"
    all_key = "tickets:all"

    docs = await staffperms_col.find({"guild": str(guild.id)}).to_list(None)

    member_ids: list[int] = []
    for entry in docs:
        perms = [p.lower() for p in entry.get("permissions", [])]
        if category_key in perms or all_key in perms or "all" in perms:
            member_ids.append(int(entry["user"]))

    members: list[discord.Member] = []
    for mid in member_ids:
        m = guild.get_member(mid)
        if m:
            members.append(m)
    return members


__all__ = ["resolve_member", "get_category_support_members"]
