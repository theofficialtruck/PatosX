"""Giveaway modal + persistent view + resume helpers."""

from __future__ import annotations

import asyncio
import io
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import discord

from bot.database import giveaway_col
from bot.utils.time_parsing import parse_time


class GiveawayView(discord.ui.View):
    """Persistent giveaway controls. Self-counts participants on edit."""

    def __init__(
        self,
        embed_message: discord.Message,
        giveaway_id: str,
        end_time: datetime,
        winners: int,
        prize: str,
    ) -> None:
        super().__init__(timeout=None)
        self.participants: dict[int, int] = defaultdict(int)
        self.embed_message = embed_message
        self.giveaway_id = giveaway_id
        self.end_time = end_time
        self.winners_count = winners
        self.prize = prize

    async def update_db(self) -> None:
        await giveaway_col.update_one(
            {"_id": self.giveaway_id},
            {
                "$set": {
                    "participants": {
                        str(uid): entries
                        for uid, entries in self.participants.items()
                    }
                }
            },
        )

    @discord.ui.button(label="🎉 Entry", style=discord.ButtonStyle.blurple)
    async def entry_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id in self.participants:
            del self.participants[interaction.user.id]
            await interaction.response.send_message(
                "❌ You left the giveaway.", ephemeral=True
            )
        else:
            entries = 1
            data = await giveaway_col.find_one({"_id": self.giveaway_id})
            bonus_roles = data.get("bonus_roles", {}) if data else {}

            for role_id, bonus in bonus_roles.items():
                role = interaction.guild.get_role(int(role_id))
                if role and role in interaction.user.roles:
                    entries += bonus

            self.participants[interaction.user.id] = entries
            await interaction.response.send_message(
                f"✅ You joined the giveaway with **{entries} ticket(s)**!",
                ephemeral=True,
            )

        await self.update_db()

        embed = self.embed_message.embeds[0]
        for index, field in enumerate(embed.fields):
            if field.name == "Participants":
                embed.set_field_at(
                    index,
                    name="Participants",
                    value=str(len(self.participants)),
                    inline=False,
                )
                break
        await self.embed_message.edit(embed=embed)

    @discord.ui.button(label="👥 Participants", style=discord.ButtonStyle.gray)
    async def participants_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.participants:
            await interaction.response.send_message(
                "👀 Nobody yet! Be the first to join!", ephemeral=True
            )
            return

        names: list[str] = []
        for uid, entries in self.participants.items():
            user = interaction.client.get_user(uid) or await interaction.client.fetch_user(uid)
            if user:
                names.append(f"{user} - {entries} ticket(s)")
        names_str = "\n".join(names)

        if len(names_str) > 1900:
            file = discord.File(
                io.BytesIO(names_str.encode()), filename="participants.txt"
            )
            await interaction.response.send_message(
                "📄 Too many participants to display! Here's the list:",
                file=file,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"**Participants:**\n{names_str}", ephemeral=True
            )

    async def end_giveaway(self) -> None:
        embed = self.embed_message.embeds[0]
        for index, field in enumerate(embed.fields):
            if field.name == "Ends":
                embed.set_field_at(
                    index,
                    name="Ended",
                    value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>",
                    inline=False,
                )
                break
        await self.embed_message.edit(embed=embed, view=None)

        channel = self.embed_message.channel

        if isinstance(self.participants, list):  # pragma: no cover - safety net
            self.participants = {}

        if not self.participants:
            await channel.send(
                f"😔 No one joined the giveaway for **{self.prize}**. "
                "No winners this time!"
            )
            await giveaway_col.update_one(
                {"_id": self.giveaway_id},
                {"$set": {"ended": True, "winners": []}},
            )
            return

        ticket_pool: list[int] = []
        for uid, entries in self.participants.items():
            ticket_pool.extend([uid] * entries)

        unique_winners: list[int] = []
        while len(unique_winners) < self.winners_count and ticket_pool:
            pick = random.choice(ticket_pool)
            if pick not in unique_winners:
                unique_winners.append(pick)

        winners_mentions = ", ".join(f"<@{uid}>" for uid in unique_winners)
        await channel.send(
            f"🎉 Congratulations {winners_mentions}! You won **{self.prize}**!"
        )

        await giveaway_col.update_one(
            {"_id": self.giveaway_id},
            {"$set": {"ended": True, "winners": unique_winners}},
        )

        for index, field in enumerate(embed.fields):
            if field.name == "Winners":
                embed.set_field_at(
                    index,
                    name="Winners",
                    value=winners_mentions,
                    inline=False,
                )
                break
        await self.embed_message.edit(embed=embed)


async def end_after_delay(view: GiveawayView, delay: float) -> None:
    """Wait ``delay`` seconds, then end the giveaway."""
    await asyncio.sleep(max(0, delay))
    await view.end_giveaway()


class GiveawayModal(discord.ui.Modal, title="Create Giveaway"):
    prize = discord.ui.TextInput(label="Prize", placeholder="What’s the giveaway prize?", required=True)
    winners = discord.ui.TextInput(label="Number of winners", placeholder="Example: 3", required=True)
    duration = discord.ui.TextInput(
        label="Duration",
        placeholder="e.g. 30s, 15m, 2h, 3d, 1w, 2mo, 1y, or combinations like 1d12h",
        required=True,
    )
    role_requirements = discord.ui.TextInput(
        label="Role Requirements (optional)",
        placeholder="Role IDs separated by commas",
        required=False,
    )
    bonus_roles = discord.ui.TextInput(
        label="Bonus Roles (optional)",
        placeholder="Format: role_id|bonus, role_id|bonus",
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            winners_count = int(self.winners.value)
            if winners_count < 1:
                raise ValueError("Number of winners must be at least 1.")

            duration_seconds = parse_time(self.duration.value)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

        embed = discord.Embed(title=self.prize.value, color=discord.Color.blue())
        embed.add_field(name="Hosted by", value=interaction.user.mention, inline=False)
        embed.add_field(
            name="Ends",
            value=(
                f"<t:{int(end_time.timestamp())}:F> "
                f"(<t:{int(end_time.timestamp())}:R>)"
            ),
            inline=False,
        )
        embed.add_field(name="Winners", value=str(winners_count), inline=False)
        embed.add_field(name="Participants", value="0", inline=False)

        if self.role_requirements.value:
            embed.add_field(
                name="Requirements",
                value=f"Roles required (one of): {self.role_requirements.value}",
                inline=False,
            )

        if self.bonus_roles.value:
            lines: list[str] = []
            for entry in self.bonus_roles.value.split(","):
                try:
                    role, bonus = entry.strip().split("|")
                    lines.append(f"{role.strip()} • {bonus.strip()} bonus entries")
                except ValueError:
                    continue
            if lines:
                embed.add_field(
                    name="Roles with bonus entries",
                    value="\n".join(lines),
                    inline=False,
                )

        sent_message = await interaction.channel.send(embed=embed)

        bonus_roles_dict: dict[int, int] = {}
        if self.bonus_roles.value:
            for entry in self.bonus_roles.value.split(","):
                try:
                    role, bonus = entry.strip().split("|")
                    role_id = int(role.strip().replace("<@&", "").replace(">", ""))
                    bonus_roles_dict[role_id] = int(bonus.strip())
                except ValueError:
                    continue

        giveaway_id = str(sent_message.id)
        await giveaway_col.insert_one(
            {
                "_id": giveaway_id,
                "channel_id": interaction.channel.id,
                "message_id": sent_message.id,
                "prize": self.prize.value,
                "host_id": interaction.user.id,
                "end_time": end_time.isoformat(),
                "winners_count": winners_count,
                "participants": {},
                "ended": False,
                "bonus_roles": bonus_roles_dict,
            }
        )

        view = GiveawayView(
            embed_message=sent_message,
            giveaway_id=giveaway_id,
            end_time=end_time,
            winners=winners_count,
            prize=self.prize.value,
        )
        await sent_message.edit(view=view)

        await interaction.response.send_message(
            "✅ Giveaway created!", ephemeral=True
        )

        asyncio.create_task(end_after_delay(view, duration_seconds))


async def resume_giveaways(bot) -> None:
    """Re-attach views and schedule end timers for active giveaways at boot."""
    now = datetime.now(timezone.utc)
    cursor = giveaway_col.find({"ended": False})

    async for data in cursor:
        try:
            end_time = datetime.fromisoformat(data["end_time"])
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            remaining = (end_time - now).total_seconds()

            channel = bot.get_channel(data["channel_id"])
            if not channel:
                print(
                    f"[Giveaway Resume] Channel {data['channel_id']} not found "
                    f"for giveaway {data['_id']}"
                )
                continue

            message = await channel.fetch_message(data["message_id"])
            view = GiveawayView(
                embed_message=message,
                giveaway_id=data["_id"],
                end_time=end_time,
                winners=data["winners_count"],
                prize=data["prize"],
            )

            participants = data.get("participants", {})
            if isinstance(participants, list):
                participants = {}
            for uid, entries in participants.items():
                try:
                    view.participants[int(uid)] = entries
                except Exception as exc:
                    print(
                        f"[Giveaway Resume] Could not fetch participant {uid} "
                        f"for giveaway {data['_id']}: {exc}"
                    )

            await message.edit(view=view)

            if remaining <= 0:
                print(
                    f"[Giveaway Resume] Giveaway {data['_id']} expired while "
                    "offline. Ending now."
                )
                await view.end_giveaway()
            else:
                asyncio.create_task(end_after_delay(view, remaining))

        except Exception as exc:
            print(f"[Giveaway Resume] Failed to resume giveaway {data['_id']}: {exc}")


__all__ = ["GiveawayView", "GiveawayModal", "end_after_delay", "resume_giveaways"]
