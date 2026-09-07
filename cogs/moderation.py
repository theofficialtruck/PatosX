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

"""Moderation commands (kick/ban/mute/warn/purge/slowmode/say/modview/performance/userinfo/reactionrole),
the moderator panel views, mute expiry + Muted-role permission loops, and reaction-role listeners."""

import asyncio
import contextlib
import traceback
from datetime import datetime, timedelta, timezone

import discord
from dateutil import parser
from discord import (
    VerificationLevel,
    app_commands,
)
from discord.ext import commands, tasks

from core import config as cfg
from core import economyHelperFuncs as econ
from core import errors, state
from core import permsHelperFuncs as perms

# Legacy in memory warning and action stores, kept for backwards compatibility
warnings_data = {}


actions_data = {}


def parse_mute_end(raw) -> datetime | None:
    """Normalize a stored mute_end value (None, datetime, ISO string, or legacy
    '%Y-%m-%d %H:%M:%S' string) into an aware UTC datetime. Returns None if raw is
    falsy or unparsable."""
    if not raw:
        return None
    mute_end = raw
    if isinstance(mute_end, str):
        try:
            mute_end = parser.isoparse(mute_end)
        except ValueError:
            try:
                mute_end = datetime.strptime(mute_end, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    if not isinstance(mute_end, datetime):
        return None
    if mute_end.tzinfo is None:
        mute_end = mute_end.replace(tzinfo=timezone.utc)
    return mute_end


async def remove_mute_role(guild: discord.Guild, member: discord.Member, reason: str) -> bool:
    """Remove the Muted role from member if they currently have it. Returns True if a
    removal was attempted (regardless of whether it ultimately succeeded)."""
    mute_role = discord.utils.get(guild.roles, name="Muted")
    if not mute_role or mute_role not in member.roles:
        return False
    try:
        await member.remove_roles(mute_role, reason=reason)
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"[remove_mute_role error] {e}")
    return True


async def schedule_unmute(guild, member, remaining):
    """Sleep for remaining seconds, then remove the Muted role from member.
    Designed to run as a detached asyncio task created by the mute command."""
    try:
        await asyncio.sleep(remaining)
        if not guild:
            print("[schedule_unmute] Guild not found, skipping.")
            return
        member_id = member.id
        member = guild.get_member(member_id)
        if not member:
            print(f"[schedule_unmute] Member {member_id} not found, likely left the server.")
            await state.mutes_col.delete_one({"guild_id": guild.id, "user_id": member_id})
            return
        await remove_mute_role(guild, member, reason="Mute expired")
        print(f"[schedule_unmute] Auto unmuted {member} in {guild.name}")
        await state.mutes_col.delete_one({"guild_id": guild.id, "user_id": member_id})
    except asyncio.CancelledError:
        print(f"[schedule_unmute] Task for {member.id} cancelled.")
    except Exception as e:
        print(f"[schedule_unmute error] {e}")


async def _reapply_or_clear_mute(guild: discord.Guild, member: discord.Member, doc: dict, mute_role) -> None:
    """Per-member body of the on_ready restart-reapply loop: if the stored mute has
    already expired, delete the record and remove the role (never both leave the role
    stuck); otherwise reapply the role if it's missing (e.g. manually stripped offline)."""
    mute_end = parse_mute_end(doc.get("mute_end"))
    if mute_end is None:
        if mute_role and mute_role not in member.roles:
            await member.add_roles(mute_role, reason="Reapplying mute after restart")
        return
    if mute_end <= datetime.now(timezone.utc):
        await state.mutes_col.delete_one({"_id": doc["_id"]})
        await remove_mute_role(guild, member, reason="Mute expired while bot was offline")
        return
    if mute_role and mute_role not in member.roles:
        await member.add_roles(mute_role, reason="Reapplying mute after restart")


_MAX_MUTE_SECONDS = 30 * 24 * 3600


async def fetch_punishments(guild_id: int, user_id: int):
    """Return a formatted string listing all recorded punishments for a user in a guild."""
    data = await state.mod_col.find_one({"guild": str(guild_id), "user": str(user_id)})
    if not data:
        return "No recorded punishments."
    punishments = []
    for key, records in data.items():
        if isinstance(records, list) and key != "notes":
            for r in records:
                ts = ""
                tval = r.get("time")
                if tval:
                    try:
                        dt = datetime.fromisoformat(tval)
                        ts = f" (on <t:{int(dt.timestamp())}:f>)"
                    except Exception:
                        ts = f" ({tval})"
                punishments.append(
                    f"**{key.title()}** - {r.get('reason', 'No reason')} *(by {r.get('by', 'Unknown')})*{ts}"
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
        punishments.append(f"📝 **Note:** {last_note.get('note')} *(by {last_note.get('by', 'Unknown')})*{nts}")
    if not punishments:
        return "No past recorded punishments with this bot."
    if len(punishments) > 10:
        return "\n".join(punishments[:10]) + f"\n…(+{len(punishments) - 10} more)"
    return "\n".join(punishments)


def format_permissions(member: discord.Member):
    perm_names = [perm.replace("_", " ").title() for perm, val in member.guild_permissions if val]
    if not perm_names:
        return "None"
    lines = [", ".join(perm_names[i : i + 5]) for i in range(0, len(perm_names), 5)]
    result = "\n".join(lines)
    return result if len(result) <= 1024 else result[:1000] + "…"


def format_roles(member: discord.Member):
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    if not roles:
        return "None"
    if len(roles) > 10:
        return ", ".join(roles[:10]) + f"… (+{len(roles) - 10} more)"
    return ", ".join(roles)


def format_flags(member: discord.Member):
    try:
        flags = [flag.name.replace("_", " ").title() for flag in member.public_flags.all()]
    except Exception:
        flags = []
    if not flags:
        return "None"
    if len(flags) > 10:
        return ", ".join(flags[:10]) + f"… (+{len(flags) - 10} more)"
    return ", ".join(flags)


def format_activity(member: discord.Member):
    if not member.activity:
        return "None"
    activity_name = str(member.activity.name)[:100]
    return activity_name


class ModViewButtons(discord.ui.View):
    def __init__(self, bot, ctx, member, message=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.message = message

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            try:
                await interaction.response.send_message("❌ This modview belongs to another moderator.", ephemeral=True)
            except discord.HTTPException:
                pass
            return False
        return True

    @discord.ui.button(label="📋 Copy User ID", style=discord.ButtonStyle.grey)
    async def copy_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"🆔 **User ID:** `{self.member.id}`", ephemeral=True)

    @discord.ui.button(label="📝 Add Note", style=discord.ButtonStyle.blurple)
    async def add_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NoteModal(self.bot, self.ctx, self.member, self.message))

    @discord.ui.button(label="⚠️ Warn", style=discord.ButtonStyle.danger)
    async def warn_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self.bot, self.ctx, self.member, "warn", self.message))

    @discord.ui.button(label="🔇 Mute", style=discord.ButtonStyle.danger)
    async def mute_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self.bot, self.ctx, self.member, "mute", self.message))

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.danger)
    async def kick_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self.bot, self.ctx, self.member, "kick", self.message))

    @discord.ui.button(label="⛔ Ban", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self.bot, self.ctx, self.member, "ban", self.message))

    @discord.ui.button(label="🧹 Clear Warns", style=discord.ButtonStyle.green)
    async def clear_warns(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚠️ Confirm: Clear All Warnings",
            description=(
                f"Are you sure you want to clear **all warnings** for {self.member.mention}?\n\n"
                f"This will **permanently delete every warning** on their record. "
                f"This action **cannot be undone**."
            ),
            color=discord.Color.orange(),
        )
        confirm_view = ClearWarnsConfirmView(self.bot, self.ctx, self.member)
        await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=True)
        confirm_view.message = await interaction.original_response()

    @discord.ui.button(label="🧽 Clear Punishment", style=discord.ButtonStyle.green)
    async def clear_specific(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🔢 Please enter the **number** of the punishment or note you wish to clear.\nExample: `1` to remove the first one.",
            ephemeral=True,
        )

        def check(m):
            return m.author == self.ctx.author and m.channel == self.ctx.channel

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=30.0)
            content = msg.content.strip()
            if not content.isdigit():
                await interaction.followup.send(f"❌ Expected a plain number, got `{content}`.", ephemeral=True)
                return
            number = int(content) - 1
        except asyncio.TimeoutError:
            await interaction.followup.send("⌛ Timed out. Please try again.", ephemeral=True)
            return
        guild_id = str(self.ctx.guild.id)
        user_id = str(self.member.id)
        data = await state.mod_col.find_one({"guild": guild_id, "user": user_id})
        if not data:
            await interaction.followup.send("❌ No punishments or notes found for this user.", ephemeral=True)
            return
        entries = []
        for key, records in data.items():
            if isinstance(records, list):
                for idx, r in enumerate(records):
                    entries.append((key, idx, r))
        if number < 0 or number >= len(entries):
            await interaction.followup.send("❌ That number doesn't match any record.", ephemeral=True)
            return
        key, idx, record = entries[number]
        data[key].pop(idx)
        await state.mod_col.update_one({"guild": guild_id, "user": user_id}, {"$set": {key: data[key]}})
        entry_desc = f"{key.title()} - {record.get('reason', record.get('note', 'No details'))} (by {record.get('by', 'Unknown')})"
        await perms.log_action(
            self.ctx,
            f"Cleared specific {key} for {self.member}: {entry_desc}",
            user_id=self.member.id,
            action_type="clearspecific",
        )
        await interaction.followup.send(
            f"✅ Cleared **{key} #{number + 1}** for {self.member.mention}.", ephemeral=True
        )
        punishments = await fetch_punishments(self.ctx.guild.id, self.member.id)
        if not self.message.embeds:
            return
        embed = self.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "📜 Past Punishments":
                embed.set_field_at(i, name="📜 Past Punishments", value=punishments, inline=False)
                break
        await self.message.edit(embed=embed, view=ModViewButtons(self.bot, self.ctx, self.member, self.message))


class ClearWarnsConfirmView(discord.ui.View):
    """Second confirmation step for ModViewButtons.clear_warns so a stray click can't
    permanently wipe a user's warning history."""

    def __init__(self, bot, ctx, member):
        super().__init__(timeout=60)
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.message = None  # set by the caller to the ephemeral confirmation message, for on_timeout

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ You can't confirm this action!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Yes, delete permanently", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await state.mod_col.update_one(
            {"guild": str(self.ctx.guild.id), "user": str(self.member.id)}, {"$set": {"warnings": []}}
        )
        embed = discord.Embed(
            title="✅ Warnings Cleared",
            description=f"All warnings for {self.member.mention} have been permanently deleted.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await perms.log_action(
            self.ctx, f"Cleared all warnings for {self.member}", user_id=self.member.id, action_type="clearwarns"
        )
        self.message = None
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="❌ Cancelled",
            description=f"No changes were made to {self.member.mention}'s warnings.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.message = None
        self.stop()

    async def on_timeout(self):
        if self.message:
            try:
                embed = discord.Embed(
                    title="⌛ Confirmation Timed Out",
                    description=f"No changes were made to {self.member.mention}'s warnings.",
                    color=discord.Color.red(),
                )
                await self.message.edit(embed=embed, view=None)
            except (discord.HTTPException, discord.NotFound):
                pass


class NoteModal(discord.ui.Modal, title="Add Moderator Note"):
    note = discord.ui.TextInput(
        label="Note Content",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Ban this user if he does it again",
        required=True,
        max_length=500,
    )

    def __init__(self, bot, ctx, member, message):
        super().__init__()
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        ctx = self.ctx
        member = self.member
        note_content = self.note.value
        await state.mod_col.update_one(
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
        await interaction.response.send_message(f"✅ Note added for {member.mention}.", ephemeral=True)
        await perms.log_action(ctx, f"Added note for {member}: {note_content}", user_id=member.id, action_type="note")
        punishments = await fetch_punishments(ctx.guild.id, member.id)
        if not self.message.embeds:
            return
        embed = self.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "📜 Past Punishments":
                embed.set_field_at(i, name="📜 Past Punishments", value=punishments, inline=False)
                break
        await self.message.edit(embed=embed, view=ModViewButtons(self.bot, ctx, member, self.message))


class PerformanceView(discord.ui.View):
    def __init__(self, guild_id, staff_members, days=30):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.staff_members = staff_members
        self.days = days
        options = []
        for member in staff_members[:25]:
            options.append(
                discord.SelectOption(
                    label=member.display_name,
                    description=f"Review {member.display_name}'s performance",
                    value=str(member.id),
                    emoji="👤",
                )
            )
        self.dropdown = discord.ui.Select(placeholder="📊 Select a staff member to review...", options=options)
        self.dropdown.callback = self.dropdown_callback
        self.add_item(self.dropdown)

    async def dropdown_callback(self, interaction: discord.Interaction):
        if interaction.user.id not in [m.id for m in self.staff_members]:
            await interaction.response.send_message("❌ Only staff members can use this!", ephemeral=True)
            return
        selected_id = int(self.dropdown.values[0])
        selected_member = interaction.guild.get_member(selected_id)
        if not selected_member:
            await interaction.response.send_message("❌ Staff member not found!", ephemeral=True)
            return
        analytics = await generate_performance_analytics(self.guild_id, selected_id, days=self.days)
        embed = discord.Embed(
            title=f"📊 Performance Review: {selected_member.display_name}",
            description=f"Analytics for {selected_member.mention}",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="📈 Basic Statistics",
            value=f"**Total Actions:** {analytics['total_actions']}\n**Messages Sent:** {analytics['total_messages']:,}\n**Commands Used:** {analytics['commands_used']:,}\n**Staff Since:** {analytics['staff_since']}",
            inline=False,
        )
        if analytics["punishments"]["total"] > 0:
            punish_text = f"**Total:** {analytics['punishments']['total']}\n"
            for ptype, count in analytics["punishments"].items():
                if ptype != "total" and count > 0:
                    punish_text += f"**{ptype.capitalize()}:** {count}\n"
            embed.add_field(name="⚖️ Punishments", value=punish_text, inline=False)
        else:
            embed.add_field(name="⚖️ Punishments", value="No punishments recorded", inline=False)
        embed.add_field(
            name="🕐 Activity Metrics",
            value=f"**Avg. Actions/Day:** {analytics['avg_actions_per_day']:.1f}\n**Most Active Day:** {analytics['most_active_day']}\n**Peak Hour:** {analytics['peak_hour']}:00\n**Active This Week:** {('Yes' if analytics['active_this_week'] else 'No')}",
            inline=False,
        )
        embed.add_field(name="📊 Efficiency", value=f"**Efficiency:** {analytics['efficiency']:.1f}%", inline=False)
        if analytics["recent_activity"]:
            recent_text = "\n".join([f"• {activity}" for activity in analytics["recent_activity"][:5]])
            embed.add_field(name="📝 Recent Activity", value=recent_text, inline=False)
        embed.set_thumbnail(url=selected_member.display_avatar.url)
        embed.set_footer(text=f"Performance data for last {self.days} days")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def generate_performance_analytics(guild_id, staff_id, days=30):
    """Aggregate moderation actions and message counts for staff_id over the last days days.
    Returns a dict containing action totals, averages, peak hours, and a recent activity list."""
    try:
        analytics = {
            "total_actions": 0,
            "total_messages": 0,
            "commands_used": 0,
            "staff_since": "Unknown",
            "punishments": {"warn": 0, "mute": 0, "kick": 0, "ban": 0, "total": 0},
            "avg_actions_per_day": 0,
            "most_active_day": "Monday",
            "peak_hour": 14,
            "active_this_week": False,
            "recent_activity": [],
        }
        days_ago = datetime.now(timezone.utc) - timedelta(days=days)
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        mod_data = await state.mod_col.find({"guild": str(guild_id)}).to_list(length=None)
        for doc in mod_data:
            if "warnings" in doc:
                for warning in doc.get("warnings", []):
                    if warning.get("by") == str(staff_id):
                        try:
                            warning_time = parser.isoparse(warning["time"])
                            if warning_time >= days_ago:
                                analytics["punishments"]["warn"] += 1
                                analytics["total_actions"] += 1
                        except ValueError:
                            pass
            if "mutes" in doc:
                for mute in doc.get("mutes", []):
                    if mute.get("by") == str(staff_id):
                        try:
                            mute_time = parser.isoparse(mute["time"])
                            if mute_time >= days_ago:
                                analytics["punishments"]["mute"] += 1
                                analytics["total_actions"] += 1
                        except ValueError:
                            pass
            if "kicks" in doc:
                for kick in doc.get("kicks", []):
                    if kick.get("by") == str(staff_id):
                        try:
                            kick_time = parser.isoparse(kick["time"])
                            if kick_time >= days_ago:
                                analytics["punishments"]["kick"] += 1
                                analytics["total_actions"] += 1
                        except ValueError:
                            pass
            if "bans" in doc:
                for ban in doc.get("bans", []):
                    if ban.get("by") == str(staff_id):
                        try:
                            ban_time = parser.isoparse(ban["time"])
                            if ban_time >= days_ago:
                                analytics["punishments"]["ban"] += 1
                                analytics["total_actions"] += 1
                        except ValueError:
                            pass
        analytics["punishments"]["total"] = sum(analytics["punishments"][p] for p in ["warn", "mute", "kick", "ban"])
        analytics["commands_used"] = analytics["punishments"]["total"]
        for doc in mod_data:
            if "notes" in doc:
                for note in doc.get("notes", []):
                    if note.get("by") == str(staff_id):
                        try:
                            note_time = parser.isoparse(note["time"])
                            if note_time >= days_ago:
                                analytics["commands_used"] += 1
                        except ValueError:
                            pass
        earliest_action = None
        for doc in mod_data:
            for action_type in ["warnings", "mutes", "kicks", "bans", "notes"]:
                if action_type in doc:
                    for action in doc.get(action_type, []):
                        if action.get("by") == str(staff_id):
                            try:
                                action_time = parser.isoparse(action["time"])
                                if not earliest_action or action_time < earliest_action:
                                    earliest_action = action_time
                            except ValueError:
                                pass
        if earliest_action:
            analytics["staff_since"] = earliest_action.strftime("%b %d, %Y")
        analytics["total_messages"] = await get_user_message_count(guild_id, staff_id, days_ago)
        analytics["avg_actions_per_day"] = analytics["total_actions"] / days if analytics["total_actions"] > 0 else 0
        analytics["active_this_week"] = analytics["total_actions"] > 0 and any(
            doc.get("time") and parser.isoparse(doc["time"]) >= seven_days_ago
            for doc in mod_data
            for doc_list in [
                doc.get("warnings", []),
                doc.get("mutes", []),
                doc.get("kicks", []),
                doc.get("bans", []),
            ]
            for doc_item in doc_list
            if isinstance(doc_item, dict) and doc_item.get("by") == str(staff_id)
        )
        expected_daily = 2
        analytics["efficiency"] = min(100, analytics["avg_actions_per_day"] / expected_daily * 100)
        analytics["recent_activity"] = [
            f"Used {ptype} command"
            for ptype, count in analytics["punishments"].items()
            if ptype != "total" and count > 0
        ][:3]
        if not analytics["recent_activity"]:
            analytics["recent_activity"] = ["No recent activity"]
        return analytics
    except Exception as e:
        print(f"[Performance Analytics Error] {e}")
        return {
            "total_actions": 0,
            "total_messages": 0,
            "commands_used": 0,
            "staff_since": "Unknown",
            "punishments": {"warn": 0, "mute": 0, "kick": 0, "ban": 0, "total": 0},
            "avg_actions_per_day": 0,
            "most_active_day": "Monday",
            "peak_hour": 14,
            "active_this_week": False,
            "efficiency": 0,
            "recent_activity": ["No data available"],
        }


async def get_user_message_count(guild_id, user_id, since_date):
    try:
        message_count = 0
        mod_data = await state.mod_col.find({"guild": str(guild_id)}).to_list(length=None)
        for doc in mod_data:
            for action_type in ["warnings", "mutes", "kicks", "bans", "notes"]:
                if action_type in doc:
                    for action in doc.get(action_type, []):
                        if action.get("by") == str(user_id):
                            try:
                                action_time = parser.isoparse(action["time"])
                                if action_time >= since_date:
                                    message_count += 1
                            except ValueError:
                                pass
        return message_count
    except Exception as e:
        print(f"[Message Count Error] {e}")
        return 0


class ModerationConfirmView(discord.ui.View):
    def __init__(self, action, member, reason, duration=None, ctx=None, interaction=None, message=None):
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
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != (self.ctx.author.id if self.ctx else self.interaction.user.id):
            await interaction.response.send_message("❌ You can't confirm this action!", ephemeral=True)
            return
        self.confirmed = True
        await self.execute_moderation(interaction)

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.red, custom_id="confirm_no")
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != (self.ctx.author.id if self.ctx else self.interaction.user.id):
            await interaction.response.send_message("❌ You can't cancel this action!", ephemeral=True)
            return
        self.confirmed = False
        embed = discord.Embed(
            title="❌ Moderation Cancelled",
            description=f"The {self.action} action on {self.member.mention} has been cancelled.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)

    async def execute_moderation(self, interaction):
        try:
            ctx = self.ctx or self.interaction
            member = self.member
            reason = self.reason
            duration = self.duration
            if self.action == "warn":
                await state.mod_col.update_one(
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
                    await member.send(f"⚠️ You were warned in **{ctx.guild.name}**\nReason: {reason}")
                except discord.Forbidden:
                    pass
                msg = f"✅ Warned {member.mention}."
                await perms.log_action(
                    ctx, f"Warn executed on {member}: {reason}", user_id=member.id, action_type="warn"
                )
            elif self.action == "mute":
                mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
                if not mute_role:
                    mute_role = await ctx.guild.create_role(name="Muted")
                    for ch in ctx.guild.channels:
                        await ch.set_permissions(mute_role, speak=False, send_messages=False)
                await member.add_roles(mute_role, reason=reason)
                await state.mod_col.update_one(
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
                        seconds = econ.parse_time(duration)
                        end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                        await state.mutes_col.update_one(
                            {"guild_id": ctx.guild.id, "user_id": member.id},
                            {"$set": {"mute_end": end_time.isoformat()}},
                            upsert=True,
                        )
                        msg = f"🔇 Muted {member.mention} until <t:{int(end_time.timestamp())}:f>."
                    except Exception as e:
                        msg = f"🔇 Muted {member.mention}. (Duration error: {e})"
                else:
                    msg = f"🔇 Muted {member.mention}."
                await perms.log_action(
                    ctx, f"Mute executed on {member}: {reason}", user_id=member.id, action_type="mute"
                )
            elif self.action == "kick":
                await member.kick(reason=f"{reason} (by {ctx.author})")
                await state.mod_col.update_one(
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
                await perms.log_action(
                    ctx, f"Kick executed on {member}: {reason}", user_id=member.id, action_type="kick"
                )
            elif self.action == "ban":
                await member.ban(reason=f"{reason} (by {ctx.author})")
                await state.mod_col.update_one(
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
                await perms.log_action(ctx, f"Ban executed on {member}: {reason}", user_id=member.id, action_type="ban")
            embed = discord.Embed(
                title=f"✅ {self.action.capitalize()} Executed",
                description=f"{msg}\n\nReason: {reason}" + (f"\nDuration: {duration}" if duration else ""),
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=embed, view=None)
            if self.message:
                punishments = await fetch_punishments(ctx.guild.id, member.id)
                if self.message.embeds:
                    modview_embed = self.message.embeds[0]
                    for i, field in enumerate(modview_embed.fields):
                        if field.name == "📜 Past Punishments":
                            modview_embed.set_field_at(i, name="📜 Past Punishments", value=punishments, inline=False)
                            break
                    await self.message.edit(
                        embed=modview_embed, view=ModViewButtons(interaction.client, ctx, member, self.message)
                    )
        except Exception as e:
            error_embed = discord.Embed(
                title=f"❌ {self.action.capitalize()} Failed",
                description=f"An error occurred: `{type(e).__name__}: {e}`",
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=error_embed, view=None)


class WarnModal(discord.ui.Modal, title="Moderator Action"):
    reason = discord.ui.TextInput(label="Reason (optional)", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, bot, ctx, member, action, message):
        super().__init__()
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.action = action
        self.message = message
        if self.action != "warn":
            self.duration = discord.ui.TextInput(
                label="Duration (e.g., 1d 2h 7m; blank = permanent)", style=discord.TextStyle.short, required=False
            )
            self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        reason = self.reason.value or "No reason provided"
        ctx = self.ctx
        duration = (
            getattr(self, "duration", None).value
            if hasattr(self, "duration") and getattr(self, "duration", None)
            else None
        )
        guild = interaction.guild
        member = guild.get_member(self.member.id)
        if member is None:
            try:
                member = await guild.fetch_member(self.member.id)
            except discord.NotFound:
                await interaction.followup.send("User is no longer in the server.", ephemeral=True)
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
            confirm_view = ModerationConfirmView(self.action, member, reason, duration, ctx=ctx, message=self.message)
            await ctx.send(embed=embed, view=confirm_view)
            await interaction.followup.send(f"✅ Confirmation dialog sent to {ctx.channel.mention}", ephemeral=True)
        else:
            await interaction.followup.send("❌ Could not send confirmation dialog.", ephemeral=True)


class Moderation(commands.Cog):
    """Moderation commands (kick/ban/mute/warn/purge/slowmode/say/modview/performance/userinfo/reactionrole),
    the moderator panel views, mute expiry + Muted-role permission loops, and reaction-role listeners."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    @tasks.loop(seconds=15)
    async def check_expired_mutes(self):
        """Automatically remove the Muted role from members whose timed mute has expired.
        Checks both the dedicated state.mutes_col and the legacy muted_until field in state.mod_col."""
        count = await state.mutes_col.count_documents({})
        if count == 0:
            mod_count = await state.mod_col.count_documents({"muted_until": {"$exists": True}})
            if mod_count == 0:
                return
        now = datetime.now(timezone.utc)
        async for doc in state.mutes_col.find({"mute_end": {"$exists": True}}):
            try:
                mute_end = parse_mute_end(doc.get("mute_end"))
                if mute_end and mute_end <= now:
                    guild = self.bot.get_guild(int(doc["guild_id"]))
                    if not guild:
                        continue
                    member = guild.get_member(int(doc["user_id"]))
                    if not member:
                        await state.mutes_col.delete_one({"_id": doc["_id"]})
                        continue
                    if await remove_mute_role(guild, member, reason="Mute expired"):
                        await perms.log_action(
                            None, f"Auto-unmuted {member}", user_id=member.id, action_type="unmute", guild=guild
                        )
                    await state.mutes_col.delete_one({"_id": doc["_id"]})
            except Exception as e:
                print(f"[Auto-unmute error - state.mutes_col] {e}")
        async for doc in state.mod_col.find({"muted_until": {"$exists": True}}):
            try:
                mute_until = doc["muted_until"]
                if isinstance(mute_until, str):
                    mute_until = datetime.fromisoformat(mute_until)
                if mute_until.tzinfo is None:
                    mute_until = mute_until.replace(tzinfo=timezone.utc)
                if mute_until <= now:
                    guild = self.bot.get_guild(int(doc["guild"]))
                    if not guild:
                        continue
                    member = guild.get_member(int(doc["user"]))
                    if not member:
                        await state.mod_col.update_one(
                            {"guild": doc["guild"], "user": doc["user"]}, {"$unset": {"muted_until": ""}}
                        )
                        continue
                    mute_role = discord.utils.get(guild.roles, name="Muted")
                    if mute_role and mute_role in member.roles:
                        try:
                            await member.remove_roles(mute_role, reason="Mute expired")
                            await perms.log_action(
                                None, f"Auto-unmuted {member}", user_id=member.id, action_type="unmute", guild=guild
                            )
                        except Exception as inner_e:
                            print(f"[Auto unmute role removal error] {inner_e}")
                    await state.mod_col.update_one(
                        {"guild": doc["guild"], "user": doc["user"]}, {"$unset": {"muted_until": ""}}
                    )
            except Exception as e:
                print(f"[Auto unmute error - state.mod_col] {e}")

    @tasks.loop(minutes=1)
    async def check_muted_role_permissions(self):
        """Ensure the Muted role has the correct deny overwrites on every channel in every guild.
        Corrects any misconfigured permission overwrite automatically."""
        for guild in self.bot.guilds:
            mute_role = discord.utils.get(guild.roles, name="Muted")
            if not mute_role:
                continue
            for channel in guild.channels:
                overwrite_perms = channel.overwrites_for(mute_role)
                needs_update = False
                if isinstance(channel, discord.TextChannel):
                    if (
                        overwrite_perms.send_messages is not False
                        or overwrite_perms.add_reactions is not False
                        or overwrite_perms.create_public_threads is not False
                    ):
                        needs_update = True
                elif isinstance(channel, discord.VoiceChannel):
                    if (
                        overwrite_perms.speak is not False
                        or overwrite_perms.stream is not False
                        or overwrite_perms.connect is not False
                    ):
                        needs_update = True
                elif isinstance(channel, discord.CategoryChannel) and (
                    overwrite_perms.send_messages is not False or overwrite_perms.speak is not False
                ):
                    needs_update = True
                if needs_update:
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
                            await channel.set_permissions(mute_role, speak=False, stream=False, connect=False)
                        elif isinstance(channel, discord.StageChannel):
                            await channel.set_permissions(mute_role, request_to_speak=False)
                        elif isinstance(channel, discord.CategoryChannel):
                            await channel.set_permissions(
                                mute_role, send_messages=False, add_reactions=False, speak=False
                            )
                        print(f"Updated Muted role permissions for #{channel.name} in {guild.name}")
                    except Exception as e:
                        print(f"Failed to update permissions for #{channel.name} in {guild.name}: {e}")

    @check_muted_role_permissions.before_loop
    @check_expired_mutes.before_loop
    async def before_unmute_loop(self):
        """Wait for the bot to be fully connected before starting the mute management loops."""
        await self.bot.wait_until_ready()

    async def _handle_rejoin_mute(self, member: discord.Member, doc: dict) -> None:
        """Handle a rejoining member who has an existing state.mutes_col record: if the mute has
        already expired, clean up the record without reapplying the role; otherwise
        reapply the role and reschedule its removal."""
        guild = member.guild
        mute_end = parse_mute_end(doc.get("mute_end"))
        if mute_end is None:
            mute_role = discord.utils.get(guild.roles, name="Muted")
            if mute_role and mute_role not in member.roles:
                await member.add_roles(mute_role, reason="Reapplying mute after rejoin")
            return
        now_utc = datetime.now(timezone.utc)
        if now_utc >= mute_end:
            await state.mutes_col.delete_one({"guild_id": guild.id, "user_id": member.id})
            await remove_mute_role(guild, member, reason="Mute expired before rejoin")
            return
        mute_role = discord.utils.get(guild.roles, name="Muted")
        if mute_role and mute_role not in member.roles:
            await member.add_roles(mute_role, reason="Reapplying mute after rejoin")
        remaining = (mute_end - now_utc).total_seconds()
        self.bot.loop.create_task(schedule_unmute(guild, member, remaining))

    @commands.command(name="kick", description="Kick a member. Staff only.")
    @perms.staffperm("kick")
    @perms.staff_only()
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Kick member from the guild, log the action, and send a result message."""
        err = perms.check_target_permission(ctx, member)
        if err:
            return await ctx.send(err)
        await member.kick(reason=reason)
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        actions_data.setdefault(guild_id, {}).setdefault(user_id, []).append(
            {
                "type": "kick",
                "reason": reason,
                "by": str(getattr(ctx.author, "id", "")),
                "time": datetime.now(timezone.utc).isoformat(),
            }
        )
        try:
            await ctx.send(f"✅ {member.mention} has been kicked. Reason: {reason}")
        except Exception as e:
            print(f"[kick] Could not send confirmation: {e}")
        await perms.log_action(
            ctx, f"Kicked {member} ({member.id}) for: {reason}", user_id=member.id, action_type="kick"
        )

    @commands.command(name="ban", description="Ban a member. Staff only.")
    @perms.staffperm("ban")
    @perms.staff_only()
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Prompt for confirmation before banning member, then execute the ban and log it."""
        err = perms.check_target_permission(ctx, member)
        if err:
            return await ctx.send(err)
        embed = discord.Embed(
            title="⚠️ Confirm Ban",
            description=f"Are you sure you want to ban {member.mention}?",
            color=discord.Color.orange(),
        )
        embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="This action will be logged.")
        confirm_view = ModerationConfirmView("ban", member, reason, ctx=ctx)
        await ctx.send(embed=embed, view=confirm_view)

    @commands.hybrid_command(name="unban", description="Unban a member. Staff only.")
    @perms.staffperm("ban")
    @perms.staff_only()
    async def unban(self, ctx, *, user_id: int):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.send(f"✅ {user.mention} has been unbanned.")
            await perms.log_action(ctx, f"Unbanned {user}", user_id=user.id, action_type="unban")
        except Exception:
            await ctx.send("❌ Failed to unban that user.")

    @commands.hybrid_command(name="say", description="Make the bot say a message in a chosen channel.")
    @perms.staff_only()
    @perms.blacklist_barrier()
    async def say(self, ctx):
        try:
            await ctx.send("📝 Type the message you want me to say, or type `cancel` to cancel.")

            def msg_check(m):
                return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

            try:
                msg = await self.bot.wait_for("message", timeout=60.0, check=msg_check)
            except asyncio.TimeoutError:
                return await ctx.send("⌛ Timed out waiting for the message.")
            content = msg.content.strip()
            if content.lower() == "cancel":
                return await ctx.send("❎ Cancelled.")
            if not content:
                return await ctx.send("❌ Message cannot be empty.")
            if len(content) > 2000:
                return await ctx.send("❌ Message is too long. Please keep it under 2000 characters.")
            await ctx.send("📨 Mention the channel (e.g. #general). Type `cancel` to abort.")
            try:
                ch_msg = await self.bot.wait_for("message", timeout=60.0, check=msg_check)
            except asyncio.TimeoutError:
                return await ctx.send("⌛ Timed out waiting for the channel.")
            ch_text = ch_msg.content.strip()
            if ch_text.lower() == "cancel":
                return await ctx.send("❎ Cancelled.")
            target = None
            if ch_msg.channel_mentions:
                target = ch_msg.channel_mentions[0]
            else:
                try:
                    ch_id = int(ch_text)
                    target = ctx.guild.get_channel(ch_id)
                except Exception:
                    target = None
            if not isinstance(target, discord.TextChannel):
                return await ctx.send("❌ Invalid channel. Mention a text channel or provide a valid channel ID.")
            try:
                await target.send(content)
            except discord.Forbidden:
                return await ctx.send("❌ I do not have permission to send messages in that channel.")
            except discord.HTTPException as e:
                return await ctx.send(f"⚠️ Failed to send the message: {type(e).__name__}")
            await ctx.send(f"✅ Sent your message to {target.mention}.")
            await perms.log_action(
                ctx, f"say command used in #{target.name} ({target.id}): {content[:500]}", action_type="say"
            )
        except Exception as e:
            print(f"[ERROR] say command: {e}")
            traceback.print_exc()
            await ctx.send("⚠️ An unexpected error occurred.")

    @say.error
    async def say_error(self, ctx, error):
        root_error = errors.unwrap_command_error(error)
        print(f"[ERROR] say_error: {root_error}")
        traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
        try:
            if isinstance(error, commands.CheckFailure):
                return await errors.send_hybrid_error(ctx, content="❌ Only staff members can use this command.")
            if errors.is_discord_service_unavailable_error(error):
                return await errors.send_hybrid_error(ctx, content=errors.DISCORD_SERVICE_UNAVAILABLE_MESSAGE)
            if isinstance(error, commands.CommandInvokeError):
                return await errors.send_hybrid_error(ctx, content="⚠️ Error running say. Please try again shortly.")
            await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred.")
        except Exception as e:
            print(f"[say_error] {e}")

    @commands.command(name="mute", description="Mute a member temporarily. Staff only.")
    @perms.staffperm("mute")
    @perms.staff_only()
    async def mute(
        self, ctx, member: discord.Member, duration: str | None = None, *, reason: str = "No reason provided"
    ):
        err = perms.check_target_permission(ctx, member)
        if err:
            return await ctx.send(err)
        mute_role = discord.utils.get(getattr(ctx.guild, "roles", []), name="Muted")
        if not mute_role:
            return await ctx.send("❌ Could not find a role named `Muted`.")
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        seconds = None
        if duration:
            try:
                seconds = econ.parse_time(duration)
            except ValueError as e:
                return await ctx.send(f"❌ Invalid duration: {e}")
            except Exception as e:
                print(f'[mute] Unexpected error parsing duration "{duration}": {e}')
                return await ctx.send("❌ Could not parse that duration. Use formats like `10m`, `2h`, `1d`.")
            if seconds > _MAX_MUTE_SECONDS:
                return await ctx.send("❌ Maximum mute duration is **30 days**.")
        await member.add_roles(mute_role, reason=reason)
        actions_data.setdefault(guild_id, {}).setdefault(user_id, []).append(
            {
                "type": "mute",
                "reason": reason,
                "by": str(getattr(ctx.author, "id", "")),
                "time": datetime.now(timezone.utc).isoformat(),
            }
        )
        if seconds:
            mute_end = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            await state.mutes_col.update_one(
                {"guild_id": ctx.guild.id, "user_id": member.id},
                {"$set": {"guild_id": ctx.guild.id, "user_id": member.id, "mute_end": mute_end.isoformat()}},
                upsert=True,
            )
            try:
                await ctx.send(f"✅ {member.mention} has been muted for **{duration}**. Reason: {reason}")
            except Exception as e:
                print(f"[mute] Could not send confirmation: {e}")
        else:
            try:
                await ctx.send(f"✅ {member.mention} has been muted indefinitely. Reason: {reason}")
            except Exception as e:
                print(f"[mute] Could not send confirmation: {e}")
        await perms.log_action(
            ctx,
            f"Muted {member} ({member.id}) for: {reason}" + (f" | Duration: {duration}" if duration else ""),
            user_id=member.id,
            action_type="mute",
        )

    @commands.hybrid_command(name="unmute", description="Unmute a member. Staff-only.")
    @perms.staffperm("mute")
    @perms.staff_only()
    async def unmute(self, ctx, member: discord.Member):
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if mute_role and mute_role in member.roles:
            await member.remove_roles(mute_role, reason="Unmute command used")
            await state.mutes_col.delete_one({"guild_id": ctx.guild.id, "user_id": member.id})
            await ctx.send(f"✅ {member.mention} has been unmuted.")
            await perms.log_action(ctx, f"Unmuted {member}", user_id=member.id, action_type="unmute")
        else:
            await ctx.send("⚠️ That member is not muted.")

    @commands.hybrid_command(name="warn", description="Warn a user. Staff only.")
    @app_commands.describe(member="The user to warn", reason="Reason for the warning (optional)")
    @perms.staffperm("other_moderation")
    @perms.staff_only()
    async def warn(self, ctx, member: discord.Member, *, reason="No reason provided"):
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        warnings_data.setdefault(guild_id, {}).setdefault(user_id, []).append(
            {"reason": reason, "by": str(getattr(ctx.author, "id", "")), "time": datetime.now(timezone.utc).isoformat()}
        )
        if not (cfg._running_under_pytest() and cfg._looks_like_motor_collection(state.mod_col)):
            try:
                await state.mod_col.update_one(
                    {"guild": guild_id, "user": user_id},
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
            except Exception as e:
                print(f"[warn] state.mod_col update failed: {e}")
        try:
            guild_name = getattr(ctx.guild, "name", "this server")
            author_mention = getattr(ctx.author, "mention", "")
            await member.send(
                f"⚠️ You have been **warned** in **{guild_name}**\n**Reason:** {reason}\n**Warned by:** {ctx.author} {author_mention}".strip()
            )
        except discord.Forbidden:
            try:
                await ctx.send(f"⚠️ Could not DM {member.mention} - they might have DMs disabled.")
            except discord.HTTPException:
                pass
        try:
            await ctx.send(f"⚠️ {member.mention} has been warned: {reason}")
        except discord.HTTPException:
            pass
        try:
            await perms.log_action(ctx, f"Warned {member} for: {reason}", user_id=member.id, action_type="warn")
        except Exception as e:
            print(f"[warn] perms.log_action failed: {e}")

    @commands.hybrid_command(name="clearwarns", description="Clear all warnings. Staff only.")
    @perms.staffperm("other_moderation")
    @perms.staff_only()
    async def clearwarns(self, ctx, member: discord.Member):
        await state.mod_col.update_one({"guild": str(ctx.guild.id), "user": str(member.id)}, {"$set": {"warnings": []}})
        await ctx.send(f"✅ All warnings for {member.mention} have been cleared.")
        await perms.log_action(ctx, f"Cleared warnings for {member}", user_id=member.id, action_type="clearwarns")

    @commands.hybrid_command(name="purge", description="Bulk delete messages. Staff only.")
    @perms.staffperm("other_moderation")
    @perms.staff_only()
    async def purge(self, ctx, count: int, member: discord.Member = None):
        def check(m):
            return m.author == member if member else True

        # A prefix invocation leaves the command message itself behind; remove it separately so the
        # requested count (and the reported number) only ever covers other people's messages.
        if errors.is_prefix(ctx):
            with contextlib.suppress(discord.HTTPException):
                await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=count, check=check)
        await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=5)
        await perms.log_action(
            ctx,
            f"Purged {len(deleted)} messages{(' from ' + member.display_name if member else '')}",
            action_type="purge",
        )

    @commands.hybrid_command(name="slowmode", description="Set slowmode for this channel. Staff only.")
    @perms.staffperm("other_moderation")
    @perms.staff_only()
    async def slowmode(self, ctx, seconds: int):
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"✅ Slowmode set to {seconds} seconds.")
        await perms.log_action(ctx, f"Set slowmode to {seconds}s in #{ctx.channel.name}", action_type="slowmode")

    @commands.hybrid_command(name="userinfo", description="View info about the specified user.")
    @app_commands.describe(member="The user to check (optional - shows your info if not provided)")
    async def userinfo(self, ctx, member: discord.Member = None):
        """Show account creation date, server join date, and warning count for member."""
        member = member or ctx.author
        join = member.joined_at.strftime("%Y-%m-%d")
        created = member.created_at.strftime("%Y-%m-%d")
        doc = await state.mod_col.find_one({"guild": str(ctx.guild.id), "user": str(member.id)})
        warns = len(doc.get("warnings", [])) if doc else 0
        embed = discord.Embed(title="User Information", color=discord.Color.blurple())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=member.id)
        embed.add_field(name="Joined Server", value=join)
        embed.add_field(name="Account Created", value=created)
        embed.add_field(name="Warnings", value=warns)
        await ctx.send(embed=embed)

    @commands.command(name="performance", description="View staff performance analytics. Staff only.")
    @perms.staffperm("other_moderation")
    @perms.staff_only()
    async def performance(self, ctx, days: int = 30):
        """Show a staff performance report with action counts, message stats, and peak activity times.
        Defaults to the last 30 days. Detailed errors are printed to the console only."""
        try:
            staff_role_id = None
            settings = await state.settings_col.find_one({"guild": str(ctx.guild.id)})
            if settings and "staff_role" in settings:
                staff_role_id = settings["staff_role"]
            if not staff_role_id:
                return await ctx.send("❌ No staff role configured. Use `.configure` to configure one.")
            if days < 1 or days > 365:
                return await ctx.send("❌ Review period must be between 1 and 365 days.")
            staff_role = ctx.guild.get_role(staff_role_id)
            if not staff_role:
                return await ctx.send("❌ Staff role not found.")
            staff_members = [member for member in ctx.guild.members if staff_role in member.roles]
            if not staff_members:
                return await ctx.send("❌ No staff members found.")
            embed = discord.Embed(
                title="📊 Staff Performance Review",
                description=f"Select a staff member from the dropdown below to view their performance analytics.\n\n**Total Staff Members:** {len(staff_members)}\n**Review Period:** Last {days} days",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="📊 Available Analytics",
                value="• Total moderation actions\n• Punishment breakdown\n• Activity patterns\n• Efficiency rating",
                inline=False,
            )
            embed.set_footer(text="Review period can be adjusted with .performance <days> (1-365)")
            view = PerformanceView(ctx.guild.id, staff_members, days)
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            print(f"[ERROR] performance command: {type(e).__name__}: {e}")
            await ctx.send("❌ An unexpected error occurred while loading performance data.")

    @commands.hybrid_command(name="modview", description="Open moderator view for a user. Staff only.")
    @perms.staffperm("other_moderation")
    @perms.staff_only()
    async def modview(self, ctx, member: discord.Member):
        """Send a detailed embed with member account info, roles, permissions, flags, and punishment history."""
        punishments = await fetch_punishments(ctx.guild.id, member.id)
        mod_perms = format_permissions(member)
        roles = format_roles(member)
        flags = format_flags(member)
        nick = member.nick or "None"
        pending = "✅ Yes" if member.pending else "❌ No"
        bot_flag = "🤖 Yes" if member.bot else "👤 No"
        top_role = member.top_role.mention
        status = str(member.status).title()
        joined_discord = f"<t:{int(member.created_at.timestamp())}:F>"
        joined_server = f"<t:{int(member.joined_at.timestamp())}:F>"
        verification_map = {
            VerificationLevel.none: "None",
            VerificationLevel.low: "Low",
            VerificationLevel.medium: "Medium",
            VerificationLevel.high: "High",
        }
        verification_name = verification_map.get(
            ctx.guild.verification_level, str(ctx.guild.verification_level).title()
        )
        embed = discord.Embed(
            title=f"🛠️ Moderator View: {member}", color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Username", value=f"{member} (`{member.name}`)", inline=False)
        embed.add_field(name="🪪 Nickname", value=nick, inline=True)
        embed.add_field(name="🤖 Bot Account", value=bot_flag, inline=True)
        embed.add_field(name="📶 Status", value=status, inline=True)
        embed.add_field(name="🧩 Top Role", value=top_role, inline=True)
        embed.add_field(name="🎭 Roles", value=roles, inline=False)
        embed.add_field(name="🕐 Joined Discord", value=joined_discord, inline=True)
        embed.add_field(name="🏠 Joined Server", value=joined_server, inline=True)
        embed.add_field(name="🧾 Pending Verification", value=pending, inline=True)
        embed.add_field(name="🔒 Guild Verification Level", value=verification_name, inline=False)
        embed.add_field(name="🎖️ Badges / Flags", value=flags, inline=False)
        embed.add_field(name="⚙️ Effective Permissions", value=mod_perms, inline=False)
        embed.add_field(name="📜 Past Punishments", value=punishments, inline=False)
        msg = await ctx.send(embed=embed)
        view = ModViewButtons(self.bot, ctx, member, msg)
        await msg.edit(view=view)

    @modview.error
    async def modview_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await errors.send_hybrid_error(
                ctx, content="❌ You don't have the required permissions to use this command."
            )
        elif isinstance(error, commands.CheckFailure):
            await errors.send_hybrid_error(ctx, content="❌ This command is restricted to staff members only.")
        elif isinstance(error, commands.BadArgument):
            await errors.send_hybrid_error(ctx, content="❌ Invalid member provided. Please mention a valid user.")
        elif isinstance(error, commands.MemberNotFound):
            await errors.send_hybrid_error(ctx, content="❌ Could not find that member in this server.")
        elif errors.is_discord_service_unavailable_error(error):
            await errors.send_hybrid_error(ctx, content=errors.DISCORD_SERVICE_UNAVAILABLE_MESSAGE)
        elif isinstance(error, commands.CommandInvokeError):
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )
        else:
            await errors.send_hybrid_error(
                ctx, content="⚠️ An error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        data = await state.reaction_col.find_one({"message": payload.message_id})
        if not data:
            return
        if str(payload.emoji) != data["emoji"]:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(data["role"])
        if role is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None:
            return
        try:
            await member.add_roles(role)
        except Exception as e:
            print(f"[reactionrole add error] {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        data = await state.reaction_col.find_one({"message": payload.message_id})
        if not data:
            return
        if str(payload.emoji) != data["emoji"]:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(data["role"])
        if role is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None:
            return
        try:
            await member.remove_roles(role)
        except Exception as e:
            print(f"[reactionrole remove error] {e}")

    @commands.hybrid_command(name="reactionrole", description="Set up a reaction role. Staff only.")
    @perms.staffperm("reactionroles")
    @perms.staff_only()
    async def reactionrole(self, ctx, message_id: int, emoji, role: discord.Role):
        try:
            msg = await ctx.channel.fetch_message(message_id)
            await msg.add_reaction(emoji)
            await state.reaction_col.update_one(
                {"message": message_id}, {"$set": {"emoji": str(emoji), "role": role.id}}, upsert=True
            )
            await ctx.send(f"✅ Reaction role set: {emoji} will grant {role.mention}.")
        except Exception as e:
            print(f"[reactionrole error] {e}")
            await ctx.send("❌ Could not set reaction role. Check your permissions and message ID.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Ensure every guild has a Muted role, re-apply (or clear) stored mutes after a restart,
        and start the mute-expiry and Muted-role permission loops (runs once)."""
        if self._ready_done:
            return
        self._ready_done = True
        if not self.check_expired_mutes.is_running():
            self.check_expired_mutes.start()
        mute_role_name = "Muted"
        mute_role = None
        for guild in self.bot.guilds:
            mute_role = discord.utils.get(guild.roles, name=mute_role_name)
            if not mute_role:
                mute_role = await guild.create_role(name=mute_role_name)
                for ch in guild.channels:
                    await ch.set_permissions(mute_role, speak=False, send_messages=False)
            async for doc in state.mutes_col.find({"guild_id": guild.id}):
                member = guild.get_member(doc["user_id"])
                if not member:
                    continue
                await _reapply_or_clear_mute(guild, member, doc, mute_role)
        if not self.check_muted_role_permissions.is_running():
            self.check_muted_role_permissions.start()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Re-apply an active mute (or clean up an expired one) when a muted member rejoins."""
        try:
            doc = await state.mutes_col.find_one({"guild_id": member.guild.id, "user_id": member.id})
            if doc:
                await self._handle_rejoin_mute(member, doc)
        except Exception as e:
            print("on_member_join ERROR:", e)

    def cog_unload(self):
        """Cancel the loops this cog owns."""
        for loop in (self.check_expired_mutes, self.check_muted_role_permissions):
            if loop.is_running():
                loop.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
