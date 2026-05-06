"""Leaderboard views for the economy and quack-counter commands."""

from __future__ import annotations

import discord
from discord.ui import View

from bot.database import economy_col, xp_col


class LeaderboardView(discord.ui.View):
    """Toggle between Money and XP leaderboards."""

    def __init__(self, ctx) -> None:
        super().__init__(timeout=60)
        self.ctx = ctx

    @discord.ui.button(label="Money", style=discord.ButtonStyle.primary)
    async def money_lb(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "❌ You can't use this button!", ephemeral=True
            )
        embed = await self.get_money_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="XP", style=discord.ButtonStyle.secondary)
    async def xp_lb(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "❌ You can't use this button!", ephemeral=True
            )
        embed = await self.get_xp_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def get_money_embed(self) -> discord.Embed:
        cursor = economy_col.find({"guild": str(self.ctx.guild.id)})
        users: list[tuple[int, int]] = []
        async for doc in cursor:
            total = doc.get("wallet", 0) + doc.get("bank", 0)
            if "user" in doc:
                users.append((int(doc["user"]), total))

        users.sort(key=lambda kv: kv[1], reverse=True)

        embed = discord.Embed(
            title="🏆 Leaderboard - Richest Users", color=discord.Color.teal()
        )
        for i, (uid, total) in enumerate(users[:10], start=1):
            member = self.ctx.guild.get_member(uid)
            name = member.display_name if member else f"Unknown User ({uid})"
            embed.add_field(name=f"#{i} {name}", value=f"🪙 {total} coins", inline=False)

        rank = next(
            (i + 1 for i, (uid, _) in enumerate(users) if uid == self.ctx.author.id),
            None,
        )
        user_total = next(
            (total for uid, total in users if uid == self.ctx.author.id), 0
        )
        if rank:
            embed.set_footer(text=f"Your Rank: #{rank} • 🪙 {user_total} coins")
        return embed

    async def get_xp_embed(self) -> discord.Embed:
        cursor = xp_col.find({"guild": str(self.ctx.guild.id)})
        users: list[tuple[int, int]] = []
        async for doc in cursor:
            xp = doc.get("xp", 0)
            if "user" in doc:
                users.append((int(doc["user"]), xp))

        users.sort(key=lambda kv: kv[1], reverse=True)

        embed = discord.Embed(
            title="🏆 Leaderboard - Most XP", color=discord.Color.gold()
        )
        for i, (uid, xp) in enumerate(users[:10], start=1):
            member = self.ctx.guild.get_member(uid)
            name = member.display_name if member else f"Unknown User ({uid})"
            embed.add_field(name=f"#{i} {name}", value=f"⭐ {xp} XP", inline=False)

        rank = next(
            (i + 1 for i, (uid, _) in enumerate(users) if uid == self.ctx.author.id),
            None,
        )
        user_xp = next(
            (xp for uid, xp in users if uid == self.ctx.author.id), 0
        )
        if rank:
            embed.set_footer(text=f"Your Rank: #{rank} • ⭐ {user_xp} XP")
        return embed


class QuackTopView(View):
    """Pagination for the ``/quacktop`` leaderboard."""

    def __init__(self, ctx, entries: list[tuple[str, int]], per_page: int = 10) -> None:
        super().__init__(timeout=None)
        self.ctx = ctx
        self.entries = entries
        self.per_page = per_page
        self.page = 0
        self.max_page = max(0, (len(entries) - 1) // per_page)

        self.user_id = str(ctx.author.id)
        self.user_rank: int | None = None
        for index, (uid, _) in enumerate(entries, start=1):
            if uid == self.user_id:
                self.user_rank = index
                break

    def get_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        description = ""
        for index, (user_id, count) in enumerate(self.entries[start:end], start=start + 1):
            member = self.ctx.guild.get_member(int(user_id))
            name = member.display_name if member else f"User ID {user_id}"
            description += f"**{index}. {name}** — {count} quacks\n"

        embed = discord.Embed(
            title=f"🦆 Quack Leaderboard (Page {self.page + 1}/{self.max_page + 1})",
            description=description or "No data.",
            color=discord.Color.green(),
        )
        if self.user_rank:
            embed.set_footer(text=f"Your rank: #{self.user_rank}")
        else:
            embed.set_footer(text="You haven't quacked yet!")
        return embed

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.blurple)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page < self.max_page:
            self.page += 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)


__all__ = ["LeaderboardView", "QuackTopView"]
