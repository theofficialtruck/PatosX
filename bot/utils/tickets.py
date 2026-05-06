"""Helpers used across the ticket system (cog, views, on_message handler)."""

from __future__ import annotations

import io
import traceback
from datetime import datetime, timezone

import discord

from bot.database import logs_col, settings_col, tickets_col
from bot.utils.logging import log_action


async def ping_ticket_roles(channel: discord.TextChannel, guild_id) -> None:
    """Ping the staff role + every member with view-channel access.

    The ping is sent and immediately deleted; the goal is to bring the
    ticket to the support team's attention without leaving extra noise.
    """
    try:
        allowed_members: list[discord.Member] = []
        staff_role_mention = ""

        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member):
                if overwrite.view_channel:
                    allowed_members.append(target)
            elif isinstance(target, discord.Role):
                if overwrite.view_channel and target.name.lower() != "@everyone":
                    staff_role_mention = target.mention

        if not staff_role_mention:
            data = await settings_col.find_one({"guild": str(guild_id)})
            staff_role_id = data.get("staff_role") if data else None
            if staff_role_id:
                staff_role = channel.guild.get_role(int(staff_role_id))
                if staff_role:
                    staff_role_mention = staff_role.mention

        if not allowed_members and not staff_role_mention:
            return

        ping_parts: list[str] = []
        if staff_role_mention:
            ping_parts.append(staff_role_mention)
        if allowed_members:
            ping_parts.extend(member.mention for member in allowed_members)

        msg = await channel.send(content=" ".join(ping_parts))
        await msg.delete(delay=0)
    except Exception:  # pragma: no cover
        print("ping_ticket_roles ERROR:", traceback.format_exc())


async def actually_close_ticket(ctx, opener, forced: bool = False) -> None:
    """Persist a transcript, DM the opener, log the action, and delete the channel.

    Used by both ``ticketclose`` (after confirmation) and ``ticketforceclose``.
    """
    channel = ctx.channel

    messages = [m async for m in channel.history(limit=None, oldest_first=True)]
    transcript_text = "\n".join(
        [f"[{m.created_at}] {m.author}: {m.content}" for m in messages]
    )
    ticket_id = f"{channel.id}-{int(datetime.now(timezone.utc).timestamp())}"

    await tickets_col.insert_one(
        {
            "ticket_id": ticket_id,
            "guild_id": str(channel.guild.id),
            "channel_id": str(channel.id),
            "opener_id": str(opener.id) if opener else None,
            "closer_id": str(ctx.author.id),
            "closer_name": str(ctx.author),
            "transcript": transcript_text,
            "created_at": str(channel.created_at),
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "forced": forced,
        }
    )

    transcript_file = io.StringIO(transcript_text)
    discord_file = discord.File(
        fp=transcript_file, filename=f"{ticket_id}_transcript.txt"
    )

    if opener:
        try:
            await opener.send(
                embed=discord.Embed(
                    title="📜 Ticket Transcript",
                    description=f"Transcript for `{channel.name}` attached below.",
                    color=discord.Color.blue(),
                ),
                file=discord_file,
            )
        except Exception:
            pass

    action_type = "forceclose" if forced else "close"
    closer_text = f"{ctx.author} ({ctx.author.mention})"
    opener_text = f"{opener} ({opener.mention})" if opener else "Unknown"
    await log_action(
        ctx,
        f"Ticket `{channel.name}` closed by {closer_text} "
        f"(opener: {opener_text}){' [FORCED]' if forced else ''}",
        user_id=ctx.author.id,
        action_type=action_type,
    )

    if forced:
        await channel.send(f"✅ Ticket force-closed by {ctx.author.mention}.")
    else:
        await channel.send("✅ Ticket confirmed and closed.")
    await channel.delete()


async def get_ticket_button_permissions(guild_id) -> list[discord.SelectOption]:
    """Build select options describing every ticket category in this guild."""
    from bot.database import ticket_panels_col

    cursor = ticket_panels_col.find({"guild": str(guild_id)})
    categories: dict[str, dict] = {}

    async for panel in cursor:
        for btn in panel.get("buttons", []):
            cat = btn.get("category_name")
            label = btn.get("label")
            emoji = btn.get("emoji")
            if cat:
                categories[cat] = {"label": label or cat, "emoji": emoji}

    options: list[discord.SelectOption] = []
    if categories:
        options.append(
            discord.SelectOption(
                label="All Ticket Types",
                value="tickets:all",
                description="Access to ALL ticket types",
            )
        )

    for cat, info in categories.items():
        options.append(
            discord.SelectOption(
                label=info["label"],
                value=f"tickets:{cat}",
                description=f"Access to ticket type: {info['label']}",
                emoji=info["emoji"],
            )
        )

    return options


async def ticket_error(interaction: discord.Interaction, func) -> None:
    """Wrap an inner async function with a fallback error embed."""
    try:
        return await func()
    except Exception as exc:
        embed = discord.Embed(
            title="⚠️ Error",
            description=f"An unexpected error occurred:\n```{exc}```",
            color=discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


__all__ = [
    "ping_ticket_roles",
    "actually_close_ticket",
    "get_ticket_button_permissions",
    "ticket_error",
]
