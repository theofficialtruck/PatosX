"""Pagination view for the ``transcriptlist`` command."""

from __future__ import annotations

from datetime import datetime

import discord


class TranscriptPaginationView(discord.ui.View):
    """Browse open and closed tickets with prev/next pagination."""

    def __init__(self, ctx, tickets, per_page: int = 25) -> None:
        super().__init__(timeout=120)
        self.ctx = ctx
        self.tickets = tickets
        self.per_page = per_page
        self.page = 0
        self.max_page = max(0, (len(tickets) - 1) // per_page)
        self.message: discord.Message | None = None

    @staticmethod
    def format_time(dt, style: str = "both") -> str:
        if not dt:
            return "Unknown"
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError:
                return str(dt)
        if not isinstance(dt, datetime):
            return "Unknown"
        ts = int(dt.timestamp())
        if style == "full":
            return f"<t:{ts}:F>"
        if style == "short":
            return f"<t:{ts}:f>"
        if style == "relative":
            return f"<t:{ts}:R>"
        return f"<t:{ts}:f> • <t:{ts}:R>"

    async def format_user(self, user_id) -> str:
        if not user_id or user_id == "unknown":
            return "Unknown"
        try:
            user_id_int = int(user_id)
            user = (
                self.ctx.bot.get_user(user_id_int)
                or await self.ctx.bot.fetch_user(user_id_int)
            )
            return user.mention if user else "Unknown"
        except Exception:
            return "Unknown"

    async def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.tickets[start:end]

        embed = discord.Embed(
            title=(
                f"📜 Ticket Overview ({len(self.tickets)} total) - "
                f"Page {self.page + 1}/{self.max_page + 1}"
            ),
            color=discord.Color.blurple(),
        )

        for ticket in chunk:
            ticket_id = ticket.get("ticket_id", "Unknown")
            opener = await self.format_user(ticket.get("opener_id"))
            opened_at = self.format_time(ticket.get("created_at"), "both")

            status = (
                f"🟢 Ongoing\nOpened by: {opener}\nOpened at: {opened_at}"
            )

            if ticket.get("closed_at"):
                closer = await self.format_user(ticket.get("closer_id"))
                closed_at = self.format_time(ticket.get("closed_at"), "both")
                status = (
                    "🔴 Closed\n"
                    f"Opened by: {opener}\nOpened at: {opened_at}\n"
                    f"Closed by: {closer}\nClosed at: {closed_at}"
                )

            embed.add_field(
                name=f"🎟 Ticket {ticket_id}",
                value=status,
                inline=False,
            )

        return embed

    @discord.ui.button(label="⬅ Prev", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can use this.",
                ephemeral=True,
            )
        self.page = max(0, self.page - 1)
        self.children[0].disabled = self.page == 0
        self.children[1].disabled = self.page == self.max_page
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next ➡", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can use this.",
                ephemeral=True,
            )
        self.page = min(self.max_page, self.page + 1)
        self.children[0].disabled = self.page == 0
        self.children[1].disabled = self.page == self.max_page
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    async def on_timeout(self) -> None:  # pragma: no cover
        if self.message:
            try:
                await self.message.delete()
            except discord.NotFound:
                pass


__all__ = ["TranscriptPaginationView"]
