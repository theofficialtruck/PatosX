"""Polls — modal, view, and embed builder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from bot.database import polls_col
from bot.utils.time_parsing import parse_time


def build_poll_embed(question, options, counts, closed: bool = False, duration=None) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Poll" + (" (Closed)" if closed else ""),
        color=discord.Color.blue(),
    )
    embed.description = f"**{question}**"
    for i, opt in enumerate(options, start=1):
        if opt:
            embed.add_field(
                name=opt,
                value=f"Votes: {counts.get(str(i), 0)}",
                inline=False,
            )
    if duration and not closed:
        embed.set_footer(text=f"Poll duration: {duration}")
    return embed


class PollButton(discord.ui.Button):
    def __init__(self, label: str, option: int, poll_id: str) -> None:
        super().__init__(style=discord.ButtonStyle.primary, label=label)
        self.option = option
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction) -> None:
        poll = await polls_col.find_one({"poll_id": self.poll_id})
        if not poll:
            return await interaction.response.send_message(
                "⚠️ Poll not found.", ephemeral=True
            )

        poll["votes"][str(interaction.user.id)] = str(self.option)
        await polls_col.update_one(
            {"poll_id": self.poll_id}, {"$set": {"votes": poll["votes"]}}
        )

        counts: dict[str, int] = {}
        for vote in poll["votes"].values():
            counts[vote] = counts.get(vote, 0) + 1

        embed = build_poll_embed(
            poll["question"],
            poll["options"],
            counts,
            closed=False,
            duration=poll["duration_raw"],
        )
        channel = interaction.client.get_channel(int(poll["channel_id"]))
        if channel is None:
            return await interaction.response.send_message(
                "⚠️ Poll channel not found.", ephemeral=True
            )
        try:
            message = await channel.fetch_message(int(poll["message_id"]))
            await message.edit(embed=embed, view=self.view)
        except (discord.NotFound, discord.Forbidden) as exc:
            print(f"Could not update poll embed for {self.poll_id}: {exc}")

        await interaction.response.send_message(
            f"✅ You voted for **{self.label}**", ephemeral=True
        )


class RemoveVoteButton(discord.ui.Button):
    def __init__(self, poll_id: str) -> None:
        super().__init__(style=discord.ButtonStyle.danger, label="Remove Vote")
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction) -> None:
        poll = await polls_col.find_one({"poll_id": self.poll_id})
        if not poll:
            return await interaction.response.send_message(
                "⚠️ Poll not found.", ephemeral=True
            )

        if str(interaction.user.id) in poll["votes"]:
            del poll["votes"][str(interaction.user.id)]
            await polls_col.update_one(
                {"poll_id": self.poll_id}, {"$set": {"votes": poll["votes"]}}
            )

            counts: dict[str, int] = {}
            for vote in poll["votes"].values():
                counts[vote] = counts.get(vote, 0) + 1

            embed = build_poll_embed(
                poll["question"],
                poll["options"],
                counts,
                closed=False,
                duration=poll["duration_raw"],
            )
            channel = interaction.client.get_channel(int(poll["channel_id"]))
            if channel is None:
                return await interaction.response.send_message(
                    "⚠️ Poll channel not found.", ephemeral=True
                )
            try:
                message = await channel.fetch_message(int(poll["message_id"]))
                await message.edit(embed=embed, view=self.view)
            except (discord.NotFound, discord.Forbidden) as exc:
                print(f"Could not update poll embed for {self.poll_id}: {exc}")

            await interaction.response.send_message(
                "🗑️ Your vote was removed.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "⚠️ You haven’t voted yet.", ephemeral=True
            )


class PollView(discord.ui.View):
    """Persistent view that hosts the per-option buttons + a remove-vote button."""

    def __init__(self, poll_id: str, options: list[str | None]) -> None:
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.options = options

        for i, opt in enumerate(options, start=1):
            if opt:
                self.add_item(PollButton(label=opt, option=i, poll_id=poll_id))

        self.add_item(RemoveVoteButton(poll_id=poll_id))


class PollModal(discord.ui.Modal, title="Create a Poll"):
    """Modal used by the slash variant of ``/poll``."""

    question = discord.ui.TextInput(label="Question?", required=True)
    option1 = discord.ui.TextInput(label="Option 1", required=True)
    option2 = discord.ui.TextInput(label="Option 2", required=True)
    option3 = discord.ui.TextInput(label="Option 3 (optional)", required=False)
    option4 = discord.ui.TextInput(label="Option 4 (optional)", required=False)
    option5 = discord.ui.TextInput(label="Option 5 (optional)", required=False)
    channel = discord.ui.TextInput(label="Channel ID to post in?", required=True)
    duration = discord.ui.TextInput(label="Duration? (e.g. 10m, 2h)", required=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            post_channel = interaction.client.get_channel(int(self.channel.value))
            duration_seconds = parse_time(self.duration.value)
            end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

            options = [
                self.option1.value,
                self.option2.value,
                self.option3.value,
                self.option4.value,
                self.option5.value,
            ]
            poll_id = str(interaction.id)

            counts: dict[str, int] = {}
            view = PollView(poll_id, options)
            embed = build_poll_embed(
                self.question.value, options, counts,
                closed=False, duration=self.duration.value,
            )

            msg = await post_channel.send(embed=embed, view=view)

            await polls_col.insert_one(
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

            await interaction.response.send_message("✅ Poll created!", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(
                f"⚠️ Error: {exc}", ephemeral=True
            )


__all__ = [
    "build_poll_embed",
    "PollButton",
    "RemoveVoteButton",
    "PollView",
    "PollModal",
]
