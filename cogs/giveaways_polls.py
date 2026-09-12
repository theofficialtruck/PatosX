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

"""Giveaways, polls, claimable roles, money drops, addmoney/removemoney, and the reminder loop."""

import asyncio
import contextlib
import io
import random
import re
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import discord
from discord import (
    app_commands,
)
from discord.ext import commands, tasks
from discord.ui import Button, View

from core import config as cfg
from core import economyHelperFuncs as econ
from core import errors, state
from core import permsHelperFuncs as perms
from core import xpHelperFuncs as xp


class DropClaimView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="drop_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = str(interaction.message.id)
        doc = await state.drop_instances_col.find_one({"message_id": msg_id})
        if not doc:
            await interaction.response.send_message("⚠️ This drop is no longer valid.", ephemeral=True)
            return
        if str(doc.get("author_id")) == str(interaction.user.id):
            await interaction.response.send_message("❌ You can't claim your own drop.", ephemeral=True)
            return
        if doc.get("claimed"):
            await interaction.response.send_message("⚠️ This drop has already been claimed.", ephemeral=True)
            return
        amount = int(doc.get("amount", 0))
        # Atomic claim: only the click that flips claimed False -> True gets paid, so two people
        # pressing the button at the same instant can't both receive the coins.
        claimed_doc = await state.drop_instances_col.find_one_and_update(
            {"message_id": msg_id, "claimed": False},
            {
                "$set": {
                    "claimed": True,
                    "claimer_id": str(interaction.user.id),
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        if claimed_doc is None:
            await interaction.response.send_message("⚠️ This drop has already been claimed.", ephemeral=True)
            return
        await econ.add_balance(interaction.user.id, interaction.guild.id, amount)
        button.disabled = True
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.description = f"Claimed by {interaction.user.mention} for 🪙 {amount:,}"
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"✅ You claimed 🪙 {amount:,}", ephemeral=True)


# ---------------------------------------------------------------------------
# Giveaways
# ---------------------------------------------------------------------------

# Giveaways currently running in this process, keyed by giveaway id (the giveaway message id as a
# string). Lets the draw command finish a live giveaway through its view - which cancels the
# scheduled end, removes the buttons and announces exactly once - and stops resume_giveaways
# from attaching a second view to a giveaway that is already live.
ACTIVE_GIVEAWAYS: dict[str, "GiveawayView"] = {}


def parse_role_ids(raw: str | None) -> list[int]:
    """Parse a comma/space separated list of role IDs and/or ``<@&id>`` mentions into unique ints,
    keeping the order they were given in."""
    if not raw:
        return []
    ids: list[int] = []
    for token in re.findall(r"\d{15,21}", raw):
        role_id = int(token)
        if role_id not in ids:
            ids.append(role_id)
    return ids


def parse_bonus_roles(raw: str | None) -> dict[str, int]:
    """Parse ``role|bonus, role|bonus`` into ``{"role_id": bonus}``. Keys are strings because MongoDB
    only accepts string keys in embedded documents; malformed entries and bonuses below 1 are skipped."""
    result: dict[str, int] = {}
    if not raw:
        return result
    for entry in raw.split(","):
        if "|" not in entry:
            continue
        role_part, bonus_part = entry.split("|", 1)
        role_ids = parse_role_ids(role_part)
        try:
            bonus = int(bonus_part.strip())
        except ValueError:
            continue
        if not role_ids or bonus < 1:
            continue
        result[str(role_ids[0])] = bonus
    return result


def pick_winners(participants: dict, count: int) -> list[int]:
    """Draw up to ``count`` distinct winners, weighting each participant by their ticket count.
    A drawn participant's tickets are removed before the next draw, so this always terminates -
    even when fewer people entered than there are winners to pick, which made the old
    ``while len(winners) < count and ticket_pool`` loop spin forever."""
    pool: dict[int, int] = {}
    for uid, tickets in (participants or {}).items():
        try:
            uid_int, tickets_int = int(uid), int(tickets)
        except (TypeError, ValueError):
            continue
        if tickets_int > 0:
            pool[uid_int] = tickets_int
    winners: list[int] = []
    while pool and len(winners) < count:
        uids = list(pool)
        pick = random.choices(uids, weights=[pool[uid] for uid in uids], k=1)[0]
        winners.append(pick)
        del pool[pick]
    return winners


def build_giveaway_embed(prize, host_mention, end_time, winners_count, required_roles, bonus_roles):
    """The giveaway announcement embed. Field names matter: the view and the draw/reroll commands
    locate the Ends/Winners/Participants fields by name when they update it."""
    embed = discord.Embed(title=prize, color=discord.Color.blue())
    embed.add_field(name="Hosted by", value=host_mention, inline=False)
    end_ts = int(end_time.timestamp())
    embed.add_field(name="Ends", value=f"<t:{end_ts}:F> (<t:{end_ts}:R>)", inline=False)
    embed.add_field(name="Winners", value=str(winners_count), inline=False)
    embed.add_field(name="Participants", value="0", inline=False)
    if required_roles:
        embed.add_field(
            name="Requirements",
            value="Roles required (one of): " + ", ".join(f"<@&{role_id}>" for role_id in required_roles),
            inline=False,
        )
    if bonus_roles:
        embed.add_field(
            name="Roles with bonus entries",
            value="\n".join(f"<@&{role_id}> • {bonus} bonus entries" for role_id, bonus in bonus_roles.items()),
            inline=False,
        )
    return embed


def apply_result_to_embed(embed: discord.Embed, winners_mentions: str) -> None:
    """Mark a giveaway embed as finished: Ends -> Ended, Winners -> the drawn winners."""
    for idx, field in enumerate(embed.fields):
        if field.name == "Ends":
            embed.set_field_at(
                idx, name="Ended", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=False
            )
        elif field.name == "Winners":
            embed.set_field_at(idx, name="Winners", value=winners_mentions or "No winners", inline=False)


class GiveawayView(discord.ui.View):
    """Entry / participants buttons for one running giveaway. One instance per giveaway per
    process; ``end_giveaway`` is idempotent and is the single place a giveaway gets finished."""

    def __init__(self, embed_message, giveaway_id, end_time, winners, prize, *, required_roles=None, bonus_roles=None):
        super().__init__(timeout=None)
        self.participants: dict[int, int] = defaultdict(int)
        self.embed_message = embed_message
        self.giveaway_id = giveaway_id
        self.end_time = end_time
        self.winners_count = winners
        self.prize = prize
        self.required_roles = [int(role_id) for role_id in (required_roles or [])]
        self.bonus_roles = {int(role_id): int(bonus) for role_id, bonus in (bonus_roles or {}).items()}
        self.end_task: asyncio.Task | None = None
        self.ended = False
        self._end_lock = asyncio.Lock()
        ACTIVE_GIVEAWAYS[giveaway_id] = self

    def schedule_end(self, delay: float) -> asyncio.Task:
        """Finish the giveaway after ``delay`` seconds, replacing any previously scheduled end."""
        if self.end_task is not None and not self.end_task.done():
            self.end_task.cancel()
        self.end_task = asyncio.create_task(end_after_delay(self, delay))
        return self.end_task

    def entries_for(self, member) -> int:
        """1 ticket, plus the bonus of every bonus role the member holds."""
        entries = 1
        member_role_ids = {role.id for role in getattr(member, "roles", [])}
        for role_id, bonus in self.bonus_roles.items():
            if role_id in member_role_ids:
                entries += bonus
        return entries

    def meets_requirements(self, member) -> bool:
        """True when there are no required roles or the member holds at least one of them."""
        if not self.required_roles:
            return True
        member_role_ids = {role.id for role in getattr(member, "roles", [])}
        return any(role_id in member_role_ids for role_id in self.required_roles)

    async def update_db(self):
        await state.giveaway_col.update_one(
            {"_id": self.giveaway_id},
            {"$set": {"participants": {str(uid): entries for uid, entries in self.participants.items()}}},
        )

    async def refresh_participant_count(self):
        try:
            embed = self.embed_message.embeds[0]
        except IndexError:
            return
        for idx, field in enumerate(embed.fields):
            if field.name == "Participants":
                embed.set_field_at(idx, name="Participants", value=str(len(self.participants)), inline=False)
                break
        try:
            await self.embed_message.edit(embed=embed)
        except discord.HTTPException as e:
            print(f"[Giveaway] Could not update the participant count for {self.giveaway_id}: {e}")

    @discord.ui.button(label="🎉 Entry", style=discord.ButtonStyle.blurple)
    async def entry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ended:
            await interaction.response.send_message("⏰ This giveaway has already ended.", ephemeral=True)
            return
        user = interaction.user
        if user.id in self.participants:
            del self.participants[user.id]
            await interaction.response.send_message("❌ You left the giveaway.", ephemeral=True)
        else:
            if not self.meets_requirements(user):
                needed = ", ".join(f"<@&{role_id}>" for role_id in self.required_roles)
                await interaction.response.send_message(
                    f"🚫 You need one of these roles to enter: {needed}", ephemeral=True
                )
                return
            entries = self.entries_for(user)
            self.participants[user.id] = entries
            await interaction.response.send_message(
                f"✅ You joined the giveaway with **{entries} ticket(s)**!", ephemeral=True
            )
        await self.update_db()
        await self.refresh_participant_count()

    @discord.ui.button(label="👥 Participants", style=discord.ButtonStyle.gray)
    async def participants_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.participants:
            await interaction.response.send_message("👀 Nobody yet! Be the first to join!", ephemeral=True)
            return
        # Resolving many users can take longer than the 3 second interaction window
        await interaction.response.defer(ephemeral=True, thinking=True)
        names = []
        for uid, entries in self.participants.items():
            user = interaction.client.get_user(uid)
            if user is None:
                try:
                    user = await interaction.client.fetch_user(uid)
                except discord.HTTPException:
                    user = None
            label = str(user) if user else f"Unknown user ({uid})"
            names.append(f"{label} - {entries} ticket(s)")
        names_str = "\n".join(names)
        if len(names_str) > 1900:
            file = discord.File(io.BytesIO(names_str.encode()), filename="participants.txt")
            await interaction.followup.send(
                "📄 Too many participants to display! Here's the list:", file=file, ephemeral=True
            )
        else:
            await interaction.followup.send(f"**Participants:**\n{names_str}", ephemeral=True)

    def _mark_ended(self) -> None:
        self.ended = True
        ACTIVE_GIVEAWAYS.pop(self.giveaway_id, None)
        if self.end_task is not None and not self.end_task.done() and self.end_task is not asyncio.current_task():
            self.end_task.cancel()

    async def end_giveaway(self) -> list[int] | None:
        """Finish the giveaway: pick the winners, persist the result, update the embed and announce.
        Safe to call more than once (later calls are no-ops), safe if the message was deleted, and
        it checks the database first so a giveaway ended elsewhere is never announced twice.
        Returns the winner IDs, or None when the giveaway had already been ended."""
        async with self._end_lock:
            if self.ended:
                return None
            data = await state.giveaway_col.find_one({"_id": self.giveaway_id}) or {}
            if data.get("ended"):
                self._mark_ended()
                self.stop()
                return None
            self._mark_ended()
            if isinstance(self.participants, list):
                self.participants = {}
            winners = pick_winners(self.participants, self.winners_count)
            # Persist first: whatever fails below, the giveaway must never be re-run after a restart.
            await state.giveaway_col.update_one(
                {"_id": self.giveaway_id},
                {"$set": {"ended": True, "winners": winners, "ended_at": datetime.now(timezone.utc).isoformat()}},
            )
            winners_mentions = ", ".join(f"<@{uid}>" for uid in winners)
            try:
                embed = self.embed_message.embeds[0]
                apply_result_to_embed(embed, winners_mentions)
                await self.embed_message.edit(embed=embed, view=None)
            except (IndexError, discord.HTTPException) as e:
                print(f"[Giveaway] Could not update the giveaway message for {self.giveaway_id}: {e}")
            try:
                channel = self.embed_message.channel
                if winners:
                    await channel.send(f"🎉 Congratulations {winners_mentions}! You won **{self.prize}**!")
                else:
                    await channel.send(f"😔 No one joined the giveaway for **{self.prize}**. No winners this time!")
            except discord.HTTPException as e:
                print(f"[Giveaway] Could not announce the result for {self.giveaway_id}: {e}")
            self.stop()
            return winners


def build_poll_embed(question, options, counts, closed=False, duration=None):
    embed = discord.Embed(title="📊 Poll" + (" (Closed)" if closed else ""), color=discord.Color.blue())
    embed.description = f"**{question}**"
    for i, opt in enumerate(options, start=1):
        if opt:
            embed.add_field(name=opt, value=f"Votes: {counts.get(str(i), 0)}", inline=False)
    if duration and (not closed):
        embed.set_footer(text=f"Poll duration: {duration}")
    return embed


class PollView(discord.ui.View):
    def __init__(self, poll_id, options):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.options = options
        for i, opt in enumerate(options, start=1):
            if opt:
                self.add_item(PollButton(label=opt, option=i, poll_id=poll_id))
        self.add_item(RemoveVoteButton(poll_id=poll_id))


class PollButton(discord.ui.Button):
    def __init__(self, label, option, poll_id):
        super().__init__(style=discord.ButtonStyle.primary, label=label)
        self.option = option
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction):
        poll = await state.polls_col.find_one({"poll_id": self.poll_id})
        if not poll:
            return await interaction.response.send_message("⚠️ Poll not found.", ephemeral=True)
        poll["votes"][str(interaction.user.id)] = str(self.option)
        await state.polls_col.update_one({"poll_id": self.poll_id}, {"$set": {"votes": poll["votes"]}})
        counts = {}
        for v in poll["votes"].values():
            counts[v] = counts.get(v, 0) + 1
        embed = build_poll_embed(poll["question"], poll["options"], counts, closed=False, duration=poll["duration_raw"])
        await interaction.message.edit(embed=embed, view=self.view)
        await interaction.response.send_message(f"✅ You voted for **{self.label}**", ephemeral=True)


class RemoveVoteButton(discord.ui.Button):
    def __init__(self, poll_id):
        super().__init__(style=discord.ButtonStyle.danger, label="Remove Vote")
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction):
        poll = await state.polls_col.find_one({"poll_id": self.poll_id})
        if not poll:
            return await interaction.response.send_message("⚠️ Poll not found.", ephemeral=True)
        if str(interaction.user.id) in poll["votes"]:
            del poll["votes"][str(interaction.user.id)]
            await state.polls_col.update_one({"poll_id": self.poll_id}, {"$set": {"votes": poll["votes"]}})
            counts = {}
            for v in poll["votes"].values():
                counts[v] = counts.get(v, 0) + 1
            embed = build_poll_embed(
                poll["question"], poll["options"], counts, closed=False, duration=poll["duration_raw"]
            )
            await interaction.message.edit(embed=embed, view=self.view)
            await interaction.response.send_message("🗑️ Your vote was removed.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ You haven't voted yet.", ephemeral=True)


class PollModal(discord.ui.Modal, title="Create a Poll"):
    question = discord.ui.TextInput(label="Question?", required=True)
    option1 = discord.ui.TextInput(label="Option 1", required=True)
    option2 = discord.ui.TextInput(label="Option 2", required=True)
    option3 = discord.ui.TextInput(label="Option 3 (optional)", required=False)
    option4 = discord.ui.TextInput(label="Option 4 (optional)", required=False)
    option5 = discord.ui.TextInput(label="Option 5 (optional)", required=False)
    channel = discord.ui.TextInput(label="Channel ID to post in?", required=True)
    duration = discord.ui.TextInput(label="Duration? (e.g. 10m, 2h)", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration_seconds = econ.parse_time(self.duration.value)
            if duration_seconds <= 0:
                raise ValueError("Duration must be longer than zero seconds.")
            try:
                channel_id = int(self.channel.value.strip().strip("<#>"))
            except ValueError:
                raise ValueError("The channel must be a channel ID (or a #channel mention).") from None
            post_channel = interaction.client.get_channel(channel_id)
            if post_channel is None or getattr(post_channel, "guild", None) != interaction.guild:
                raise ValueError("That channel doesn't exist in this server.")
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            options = [
                self.option1.value,
                self.option2.value,
                self.option3.value,
                self.option4.value,
                self.option5.value,
            ]
            poll_id = str(interaction.id)
            counts = {}
            view = PollView(poll_id, options)
            embed = build_poll_embed(self.question.value, options, counts, closed=False, duration=self.duration.value)
            msg = await post_channel.send(embed=embed, view=view)
            await state.polls_col.insert_one(
                {
                    "poll_id": poll_id,
                    "question": self.question.value,
                    "options": options,
                    "votes": {},
                    "channel_id": str(post_channel.id),
                    "message_id": str(msg.id),
                    "end_time": end_time,
                    "duration_raw": self.duration.value,
                }
            )
            await interaction.followup.send("✅ Poll created!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Error: {e}", ephemeral=True)


class GiveawayModal(discord.ui.Modal, title="Create Giveaway"):
    # Discord rejects labels longer than 45 characters, so keep these short.
    prize = discord.ui.TextInput(label="Prize", placeholder="What's the giveaway prize?", required=True, max_length=256)
    winners = discord.ui.TextInput(label="Number of winners", placeholder="Example: 3", required=True, max_length=4)
    duration = discord.ui.TextInput(
        label="Duration", placeholder="e.g. 30s, 15m, 2h, 3d, 1w, 2mo, 1y, or combinations like 1d12h", required=True
    )
    role_requirements = discord.ui.TextInput(
        label="Role Requirements (optional)",
        placeholder="Role IDs separated by commas (modals can't autocomplete @mentions)",
        required=False,
    )
    bonus_roles = discord.ui.TextInput(
        label="Bonus Roles (optional)", placeholder="Format: role_id|bonus, role_id|bonus", required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            winners_count = int(self.winners.value.strip())
            if winners_count < 1:
                raise ValueError("Number of winners must be at least 1.")
            duration_seconds = econ.parse_time(self.duration.value)
            if duration_seconds <= 0:
                raise ValueError("Duration must be longer than zero seconds.")
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        required_roles = parse_role_ids(self.role_requirements.value)
        if self.role_requirements.value.strip() and not required_roles:
            await interaction.response.send_message(
                "❌ Couldn't read any role IDs from the role requirements. "
                "Give the role IDs, separated by commas.",
                ephemeral=True,
            )
            return
        bonus_roles = parse_bonus_roles(self.bonus_roles.value)
        if self.bonus_roles.value.strip() and not bonus_roles:
            await interaction.response.send_message(
                "❌ Couldn't read any bonus roles. Use the format `role_id|bonus, role_id|bonus` "
                "(the bonus must be 1 or more).",
                ephemeral=True,
            )
            return
        guild = interaction.guild
        unknown_roles = [
            role_id
            for role_id in required_roles + [int(role_id) for role_id in bonus_roles]
            if guild.get_role(role_id) is None
        ]
        if unknown_roles:
            await interaction.response.send_message(
                "❌ These roles don't exist in this server: " + ", ".join(str(role_id) for role_id in unknown_roles),
                ephemeral=True,
            )
            return
        # Everything below can take longer than Discord's 3 second interaction window
        await interaction.response.defer(ephemeral=True, thinking=True)
        now = datetime.now(timezone.utc)
        end_time = now + timedelta(seconds=duration_seconds)
        embed = build_giveaway_embed(
            self.prize.value, interaction.user.mention, end_time, winners_count, required_roles, bonus_roles
        )
        sent_message = await interaction.channel.send(embed=embed)
        giveaway_id = str(sent_message.id)
        giveaway_data = {
            "_id": giveaway_id,
            "guild_id": guild.id,
            "channel_id": interaction.channel.id,
            "message_id": sent_message.id,
            "prize": self.prize.value,
            "host_id": interaction.user.id,
            "created_at": now.isoformat(),
            "end_time": end_time.isoformat(),
            "winners_count": winners_count,
            "participants": {},
            "ended": False,
            "winners": [],
            "required_roles": required_roles,
            "bonus_roles": bonus_roles,
        }
        try:
            await state.giveaway_col.insert_one(giveaway_data)
        except Exception as e:
            print(f"[Giveaway] Failed to save giveaway {giveaway_id}: {e}")
            with contextlib.suppress(discord.HTTPException):
                await sent_message.delete()
            await interaction.followup.send(
                "⚠️ The giveaway could not be saved to the database, so it was not started.", ephemeral=True
            )
            return
        view = GiveawayView(
            embed_message=sent_message,
            giveaway_id=giveaway_id,
            end_time=end_time,
            winners=winners_count,
            prize=self.prize.value,
            required_roles=required_roles,
            bonus_roles=bonus_roles,
        )
        await sent_message.edit(view=view)
        view.schedule_end(duration_seconds)
        await interaction.followup.send("✅ Giveaway created!", ephemeral=True)


async def resume_giveaways(bot):
    """Re-attach views to every unfinished giveaway after a restart, ending the ones that expired
    while the bot was offline. A giveaway whose message no longer exists is closed out so it is
    not retried on every start."""
    now = datetime.now(timezone.utc)
    async for data in state.giveaway_col.find({"ended": False}):
        giveaway_id = data["_id"]
        if giveaway_id in ACTIVE_GIVEAWAYS:
            continue
        try:
            end_time = datetime.fromisoformat(data["end_time"])
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            remaining = (end_time - now).total_seconds()
            channel = bot.get_channel(int(data["channel_id"]))
            if not channel:
                print(f"[Giveaway Resume] Channel {data['channel_id']} not found for giveaway {giveaway_id}")
                continue
            try:
                message = await channel.fetch_message(int(data["message_id"]))
            except discord.NotFound:
                await state.giveaway_col.update_one(
                    {"_id": giveaway_id}, {"$set": {"ended": True, "winners": [], "ended_reason": "message deleted"}}
                )
                print(f"[Giveaway Resume] The message for giveaway {giveaway_id} was deleted; marked as ended.")
                continue
            view = GiveawayView(
                embed_message=message,
                giveaway_id=giveaway_id,
                end_time=end_time,
                winners=data["winners_count"],
                prize=data["prize"],
                required_roles=data.get("required_roles"),
                bonus_roles=data.get("bonus_roles"),
            )
            participants = data.get("participants", {})
            if isinstance(participants, list):
                participants = {}
            for uid, entries in participants.items():
                try:
                    view.participants[int(uid)] = int(entries)
                except (TypeError, ValueError) as e:
                    print(f"[Giveaway Resume] Skipping bad participant entry {uid!r} for giveaway {giveaway_id}: {e}")
            await message.edit(view=view)
            if remaining <= 0:
                print(f"[Giveaway Resume] Giveaway {giveaway_id} expired while offline. Ending now.")
                await view.end_giveaway()
            else:
                view.schedule_end(remaining)
        except Exception as e:
            ACTIVE_GIVEAWAYS.pop(giveaway_id, None)
            print(f"[Giveaway Resume] Failed to resume giveaway {giveaway_id}: {e}")


async def end_after_delay(view: GiveawayView, delay: float):
    """Sleep until the giveaway's end time, then finish it. Cancelled by ``draw`` / ``cog_unload``."""
    await asyncio.sleep(max(0, delay))
    try:
        await view.end_giveaway()
    except Exception as e:
        print(f"[Giveaway] Failed to end giveaway {view.giveaway_id}: {e}")


class RoleButtons(View):
    def __init__(self, roles, guild_id, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        for role_id in roles:
            role = guild.get_role(role_id)
            if not role:
                continue
            role_button = Button(label=role.name, style=discord.ButtonStyle.primary, custom_id=f"claim_{role_id}")
            role_button.callback = self.make_callback(role_id)
            self.add_item(role_button)

    def make_callback(self, role_id):
        async def callback(interaction: discord.Interaction):
            role = interaction.guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message("❌ Role not found.", ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            try:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role)
                    msg = f"❌ Removed {role.mention}"
                else:
                    await interaction.user.add_roles(role)
                    msg = f"✅ You claimed {role.mention}"
                await interaction.followup.send(msg, ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("⚠️ I don't have permission to manage roles.", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ Something went wrong: `{e}`", ephemeral=True)

        return callback


async def refresh_roles_embed(ctx, guild_id):
    settings = await state.roles_col.find_one({"_id": guild_id})
    if not settings:
        return
    role_ids = settings.get("roles", [])
    message_id = settings.get("message_id")
    if not message_id:
        return
    try:
        msg = await ctx.channel.fetch_message(message_id)
    except discord.NotFound:
        return
    roles = [ctx.guild.get_role(r) for r in role_ids if ctx.guild.get_role(r)]
    if not roles:
        embed = discord.Embed(
            title="🎭 Claim Your Roles", description="⚠️ No valid roles available.", color=discord.Color.red()
        )
        await msg.edit(embed=embed, view=None)
        return
    embed = discord.Embed(
        title="🎭 Claim Your Roles",
        description="\n".join([role.mention for role in roles]),
        color=discord.Color.blurple(),
    )
    view = RoleButtons(role_ids, guild_id, ctx.guild)
    await msg.edit(embed=embed, view=view)


class GiveawaysPolls(commands.Cog):
    """Giveaways, polls, claimable roles, money drops, addmoney/removemoney, and the reminder loop."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    @tasks.loop(hours=1)
    async def check_expired_drops(self):
        """Expire unclaimed money drops older than 3 days: delete their messages, refund non staff drops."""
        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        query = {"claimed": False, "created_at": {"$lt": three_days_ago.isoformat()}}
        async for drop in state.drop_instances_col.find(query):
            try:
                guild = self.bot.get_guild(int(drop["guild_id"]))
                if not guild:
                    continue
                channel = guild.get_channel(int(drop["channel_id"]))
                if not channel:
                    continue
                message = await channel.fetch_message(int(drop["message_id"]))
                await message.delete()
            except discord.NotFound:
                pass
            except Exception as e:
                print(f"Error deleting drop message {drop['message_id']}: {e}")
            if not drop.get("staff_drop"):
                try:
                    await econ.add_balance(int(drop["author_id"]), int(drop["guild_id"]), int(drop["amount"]))
                except Exception as e:
                    print(f"Error refunding drop {drop['_id']} to {drop['author_id']}: {e}")
            await state.drop_instances_col.delete_one({"_id": drop["_id"]})

    @tasks.loop(seconds=30)
    async def check_polls(self):
        now = datetime.now(timezone.utc)
        async for poll in state.polls_col.find({"end_time": {"$lte": now}}):
            channel = self.bot.get_channel(int(poll["channel_id"]))
            if not channel:
                continue
            try:
                msg = await channel.fetch_message(int(poll["message_id"]))
                counts = {}
                for v in poll["votes"].values():
                    counts[v] = counts.get(v, 0) + 1
                closed_embed = build_poll_embed(poll["question"], poll["options"], counts, closed=True)
                await msg.edit(embed=closed_embed, view=None)
                await channel.send("⏰ Poll closed!", reference=msg)
            except Exception as e:
                print(f"Error closing poll {poll['poll_id']}: {e}")
            await state.polls_col.delete_one({"poll_id": poll["poll_id"]})

    @tasks.loop(seconds=30)
    async def check_reminders(self):
        """Fire any due reminders by DMing the requesting user, then delete the reminder record."""
        now = datetime.now(timezone.utc)
        reminders = await state.reminders_col.find({"remind_at": {"$lte": now}}).to_list(length=None)
        for reminder in reminders:
            user = self.bot.get_user(int(reminder["user_id"]))
            if user:
                try:
                    await user.send(f"⏰ Reminder: {reminder['message']}")
                except Exception as e:
                    print(f"Failed to send reminder to {user}: {e}")
            await state.reminders_col.delete_one({"_id": reminder["_id"]})

    @check_reminders.before_loop
    async def before_check_reminders(self):
        """Wait for the bot to be ready before starting the reminder polling loop."""
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="poll", description="Create a poll")
    async def poll(self, ctx):
        # Slash invocations get the form; prefix invocations get the step-by-step wizard below.
        if ctx.interaction is not None:
            await ctx.interaction.response.send_modal(PollModal())
            return

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            await ctx.send("📝 Let's create a poll! Type `cancel` anytime to stop.")
            await ctx.send("❓ What is the question?")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            question = msg.content
            await ctx.send("➡️ Enter option 1:")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            option1 = msg.content
            await ctx.send("➡️ Enter option 2:")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            option2 = msg.content
            options = [option1, option2]
            for i in range(3, 6):
                await ctx.send(f"➡️ Enter option {i} (or type `skip` to leave blank):")
                msg = await self.bot.wait_for("message", check=check, timeout=300)
                if msg.content.lower() == "cancel":
                    return await ctx.send("❌ Poll creation cancelled.")
                if msg.content.lower() == "skip":
                    options.append(None)
                    continue
                options.append(msg.content)
            await ctx.send("📺 Mention the channel to post in (e.g., #general):")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            if not msg.channel_mentions:
                return await ctx.send("⚠️ Invalid channel mention. Cancelled.")
            channel = msg.channel_mentions[0]
            await ctx.send("⏳ Enter poll duration (e.g., `1h`, `30m`, `2d`):")
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            try:
                duration_seconds = econ.parse_time(msg.content)
            except Exception as e:
                return await ctx.send(f"⚠️ Invalid duration format. {e}")
            end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            final_options = [opt for opt in options if opt]
            poll_id = f"{ctx.guild.id}-{ctx.message.id}"
            counts = {}
            view = PollView(poll_id, final_options)
            embed = build_poll_embed(question, final_options, counts, closed=False, duration=msg.content)
            poll_msg = await channel.send(embed=embed, view=view)
            await state.polls_col.insert_one(
                {
                    "poll_id": poll_id,
                    "question": question,
                    "options": final_options,
                    "votes": {},
                    "channel_id": str(channel.id),
                    "message_id": str(poll_msg.id),
                    "end_time": end_time,
                    "duration_raw": msg.content,
                }
            )
            await ctx.send("✅ Poll created successfully!")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Poll creation timed out due to inactivity.")
        except Exception as e:
            await ctx.send(f"⚠️ Error: {e}")

    @commands.hybrid_command(name="giveaway", description="Create a giveaway using a form. Staff only.")
    @perms.staffperm("giveaways")
    @perms.staff_only()
    async def giveaway(self, ctx: commands.Context):
        if ctx.interaction is None:
            return await ctx.send("⚠ Giveaways are created through a form. Please use the slash command `/giveaway`.")
        await ctx.interaction.response.send_modal(GiveawayModal())

    @commands.hybrid_command(name="reroll", description="Pick new winners for a past giveaway.")
    @perms.staffperm("giveaways")
    @perms.staff_only()
    async def reroll(self, ctx: commands.Context, message_id: int):
        data = await state.giveaway_col.find_one({"message_id": message_id})
        if not data:
            await ctx.send("❌ Giveaway not found.", ephemeral=True)
            return
        if not data.get("ended"):
            await ctx.send("❌ That giveaway hasn't ended yet. Use `draw` to end it now.", ephemeral=True)
            return
        winners = pick_winners(data.get("participants") or {}, data["winners_count"])
        if not winners:
            await ctx.send("😔 No participants in that giveaway.", ephemeral=True)
            return
        winners_mentions = ", ".join(f"<@{wid}>" for wid in winners)
        await state.giveaway_col.update_one({"_id": data["_id"]}, {"$set": {"winners": winners}})
        try:
            channel = ctx.bot.get_channel(int(data["channel_id"]))
            message = await channel.fetch_message(int(data["message_id"]))
            if message.embeds:
                embed = message.embeds[0]
                for idx, field in enumerate(embed.fields):
                    if field.name == "Winners":
                        embed.set_field_at(idx, name="Winners", value=winners_mentions, inline=False)
                        break
                await message.edit(embed=embed)
            await channel.send(f"🔄 **Reroll!** New winners for **{data['prize']}**: {winners_mentions}")
        except Exception as e:
            await ctx.send(f"⚠️ Winners updated in database, but failed to update message: {e}", ephemeral=True)
            return
        await ctx.send("✅ Reroll complete and announced in the giveaway's channel.", ephemeral=True)

    @commands.hybrid_command(
        name="draw", description="Instantly draw winners from a giveaway using its message ID. Staff only."
    )
    @app_commands.describe(message_id="The giveaway message's ID")
    @perms.staffperm("giveaways")
    @perms.staff_only()
    async def draw(self, ctx: commands.Context, message_id: str):
        # message_id is a str, not int: message IDs are 64-bit snowflakes that can exceed the
        # ~9e15 safe integer range Discord enforces on slash command INTEGER options, which made
        # every real message ID get rejected client-side with "Input a valid integer".
        try:
            message_id_int = int(message_id)
        except ValueError:
            await ctx.send("❌ Please provide a valid message ID.", ephemeral=True)
            return
        data = await state.giveaway_col.find_one({"message_id": message_id_int})
        if not data:
            await ctx.send("❌ Giveaway not found.", ephemeral=True)
            return
        if data.get("ended"):
            await ctx.send("❌ That giveaway has already ended. Use `reroll` to pick new winners.", ephemeral=True)
            return
        view = ACTIVE_GIVEAWAYS.get(data["_id"])
        participants = view.participants if view is not None else (data.get("participants") or {})
        if isinstance(participants, list):
            participants = {}
        if not participants:
            await ctx.send("😔 No participants in that giveaway yet, so it stays open.", ephemeral=True)
            return
        if view is not None:
            # Live in this process: finishing through the view cancels the scheduled end, removes
            # the buttons and announces exactly once.
            winners = await view.end_giveaway()
            if winners is None:
                await ctx.send("❌ That giveaway has already ended.", ephemeral=True)
                return
        else:
            # Not live here (e.g. its message could not be resumed): finish it from the stored data.
            winners = pick_winners(participants, data["winners_count"])
            winners_mentions = ", ".join(f"<@{wid}>" for wid in winners)
            await state.giveaway_col.update_one(
                {"_id": data["_id"]},
                {"$set": {"ended": True, "winners": winners, "ended_at": datetime.now(timezone.utc).isoformat()}},
            )
            try:
                channel = ctx.bot.get_channel(int(data["channel_id"]))
                message = await channel.fetch_message(int(data["message_id"]))
                if message.embeds:
                    embed = message.embeds[0]
                    apply_result_to_embed(embed, winners_mentions)
                    await message.edit(embed=embed, view=None)
                await channel.send(f"🎉 Congratulations {winners_mentions}! You won **{data['prize']}**!")
            except Exception as e:
                await ctx.send(f"⚠️ Winners drawn but failed to update message: {e}", ephemeral=True)
                return
        winners_mentions = ", ".join(f"<@{wid}>" for wid in winners)
        await ctx.send(f"✅ Winners drawn and giveaway ended: {winners_mentions}", ephemeral=True)

    @giveaway.error
    async def giveaway_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await errors.send_hybrid_error(ctx, content="❌ You don't have permission to create giveaways.")
        elif isinstance(error, commands.BadArgument):
            await errors.send_hybrid_error(ctx, content="❌ Invalid input. Please check your command format.")
        else:
            traceback.print_exc()
            await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred while processing the giveaway.")

    @reroll.error
    async def reroll_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await errors.send_hybrid_error(ctx, content="❌ You don't have permission to reroll giveaways.")
        elif isinstance(error, commands.BadArgument):
            await errors.send_hybrid_error(ctx, content="❌ Invalid message ID.")
        else:
            traceback.print_exc()
            await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred while rerolling.")

    @draw.error
    async def draw_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await errors.send_hybrid_error(ctx, content="❌ You don't have permission to draw giveaways.")
        elif isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await errors.send_hybrid_error(ctx, content="❌ Please provide the giveaway's message ID.")
        else:
            traceback.print_exc()
            await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred while drawing winners.")

    @commands.hybrid_command(name="roles", description="Show claimable roles")
    async def roles(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        settings = await state.roles_col.find_one({"_id": guild_id})
        if not settings or not settings.get("roles"):
            return await ctx.send("⚠️ No claimable roles set yet!", ephemeral=True)
        role_ids = settings["roles"]
        roles = [ctx.guild.get_role(r) for r in role_ids if ctx.guild.get_role(r)]
        if not roles:
            return await ctx.send("⚠️ All stored roles are invalid.", ephemeral=True)
        embed = discord.Embed(
            title="🎭 Claim Your Roles",
            description="\n".join([role.mention for role in roles]),
            color=discord.Color.blurple(),
        )
        view = RoleButtons(role_ids, guild_id, ctx.guild)
        msg = await ctx.send(embed=embed, view=view)
        await state.roles_col.update_one({"_id": guild_id}, {"$set": {"message_id": msg.id}}, upsert=True)

    @commands.hybrid_command(name="roleadd", description="Add a claimable role")
    @perms.staffperm("roles")
    @perms.staff_only()
    async def roleadd(self, ctx: commands.Context, role: discord.Role):
        guild_id = ctx.guild.id
        settings = await state.roles_col.find_one({"_id": guild_id})
        if settings:
            if role.id in settings["roles"]:
                return await ctx.send("⚠️ That role is already claimable.", ephemeral=True)
            await state.roles_col.update_one({"_id": guild_id}, {"$push": {"roles": role.id}})
        else:
            await state.roles_col.insert_one({"_id": guild_id, "roles": [role.id]})
        await ctx.send(f"✅ {role.mention} has been added as a claimable role!", ephemeral=True)
        await refresh_roles_embed(ctx, guild_id)

    @commands.hybrid_command(name="roleremove", description="Remove a claimable role")
    @perms.staffperm("roles")
    @perms.staff_only()
    async def roleremove(self, ctx: commands.Context, role: discord.Role):
        guild_id = ctx.guild.id
        settings = await state.roles_col.find_one({"_id": guild_id})
        if not settings or role.id not in settings.get("roles", []):
            return await ctx.send("⚠️ That role is not claimable.", ephemeral=True)
        await state.roles_col.update_one({"_id": guild_id}, {"$pull": {"roles": role.id}})
        await ctx.send(f"❌ {role.mention} has been removed from claimable roles.", ephemeral=True)
        await refresh_roles_embed(ctx, guild_id)

    @commands.hybrid_command(name="addmoney", description="Add money to one or more users (economy admin only).")
    @app_commands.describe(
        amount="Amount to add (supports k, m, b suffixes)",
        users="User(s) to give money to - mentions or IDs, space/comma separated",
    )
    @perms.staffperm("economy")
    @xp.xp_earn(4, 8)
    async def addmoney(self, ctx, amount: str, *, users: str):
        if ctx.author.id not in (cfg.AUTHORIZED_USER_IDS | {ctx.guild.owner_id}):
            return await ctx.send("❌ You are not authorized to use this command.")
        try:
            coins = econ.parse_amount(amount)
            if coins is None or coins <= 0:
                raise ValueError
        except Exception:
            return await ctx.send("❌ Invalid amount.\nUse formats like: `100`, `4k`, `2m`, `1.5mil`, `123,456`")
        uid_list = [int(m) for m in re.findall(r"<@!?(\d+)>", users)]
        remaining_text = re.sub(r"<@!?\d+>", "", users)
        for raw in re.findall(r"\b(\d{15,20})\b", remaining_text):
            uid = int(raw)
            if uid not in uid_list:
                uid_list.append(uid)
        if not uid_list:
            return await ctx.send("❌ No valid users found. Mention users or provide their IDs.")
        results = []
        for uid in uid_list:
            member = ctx.guild.get_member(uid)
            display = member.mention if member else f"`{uid}`"
            user_data = await econ.get_user(ctx, ctx.guild.id, uid)
            new_bank = user_data.get("bank", 0) + coins
            await state.economy_col.update_one({"_id": f"{ctx.guild.id}-{uid}"}, {"$set": {"bank": new_bank}})
            await perms.log_action(ctx, f"Added 🪙 {coins:,} to {display}'s bank.", user_id=uid, action_type="AddMoney")
            results.append(f"✅ {display} -> new bank: **{new_bank:,}**")
        await ctx.send(f"Added 🪙 **{coins:,}** to {len(uid_list)} user(s):\n" + "\n".join(results))

    @addmoney.error
    async def addmoney_error(self, ctx, error):
        try:
            if isinstance(error, commands.CheckFailure):
                return
            prefix = await self.bot.get_prefix(ctx.message)
            if isinstance(error, commands.BadArgument):
                return await errors.send_hybrid_error(
                    ctx,
                    content=f"❌ Invalid arguments. Usage: `{prefix}addmoney <amount> @user1 @user2 ...`\nExample: `{prefix}addmoney 100 @User1 @User2`",
                )
            elif isinstance(error, commands.MissingRequiredArgument):
                return await errors.send_hybrid_error(
                    ctx,
                    content=f"❌ Missing arguments. Usage: `{prefix}addmoney <amount> @user1 @user2 ...`\nExample: `{prefix}addmoney 100 @User1 @User2`",
                )
            else:
                root_error = errors.unwrap_command_error(error)
                print(f"[ERROR] addmoney_error: {root_error}")
                traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
                return await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred.")
        except Exception as e:
            print(f"[addmoney_error] {e}")

    @commands.hybrid_command(
        name="removemoney", description="Remove money from one or more users (economy admin only)."
    )
    @app_commands.describe(
        amount="Amount to remove (supports k, m, b suffixes)",
        users="User(s) to take money from - mentions or IDs, space/comma separated",
    )
    @perms.staffperm("economy")
    @xp.xp_earn(4, 8)
    async def removemoney(self, ctx, amount: str, *, users: str):
        if ctx.author.id not in (cfg.AUTHORIZED_USER_IDS | {ctx.guild.owner_id}):
            return await ctx.send("❌ You are not authorized to use this command.")
        try:
            coins = econ.parse_amount(amount)
            if coins is None or coins <= 0:
                raise ValueError
        except Exception:
            return await ctx.send("❌ Invalid amount.\nUse formats like: `100`, `4k`, `2m`, `1.5mil`, `123,456`")
        uid_list = [int(m) for m in re.findall(r"<@!?(\d+)>", users)]
        remaining_text = re.sub(r"<@!?\d+>", "", users)
        for raw in re.findall(r"\b(\d{15,20})\b", remaining_text):
            uid = int(raw)
            if uid not in uid_list:
                uid_list.append(uid)
        if not uid_list:
            return await ctx.send("❌ No valid users found. Mention users or provide their IDs.")
        results = []
        for uid in uid_list:
            member = ctx.guild.get_member(uid)
            display = member.mention if member else f"`{uid}`"
            user_data = await econ.get_user(ctx, ctx.guild.id, uid)
            wallet = user_data.get("wallet", 0)
            bank = user_data.get("bank", 0)
            total = wallet + bank
            if total < coins:
                results.append(f"⚠️ {display} - insufficient funds (has {total:,})")
                continue
            if wallet >= coins:
                new_wallet = wallet - coins
                new_bank = bank
            else:
                new_wallet = 0
                new_bank = bank - (coins - wallet)
            await state.economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{uid}"}, {"$set": {"wallet": new_wallet, "bank": new_bank}}
            )
            await perms.log_action(
                ctx, f"Removed 🪙 {coins:,} from {display}'s balance.", user_id=uid, action_type="RemoveMoney"
            )
            results.append(f"✅ {display} -> wallet: **{new_wallet:,}** | bank: **{new_bank:,}**")
        await ctx.send(f"Removed 🪙 **{coins:,}** from user(s):\n" + "\n".join(results))

    @commands.hybrid_command(name="drop", description="Create a money drop (staff spawns money, members pay).")
    @app_commands.describe(amount="Amount to drop", message="Optional message to include")
    async def drop(self, ctx, amount: str, *, message: str | None = None):
        if not ctx.guild:
            return await ctx.send("❌ This command can only be used in a server.")
        guild_id = ctx.guild.id
        user_id = ctx.author.id
        try:
            coins = econ.parse_amount(amount)
            if coins is None or coins <= 0:
                raise ValueError
        except Exception:
            return await ctx.send("❌ Invalid amount.\nUse formats like: `100`, `4k`, `2m`, `1.5mil`")
        is_staff = False
        try:
            is_staff = await perms.staffperm("money_drop").predicate(ctx)
        except Exception:
            is_staff = False
        if not is_staff:
            ok = await perms.check_channel(ctx, "DROP_CHANNELS", "Drop")
            if not ok:
                return
        if not is_staff:
            try:
                data = await econ.get_user(ctx, guild_id, user_id)
                wallet = int(data.get("wallet", 0))
                bank = int(data.get("bank", 0))
                if bank >= coins:
                    new_bank = bank - coins
                    new_wallet = wallet
                elif bank + wallet >= coins:
                    take_from_wallet = coins - bank
                    new_bank = 0
                    new_wallet = wallet - take_from_wallet
                else:
                    total = wallet + bank
                    return await ctx.send(
                        f"❌ You don't have enough money.\n🏦 Bank: **{bank:,}** | 🪙 Wallet: **{wallet:,}**\n🪙 Required: **{coins:,}** (Total: {total:,})"
                    )
                await state.economy_col.update_one(
                    {"_id": f"{guild_id}-{user_id}"}, {"$set": {"wallet": new_wallet, "bank": new_bank}}, upsert=True
                )
            except Exception:
                return await ctx.send("⚠️ Failed to process your balance.\nPlease try again later.")
        role_id = None
        if is_staff:
            try:
                settings = await state.drops_col.find_one({"_id": guild_id})
                role_id = settings.get("role_id") if settings else None
            except Exception:
                role_id = None
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        embed = discord.Embed(
            title="💰 Money Drop!",
            description=f"Someone dropped **🪙 {coins:,}**!\n\nClick the button below to claim it!",
            color=discord.Color.gold(),
        )
        if message:
            embed.add_field(name="💬 Message", value=message, inline=False)
        embed.set_footer(text=f"Dropped by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        view = DropClaimView()
        role_ping = f"<@&{role_id}>" if is_staff and role_id else ""
        try:
            msg = await ctx.channel.send(content=role_ping or None, embed=embed, view=view)
            if not errors.is_prefix(ctx) and ctx.interaction and not ctx.interaction.response.is_done():
                try:
                    await ctx.interaction.response.send_message("✅ Drop created!", ephemeral=True, delete_after=4)
                except discord.HTTPException:
                    pass
        except Exception:
            if not is_staff:
                await state.economy_col.update_one(
                    {"_id": f"{guild_id}-{user_id}"}, {"$set": {"wallet": wallet, "bank": bank}}, upsert=True
                )
            return await ctx.send("❌ Failed to send the drop message. You have been refunded.")
        try:
            await state.drop_instances_col.update_one(
                {"message_id": str(msg.id)},
                {
                    "$set": {
                        "message_id": str(msg.id),
                        "channel_id": str(ctx.channel.id),
                        "guild_id": str(guild_id),
                        "amount": int(coins),
                        "author_id": str(user_id),
                        "claimed": False,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "staff_drop": is_staff,
                    }
                },
                upsert=True,
            )
        except Exception as e:
            print(f"[drop] Failed to save drop instance: {e}")

    @drop.error
    async def drop_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await errors.send_hybrid_error(
                ctx,
                content="❌ Missing arguments!\n**Usage:** `.drop <amount> [message]`\n**Example:** `.drop 5000 Enjoy the coins!`",
            )
        elif isinstance(error, commands.BadArgument):
            await errors.send_hybrid_error(
                ctx, content="❌ Invalid argument.\nUse formats like: `100`, `4k`, `2m`, `1.5mil`"
            )
        elif isinstance(error, commands.CommandInvokeError):
            await errors.send_hybrid_error(
                ctx, content="⚠️ Something went wrong while running this command.\nPlease try again later."
            )
        else:
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred.\nPlease contact an administrator."
            )

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the drop/poll/reminder loops, register the persistent role-claim and drop-claim
        views, resume unfinished giveaways and restore open polls (runs once)."""
        if self._ready_done:
            return
        self._ready_done = True
        self.check_expired_drops.start()
        if not self.check_reminders.is_running():
            self.check_reminders.start()
            print("🔄 Started reminders check loop")
        async for doc in state.roles_col.find({}):
            guild_id = doc["_id"]
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            role_ids = doc.get("roles", [])
            if not role_ids:
                continue
            view = RoleButtons(role_ids, guild_id, guild)
            self.bot.add_view(view)
        print("✅ Persistent role buttons loaded.")
        self.bot.add_view(DropClaimView())
        asyncio.create_task(resume_giveaways(self.bot))
        now = datetime.now(timezone.utc)
        async for poll_doc in state.polls_col.find({"end_time": {"$gt": now}}):
            try:
                channel = self.bot.get_channel(int(poll_doc["channel_id"]))
                if not channel:
                    continue
                msg = await channel.fetch_message(int(poll_doc["message_id"]))
                view = PollView(poll_doc["poll_id"], poll_doc["options"])
                await msg.edit(view=view)
                print(f"🔄 Restored poll {poll_doc['poll_id']}")
            except Exception as e:
                print(f"Failed to restore poll {poll_doc['poll_id']}: {e}")
        if not self.check_polls.is_running():
            self.check_polls.start()

    def cog_unload(self):
        """Cancel the loops this cog owns and every pending giveaway end task (they are resumed
        from the database on the next start)."""
        for loop in (self.check_expired_drops, self.check_polls, self.check_reminders):
            if loop.is_running():
                loop.cancel()
        for view in list(ACTIVE_GIVEAWAYS.values()):
            if view.end_task is not None and not view.end_task.done():
                view.end_task.cancel()
        ACTIVE_GIVEAWAYS.clear()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GiveawaysPolls(bot))
