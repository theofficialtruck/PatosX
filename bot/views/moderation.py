"""Modview/warn UI used by the moderation cog.

Confirmation flow: ``ModViewButtons`` opens for a target user, modal entries
collect details, and ``ModerationConfirmView`` is the yes/no double-check.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from bot.database import mod_col, mutes_col
from bot.utils.logging import log_action
from bot.utils.moderation import fetch_punishments
from bot.utils.time_parsing import parse_time


class NoteModal(discord.ui.Modal, title="Add Moderator Note"):
    """Add an arbitrary note to a user's mod record."""

    note = discord.ui.TextInput(
        label="Note Content",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Ban this user if he does it again",
        required=True,
        max_length=500,
    )

    def __init__(self, bot, ctx, member, message) -> None:
        super().__init__()
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ctx = self.ctx
        member = self.member
        note_content = self.note.value

        await mod_col.update_one(
            {"guild": str(ctx.guild.id), "user": str(member.id)},
            {
                "$push": {
                    "notes": {
                        "by": str(ctx.author),
                        "note": note_content,
                        "time": datetime.now(timezone.utc).isoformat(),
                    }
                }
            },
            upsert=True,
        )

        await interaction.response.send_message(
            f"✅ Note added for {member.mention}.", ephemeral=True
        )
        await log_action(
            ctx,
            f"Added note for {member}: {note_content}",
            user_id=member.id,
            action_type="note",
        )

        punishments = await fetch_punishments(ctx.guild.id, member.id)
        if not self.message.embeds:
            return
        embed = self.message.embeds[0]
        for index, field in enumerate(embed.fields):
            if field.name == "📜 Past Punishments":
                embed.set_field_at(
                    index,
                    name="📜 Past Punishments",
                    value=punishments,
                    inline=False,
                )
                break

        await self.message.edit(
            embed=embed,
            view=ModViewButtons(self.bot, ctx, member, self.message),
        )


class WarnModal(discord.ui.Modal, title="Moderator Action"):
    """Collect reason (and optionally duration) for a moderation action."""

    reason = discord.ui.TextInput(
        label="Reason (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
    )

    def __init__(self, bot, ctx, member, action, message) -> None:
        super().__init__()
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.action = action
        self.message = message
        if self.action != "warn":
            self.duration = discord.ui.TextInput(
                label="Duration (e.g., 1d 2h 7m; blank = permanent)",
                style=discord.TextStyle.short,
                required=False,
            )
            self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        reason = self.reason.value or "No reason provided"
        ctx = self.ctx
        duration_field = getattr(self, "duration", None)
        duration = duration_field.value if duration_field else None

        guild = interaction.guild
        member = guild.get_member(self.member.id)
        if member is None:
            try:
                member = await guild.fetch_member(self.member.id)
            except discord.NotFound:
                await interaction.followup.send(
                    "User is no longer in the server.", ephemeral=True
                )
                return

        embed = discord.Embed(
            title=f"⚠️ Confirm {self.action.capitalize()}",
            description=f"Are you sure you want to {self.action} {member.mention}?",
            color=discord.Color.orange(),
        )
        embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=False)
        embed.set_footer(text="This action will be logged.")

        if ctx.channel:
            confirm_view = ModerationConfirmView(
                self.action, member, reason, duration, ctx=ctx, message=self.message
            )
            await ctx.send(embed=embed, view=confirm_view)
            await interaction.followup.send(
                f"✅ Confirmation dialog sent to {ctx.channel.mention}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "❌ Could not send confirmation dialog.", ephemeral=True
            )


class ModViewButtons(discord.ui.View):
    """Action buttons shown alongside ``modview`` for a target user."""

    def __init__(self, bot, ctx, member, message=None) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.message = message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.ctx.author:
            try:
                await interaction.response.send_message(
                    "❌ This modview belongs to another moderator.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True

    @discord.ui.button(label="📋 Copy User ID", style=discord.ButtonStyle.grey)
    async def copy_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            f"🆔 **User ID:** `{self.member.id}`", ephemeral=True
        )

    @discord.ui.button(label="📝 Add Note", style=discord.ButtonStyle.blurple)
    async def add_note(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            NoteModal(self.bot, self.ctx, self.member, self.message)
        )

    @discord.ui.button(label="⚠️ Warn", style=discord.ButtonStyle.danger)
    async def warn_user(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            WarnModal(self.bot, self.ctx, self.member, "warn", self.message)
        )

    @discord.ui.button(label="🔇 Mute", style=discord.ButtonStyle.danger)
    async def mute_user(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            WarnModal(self.bot, self.ctx, self.member, "mute", self.message)
        )

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.danger)
    async def kick_user(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            WarnModal(self.bot, self.ctx, self.member, "kick", self.message)
        )

    @discord.ui.button(label="⛔ Ban", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            WarnModal(self.bot, self.ctx, self.member, "ban", self.message)
        )

    @discord.ui.button(label="🧹 Clear Warns", style=discord.ButtonStyle.green)
    async def clear_warns(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await mod_col.update_one(
            {"guild": str(self.ctx.guild.id), "user": str(self.member.id)},
            {"$set": {"warnings": []}},
        )
        await interaction.response.send_message(
            f"✅ All warnings for {self.member.mention} have been cleared.",
            ephemeral=True,
        )
        await log_action(
            self.ctx,
            f"Cleared all warnings for {self.member}",
            user_id=self.member.id,
            action_type="clearwarns",
        )

    @discord.ui.button(label="🧽 Clear Punishment", style=discord.ButtonStyle.green)
    async def clear_specific(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "🔢 Please enter the **number** of the punishment or note you wish to clear.\n"
            "Example: `1` to remove the first one.",
            ephemeral=True,
        )

        def check(message):
            return (
                message.author == self.ctx.author
                and message.channel == self.ctx.channel
            )

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=30.0)
            content = msg.content.strip()
            if not content.isdigit():
                await interaction.followup.send(
                    f"❌ Expected a plain number, got `{content}`.",
                    ephemeral=True,
                )
                return
            number = int(content) - 1
        except Exception:
            await interaction.followup.send("⌛ Timed out. Please try again.", ephemeral=True)
            return

        guild_id = str(self.ctx.guild.id)
        user_id = str(self.member.id)
        data = await mod_col.find_one({"guild": guild_id, "user": user_id})

        if not data:
            await interaction.followup.send(
                "❌ No punishments or notes found for this user.",
                ephemeral=True,
            )
            return

        entries: list[tuple[str, int, dict]] = []
        for key, records in data.items():
            if isinstance(records, list):
                for index, record in enumerate(records):
                    entries.append((key, index, record))

        if number < 0 or number >= len(entries):
            await interaction.followup.send(
                "❌ That number doesn’t match any record.", ephemeral=True
            )
            return

        key, index, record = entries[number]
        data[key].pop(index)
        await mod_col.update_one(
            {"guild": guild_id, "user": user_id}, {"$set": {key: data[key]}}
        )

        entry_desc = (
            f"{key.title()} - "
            f"{record.get('reason', record.get('note', 'No details'))} "
            f"(by {record.get('by', 'Unknown')})"
        )
        await log_action(
            self.ctx,
            f"Cleared specific {key} for {self.member}: {entry_desc}",
            user_id=self.member.id,
            action_type="clearspecific",
        )

        await interaction.followup.send(
            f"✅ Cleared **{key} #{number + 1}** for {self.member.mention}.",
            ephemeral=True,
        )

        punishments = await fetch_punishments(self.ctx.guild.id, self.member.id)
        if not self.message or not self.message.embeds:
            return
        embed = self.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "📜 Past Punishments":
                embed.set_field_at(
                    i,
                    name="📜 Past Punishments",
                    value=punishments,
                    inline=False,
                )
                break
        await self.message.edit(
            embed=embed,
            view=ModViewButtons(self.bot, self.ctx, self.member, self.message),
        )


class ModerationConfirmView(discord.ui.View):
    """Yes/No confirmation that actually performs the moderation action."""

    def __init__(
        self,
        action,
        member,
        reason,
        duration=None,
        ctx=None,
        interaction=None,
        message=None,
    ) -> None:
        super().__init__(timeout=60)
        self.action = action
        self.member = member
        self.reason = reason
        self.duration = duration
        self.ctx = ctx
        self.interaction = interaction
        self.message = message
        self.confirmed = False

    @discord.ui.button(label="✅ Yes", style=discord.ButtonStyle.green, custom_id="confirm_yes")
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != (
            self.ctx.author.id if self.ctx else self.interaction.user.id
        ):
            await interaction.response.send_message(
                "❌ You can't confirm this action!", ephemeral=True
            )
            return
        self.confirmed = True
        await self._execute(interaction)

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.red, custom_id="confirm_no")
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != (
            self.ctx.author.id if self.ctx else self.interaction.user.id
        ):
            await interaction.response.send_message(
                "❌ You can't cancel this action!", ephemeral=True
            )
            return

        self.confirmed = False
        embed = discord.Embed(
            title="❌ Moderation Cancelled",
            description=(
                f"The {self.action} action on {self.member.mention} has been cancelled."
            ),
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)

    async def _execute(self, interaction: discord.Interaction) -> None:
        try:
            ctx = self.ctx or self.interaction
            member = self.member
            reason = self.reason
            duration = self.duration
            msg = ""

            if self.action == "warn":
                await mod_col.update_one(
                    {"guild": str(ctx.guild.id), "user": str(member.id)},
                    {
                        "$push": {
                            "warnings": {
                                "by": str(ctx.author),
                                "reason": reason,
                                "time": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    },
                    upsert=True,
                )
                try:
                    await member.send(
                        f"⚠️ You were warned in **{ctx.guild.name}**\nReason: {reason}"
                    )
                except discord.Forbidden:
                    pass
                msg = f"✅ Warned {member.mention}."
                await log_action(
                    ctx,
                    f"Warn executed on {member}: {reason}",
                    user_id=member.id,
                    action_type="warn",
                )

            elif self.action == "mute":
                mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
                if not mute_role:
                    mute_role = await ctx.guild.create_role(name="Muted")
                    for ch in ctx.guild.channels:
                        await ch.set_permissions(mute_role, speak=False, send_messages=False)

                await member.add_roles(mute_role, reason=reason)
                await mod_col.update_one(
                    {"guild": str(ctx.guild.id), "user": str(member.id)},
                    {
                        "$push": {
                            "mutes": {
                                "by": str(ctx.author),
                                "reason": reason,
                                "time": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    },
                    upsert=True,
                )

                if duration:
                    try:
                        seconds = parse_time(duration)
                        end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                        await mutes_col.update_one(
                            {"guild_id": ctx.guild.id, "user_id": member.id},
                            {"$set": {"mute_end": end_time}},
                            upsert=True,
                        )
                        msg = f"🔇 Muted {member.mention} until <t:{int(end_time.timestamp())}:f>."
                    except Exception as exc:
                        msg = f"🔇 Muted {member.mention}. (Duration error: {exc})"
                else:
                    msg = f"🔇 Muted {member.mention}."
                await log_action(
                    ctx,
                    f"Mute executed on {member}: {reason}",
                    user_id=member.id,
                    action_type="mute",
                )

            elif self.action == "kick":
                await member.kick(reason=f"{reason} (by {ctx.author})")
                await mod_col.update_one(
                    {"guild": str(ctx.guild.id), "user": str(member.id)},
                    {
                        "$push": {
                            "kicks": {
                                "by": str(ctx.author),
                                "reason": reason,
                                "time": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    },
                    upsert=True,
                )
                msg = f"✅ Kicked {member.mention}."
                await log_action(
                    ctx,
                    f"Kick executed on {member}: {reason}",
                    user_id=member.id,
                    action_type="kick",
                )

            elif self.action == "ban":
                await member.ban(reason=f"{reason} (by {ctx.author})")
                await mod_col.update_one(
                    {"guild": str(ctx.guild.id), "user": str(member.id)},
                    {
                        "$push": {
                            "bans": {
                                "by": str(ctx.author),
                                "reason": reason,
                                "time": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    },
                    upsert=True,
                )
                msg = f"✅ Banned {member.mention}."
                await log_action(
                    ctx,
                    f"Ban executed on {member}: {reason}",
                    user_id=member.id,
                    action_type="ban",
                )

            embed = discord.Embed(
                title=f"✅ {self.action.capitalize()} Executed",
                description=(
                    f"{msg}\n\nReason: {reason}"
                    + (f"\nDuration: {duration}" if duration else "")
                ),
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=embed, view=None)

            if self.message:
                punishments = await fetch_punishments(ctx.guild.id, member.id)
                if self.message.embeds:
                    modview_embed = self.message.embeds[0]
                    for i, field in enumerate(modview_embed.fields):
                        if field.name == "📜 Past Punishments":
                            modview_embed.set_field_at(
                                i,
                                name="📜 Past Punishments",
                                value=punishments,
                                inline=False,
                            )
                            break
                    from bot.utils.state import get_bot

                    await self.message.edit(
                        embed=modview_embed,
                        view=ModViewButtons(get_bot(), ctx, member, self.message),
                    )

        except Exception as exc:
            error_embed = discord.Embed(
                title=f"❌ {self.action.capitalize()} Failed",
                description=f"An error occurred: `{type(exc).__name__}: {exc}`",
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=error_embed, view=None)


__all__ = [
    "NoteModal",
    "WarnModal",
    "ModViewButtons",
    "ModerationConfirmView",
]
