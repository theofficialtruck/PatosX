"""Casino mini-game UI: Mines, Door Game, Duck Towers."""

from __future__ import annotations

import asyncio
import random
import time

import discord
from discord import ButtonStyle, Embed, SelectOption, ui

from bot.utils.economy import (
    add_balance,
    subtract_balance,
    update_user_balance,
)
from bot.utils.games import (
    button_cooldowns,
    calculate_mines_multiplier,
    generate_board,
    get_towers_stake_multi,
)
from bot.utils.minigame_player import (
    add_bet,
    inc_player,
    update_game_stats,
)
from bot.utils.numbers import add_suffix, format_with_suffix


# --- Mines ----------------------------------------------------------------

class MinesButtons(ui.View):
    """5x5 board view used by the Mines mini-game."""

    def __init__(
        self,
        board,
        bombs,
        bet,
        userboard,
        usersafes,
        interaction,
        exploded,
        house_edge,
        message=None,
    ) -> None:
        super().__init__(timeout=None)
        self.board = board
        self.bombs = bombs
        self.bet = bet
        self.userboard = userboard
        self.usersafes = usersafes
        self.interaction = interaction
        self.exploded = exploded
        self.has_cashed_out = False
        self.max_safe_tiles = 25 - bombs
        self.house_edge = house_edge
        self.message = message
        self.setup_buttons()

    def setup_buttons(self) -> None:
        self.clear_items()
        for row in range(5):
            for col in range(5):
                square = (
                    self.userboard[row][col]
                    if not self.exploded
                    else self.board[row][col]
                )
                custom_id = f"{row} {col}"

                if not self.exploded:
                    if square == "":
                        btn = ui.Button(
                            label="​",
                            custom_id=custom_id,
                            style=ButtonStyle.gray,
                        )
                        btn.callback = self.button_callback
                    elif square == "s":
                        btn = ui.Button(
                            label="",
                            custom_id=custom_id,
                            style=ButtonStyle.green,
                            emoji="<:Mines:1432423463141900319>",
                        )
                        btn.callback = self.button_cashout
                    elif square == "m":
                        btn = ui.Button(
                            label="",
                            custom_id=custom_id,
                            style=ButtonStyle.red,
                            emoji="<:bomb:1432424251574587503>",
                        )
                        btn.callback = self.button_cashout
                else:
                    if self.board[row][col] == "s":
                        btn = ui.Button(
                            label="",
                            custom_id=custom_id,
                            style=ButtonStyle.green,
                            emoji="<:Mines:1432423463141900319>",
                        )
                    elif self.board[row][col] == "m":
                        btn = ui.Button(
                            label="",
                            custom_id=custom_id,
                            style=ButtonStyle.red,
                            emoji="<:bomb:1432424251574587503>",
                        )
                    else:
                        btn = ui.Button(
                            label="​",
                            custom_id=custom_id,
                            style=ButtonStyle.gray,
                        )
                    btn.disabled = True

                self.add_item(btn)

    async def button_cashout(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer()

        row, col = map(int, interaction.data["custom_id"].split())

        if self.has_cashed_out:
            await interaction.followup.send(
                "❌ You already cashed out!", ephemeral=True
            )
            return

        self.has_cashed_out = True

        multi = round(
            calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge), 2
        )
        winnings = round(self.bet * multi)
        await update_user_balance(interaction.user.id, interaction.guild.id, winnings)
        await inc_player(
            str(interaction.user.id), {"games_played": 1, "games_won": 1}
        )

        embed = Embed(color=0x57FF5A, title=f":bomb: {self.bombs} Mines Cashed Out")
        next_multi = round(
            calculate_mines_multiplier(
                self.bombs, self.usersafes + 1, self.house_edge
            ),
            2,
        )
        next_winnings = round(self.bet * next_multi)
        embed.add_field(
            name="Stats",
            value=(
                f"💎 Bet: {format_with_suffix(self.bet)}\n"
                f"💰 Winnings: {format_with_suffix(winnings)}\n"
                f"📈 Multiplier: {multi}x\n"
                f"⏱ Next Click: {format_with_suffix(next_winnings)}"
            ),
        )

        self.exploded = True
        self.userboard[row][col] = "s"
        self.setup_buttons()
        await self.message.edit(embed=embed, view=self)

    async def button_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return

        await interaction.response.defer()
        row, col = map(int, interaction.data["custom_id"].split())

        if self.userboard[row][col] != "":
            return

        if self.board[row][col] == "s":
            self.userboard[row][col] = "s"
            self.usersafes += 1

            multi = round(
                calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge),
                2,
            )
            next_multi = round(
                calculate_mines_multiplier(
                    self.bombs, self.usersafes + 1, self.house_edge
                ),
                2,
            )
            next_winnings = round(self.bet * next_multi)

            embed = Embed(color=0xFFA500, title=f":bomb: {self.bombs} Mines")
            embed.add_field(
                name="Stats",
                value=(
                    f"💎 Bet: {format_with_suffix(self.bet)}\n"
                    f"💰 Winnings: {format_with_suffix(round(self.bet * multi))}\n"
                    f"📈 Multiplier: {multi}x\n"
                    f"⏱ Next Click: {format_with_suffix(next_winnings)}"
                ),
            )

            self.setup_buttons()
            await self.message.edit(embed=embed, view=self)

            if self.usersafes >= self.max_safe_tiles:
                await self.button_cashout(interaction)

        elif self.board[row][col] == "m":
            self.userboard[row][col] = "m"
            self.exploded = True
            await inc_player(
                str(interaction.user.id), {"games_played": 1, "games_lost": 1}
            )

            embed = Embed(color=0xF53232, title=f":bomb: {self.bombs} Mines Exploded!")
            multi = round(
                calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge),
                2,
            )
            embed.add_field(
                name="Stats",
                value=(
                    f"💎 Bet: {format_with_suffix(self.bet)}\n"
                    f"💰 Lost: {format_with_suffix(round(self.bet * multi))}\n"
                    f"📉 Multiplier: {multi}x"
                ),
            )

            self.setup_buttons()
            await self.message.edit(embed=embed, view=self)


class MinesBombSelect(ui.Select):
    """Pick the number of mines (1-24) before the board is generated."""

    def __init__(self, ctx, bet, house_edge) -> None:
        self.ctx = ctx
        self.bet = bet
        self.house_edge = house_edge
        options = [SelectOption(label=str(i), description=f"{i} bombs") for i in range(1, 25)]
        super().__init__(
            placeholder="Select number of bombs",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return

        bombs = int(self.values[0])
        board = generate_board(bombs)
        userboard = [["" for _ in range(5)] for _ in range(5)]

        embed = Embed(color=0xFFA500, title=f":bomb: {bombs} Mines")
        embed.add_field(
            name="Stats",
            value=(
                f"💎 Bet: {format_with_suffix(self.bet)}\n"
                f"💰 Winnings: {format_with_suffix(self.bet)}\n"
                f"📈 Multiplier: 1.00x\n"
                f"⏱ Next Click: {format_with_suffix(self.bet)}"
            ),
        )

        view = MinesButtons(
            board, bombs, self.bet, userboard, 0, interaction, False, self.house_edge
        )
        await interaction.response.defer()
        game_message = await interaction.followup.send(embed=embed, view=view)
        view.message = game_message


# --- Door Game ------------------------------------------------------------

class DoorGameButton(discord.ui.View):
    """Three-door view used inside the Door Game flow."""

    def __init__(
        self,
        ctx,
        uid,
        guild_id,
        bet,
        total_doors,
        current_door,
        current_balance,
    ) -> None:
        super().__init__(timeout=None)
        self.ctx = ctx
        self.uid = uid
        self.guild_id = guild_id
        self.bet = bet
        self.total_doors = total_doors
        self.current_door = current_door
        self.current_balance = current_balance
        self._add_buttons()

    def _add_buttons(self) -> None:
        for i in range(1, 4):
            button = discord.ui.Button(
                label=f"🚪 Door {i}",
                style=discord.ButtonStyle.blurple,
                custom_id=str(i),
            )
            button.callback = self._door_clicked
            self.add_item(button)

    async def _door_clicked(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.uid:
            return await interaction.response.send_message(
                "❌ This isn’t your game!", ephemeral=True
            )

        current_time = time.time()
        key = (self.guild_id, self.uid)
        last_click_time = button_cooldowns.get(key, 0)
        if current_time - last_click_time < 2:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="⏳ Cooldown",
                    description="Please wait **2 seconds** before clicking again!",
                    color=0xFF0000,
                ),
                ephemeral=True,
            )
        button_cooldowns[key] = current_time

        await interaction.response.defer()

        chosen_door = int(interaction.data["custom_id"])
        stage = self.current_door

        lose_chance = min(30 + (stage * 10), 80)
        half_chance = min(50 + (stage * 5), 90)
        win_chance = max(100 - (lose_chance + half_chance), 5)

        weighted_outcomes = (
            ["x3"] * win_chance
            + ["0.5x"] * half_chance
            + ["0x"] * lose_chance
        )
        outcome = random.choice(weighted_outcomes)

        if outcome == "x3":
            await add_balance(int(self.uid), int(self.guild_id), self.bet * 3)
            self.current_balance += self.bet * 3
            result_text = (
                f"🎉 **Door {chosen_door} tripled your bet!**\n"
                f"You now have `{add_suffix(self.current_balance)}` coins!"
            )
            color = 0x4DFF58
        elif outcome == "0.5x":
            await add_balance(int(self.uid), int(self.guild_id), int(self.bet * 0.5))
            self.current_balance += int(self.bet * 0.5)
            result_text = (
                f"😅 **Door {chosen_door} gave half back.**\n"
                f"You now have `{add_suffix(self.current_balance)}` coins."
            )
            color = 0xFFF93D
        else:
            result_text = (
                f"💀 **Door {chosen_door} took your bet!**\n"
                f"You now have `{add_suffix(max(self.current_balance - self.bet, 0))}` coins."
            )
            color = 0xFF6B6B

            embed = discord.Embed(
                title=f"🚪 Door {self.current_door}/{self.total_doors} Result",
                description=result_text,
                color=color,
            )
            embed.add_field(
                name="Final Result",
                value="💀 You lost your bet! Game over!",
                inline=False,
            )
            embed.set_footer(text=f"Played by {interaction.user.name}")

            for child in self.children:
                child.disabled = True

            self.stop()
            await subtract_balance(int(self.uid), int(self.guild_id), self.bet)

            await interaction.edit_original_response(embed=embed, view=self)
            return

        for child in self.children:
            child.disabled = True

        game_over = self.current_door >= self.total_doors
        if game_over:
            final_msg = (
                f"🏁 **Game Over!** You finished with "
                f"`{add_suffix(self.current_balance)}` coins!"
            )
            embed = discord.Embed(
                title=f"🚪 Door {self.current_door}/{self.total_doors} Result",
                description=result_text,
                color=color,
            )
            embed.add_field(name="Final Result", value=final_msg, inline=False)
            embed.set_footer(text=f"Played by {interaction.user.name}")
            self.stop()
            await interaction.edit_original_response(embed=embed, view=self)
            return

        next_door = self.current_door + 1
        next_view = DoorGameButton(
            self.ctx,
            self.uid,
            self.guild_id,
            self.bet,
            self.total_doors,
            next_door,
            self.current_balance,
        )
        next_embed = discord.Embed(
            title=f"🚪 Door {next_door}/{self.total_doors}",
            description="Choose your next door wisely...",
            color=0xFFA500,
        )
        next_embed.add_field(
            name="Current Balance",
            value=f"🪙 `{add_suffix(self.current_balance)}`",
            inline=True,
        )
        next_embed.set_footer(text="It gets harder each door...")
        await interaction.edit_original_response(embed=next_embed, view=next_view)


class DoorCountSelect(discord.ui.Select):
    """Pick how many doors to walk through."""

    def __init__(self, ctx, bet) -> None:
        self.ctx = ctx
        self.bet = bet
        self.bet_start_balance = 0
        options = [
            discord.SelectOption(label=str(i), description=f"Go through {i} doors")
            for i in range(1, 6)
        ]
        super().__init__(
            placeholder="Select how many doors to go through...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ This isn’t your game!", ephemeral=True
            )

        doors = int(self.values[0])
        await interaction.response.defer()

        for child in self.view.children:
            child.disabled = True

        embed = discord.Embed(
            title=f"🚪 Door Game — {doors} Doors",
            description=(
                f"You’ve bet **{add_suffix(self.bet)} coins** and will go through "
                f"**{doors} doors.**\n\n"
                "Each door gets harder — more risk, less reward. Good luck!"
            ),
            color=0xFFA500,
        )
        embed.set_footer(text="Click a door to begin your journey!")

        self.view.stop()

        await interaction.edit_original_response(
            embed=embed,
            view=DoorGameButton(
                self.ctx,
                str(self.ctx.author.id),
                str(self.ctx.guild.id),
                self.bet,
                doors,
                1,
                self.bet_start_balance,
            ),
        )

    async def set_start_balance(self, balance: int) -> None:
        self.bet_start_balance = balance


# --- Duck Towers ----------------------------------------------------------

class DifficultySelect(discord.ui.Select):
    def __init__(self, ctx, bet) -> None:
        self.ctx = ctx
        self.bet = bet
        options = [
            discord.SelectOption(label="Easy", description="Low risk, low reward 🟢"),
            discord.SelectOption(label="Medium", description="Balanced challenge 🟡"),
            discord.SelectOption(label="Hard", description="High risk, high reward 🔴"),
        ]
        super().__init__(
            placeholder="🦆 Choose your difficulty...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "❌ This isn’t your game!", ephemeral=True
            )
            return

        difficulty = self.values[0].capitalize()

        await interaction.response.defer()
        await subtract_balance(self.ctx.author.id, self.ctx.guild.id, self.bet)

        embed = discord.Embed(
            title="🦆 **Duck Towers**",
            description=(
                f"**Difficulty:** {difficulty}\n"
                f"**Bet:** {add_suffix(self.bet)}\n"
                f"**Multiplier:** 1.00x → {get_towers_stake_multi(0, difficulty)}x\n"
                f"**Potential:** "
                f"{add_suffix(round(self.bet * get_towers_stake_multi(0, difficulty)))}"
            ),
            color=0x3471EB,
        )
        embed.set_footer(text="Click a tile to begin!")

        view = DuckTowersView(self.ctx, self.bet, difficulty)
        view.message = await interaction.followup.send(embed=embed, view=view)


class DuckTowersView(discord.ui.View):
    """Layered tower view used by the ``ducktowers`` minigame."""

    def __init__(self, ctx, bet, difficulty) -> None:
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bet = bet
        self.difficulty = difficulty.capitalize()
        self.layer = 0
        self.multi = 1
        self.safe_towers: list[list[int]] = []
        self.has_cashed_out = False
        self.buttons: list[list[discord.ui.Button]] = []
        self.message: discord.Message | None = None
        self._setup_buttons()

    def _setup_buttons(self) -> None:
        difficulty_settings = {
            "Easy": (4, 3),
            "Medium": (3, 2),
            "Hard": (3, 1),
        }
        towers_per_layer, safe_towers_per_layer = difficulty_settings[self.difficulty]

        for layer in range(5):
            safe_positions = random.sample(range(towers_per_layer), safe_towers_per_layer)
            self.safe_towers.append(safe_positions)
            row = 4 - layer
            layer_buttons: list[discord.ui.Button] = []
            for tower in range(towers_per_layer):
                btn = discord.ui.Button(
                    label="‎",
                    custom_id=f"{layer} {tower}",
                    style=discord.ButtonStyle.gray,
                    row=row,
                )
                btn.callback = self._tower_clicked
                if layer != 0:
                    btn.disabled = True
                    btn.style = discord.ButtonStyle.blurple
                layer_buttons.append(btn)
                self.add_item(btn)
            self.buttons.append(layer_buttons)

    async def _update_embed(self) -> None:
        next_multi = get_towers_stake_multi(self.layer, self.difficulty)
        potential = round(self.bet * next_multi)
        embed = discord.Embed(
            title="🦆 **Duck Towers**",
            description=(
                f"**Difficulty:** {self.difficulty}\n"
                f"**Bet:** {add_suffix(self.bet)}\n"
                f"**Multiplier:** {self.multi}x → {next_multi}x\n"
                f"**Potential:** {add_suffix(potential)}"
            ),
            color=0x3471EB,
        )
        embed.set_footer(text="Click a tile to continue!")
        await self.message.edit(embed=embed, view=self)

    async def _tower_clicked(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "❌ This isn't your game!", ephemeral=True
            )
            return
        await interaction.response.defer()

        layer, tower = map(int, interaction.data["custom_id"].split())
        if layer != self.layer:
            return

        is_safe = tower in self.safe_towers[layer]
        if not is_safe:
            self.buttons[layer][tower].emoji = "🥚"
            self.buttons[layer][tower].style = discord.ButtonStyle.red
            for group in self.buttons:
                for b in group:
                    b.disabled = True
            await self.message.edit(view=self)
            await asyncio.sleep(1.5)
            await add_bet(str(interaction.user.id), self.bet, 0)
            await update_game_stats(str(interaction.user.id), "loss")
            lose_embed = discord.Embed(
                title="💥 Game Over",
                description=(
                    f"**Bet:** {add_suffix(self.bet)}\n"
                    f"**Multiplier:** {self.multi}x\n"
                    f"**Winnings:** 0"
                ),
                color=0xFF0000,
            )
            lose_embed.set_footer(text="Try again!")
            await self.message.edit(embed=lose_embed, view=self)
            return

        self.buttons[layer][tower].emoji = "🦆"
        self.buttons[layer][tower].style = discord.ButtonStyle.green
        self.multi = get_towers_stake_multi(layer, self.difficulty)
        self.buttons[layer][tower].callback = self._cash_out
        if layer < 4:
            for b in self.buttons[layer + 1]:
                b.disabled = False
                b.style = discord.ButtonStyle.gray
        self.layer += 1
        if self.layer == 5:
            await self._cash_out(interaction)
            return
        await self._update_embed()

    async def _cash_out(self, interaction: discord.Interaction) -> None:
        if self.has_cashed_out:
            return
        self.has_cashed_out = True
        winnings = round(self.bet * self.multi)
        await add_balance(interaction.user.id, self.ctx.guild.id, winnings)
        await add_bet(str(interaction.user.id), self.bet, winnings)
        await update_game_stats(str(interaction.user.id), "win")
        for row in self.buttons:
            for b in row:
                b.disabled = True
        embed = discord.Embed(
            title="💰 Cashed Out!",
            description=(
                f"**Bet:** {add_suffix(self.bet)}\n"
                f"**Winnings:** {add_suffix(winnings)}\n"
                f"**Multiplier:** {self.multi}x"
            ),
            color=0x00FF00,
        )
        embed.set_footer(text="Thanks for playing!")
        await self.message.edit(embed=embed, view=self)


__all__ = [
    "MinesButtons",
    "MinesBombSelect",
    "DoorGameButton",
    "DoorCountSelect",
    "DifficultySelect",
    "DuckTowersView",
]
