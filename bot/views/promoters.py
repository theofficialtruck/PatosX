"""Pagination view for the ``promoters`` vanity-roles list."""

from __future__ import annotations

import discord
from discord.ui import View


class PromotersView(View):
    """Paginate the list of users currently wearing the vanity role."""

    def __init__(self, ctx, mentions, per_page: int = 10) -> None:
        super().__init__(timeout=60)
        self.ctx = ctx
        self.mentions = mentions
        self.per_page = per_page
        self.page = 0
        self.message: discord.Message | None = None
        self._update_buttons()

    def _get_page_data(self) -> list[str]:
        start = self.page * self.per_page
        return self.mentions[start:start + self.per_page]

    def make_embed(self) -> discord.Embed:
        total_pages = max(
            1, (len(self.mentions) + self.per_page - 1) // self.per_page
        )
        desc = "\n".join(self._get_page_data()) or "None"
        embed = discord.Embed(
            title="📢 Current Promoters",
            description=desc,
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages}")
        return embed

    def _update_buttons(self) -> None:
        total_pages = max(
            1, (len(self.mentions) + self.per_page - 1) // self.per_page
        )
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= total_pages - 1

    async def _disable_all(self, interaction=None, message=None) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if interaction:
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
        elif message:
            await message.edit(embed=self.make_embed(), view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ You can't control this menu.", ephemeral=True
            )
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ You can't control this menu.", ephemeral=True
            )
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    async def on_timeout(self) -> None:  # pragma: no cover
        if self.message is not None:
            await self._disable_all(message=self.message)


__all__ = ["PromotersView"]
