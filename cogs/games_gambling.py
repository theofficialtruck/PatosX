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

"""Gambling and skill minigames: coinflip, duckroll, lottery, doorgame, mines, ducktowers, riddle, duckquiz."""

import asyncio
import json
import math
import random
import re
import time
import traceback
from datetime import datetime, timedelta, timezone

import discord
from discord import (
    ButtonStyle,
    Embed,
    SelectOption,
    app_commands,
    ui,
)
from discord.ext import commands

from core import config as cfg
from core import economyHelperFuncs as econ
from core import errors, state
from core import permsHelperFuncs as perms
from core import xpHelperFuncs as xp
from data.duckquiz_questions import questions

button_cooldowns = {}


class DoorCountSelect(discord.ui.Select):
    def __init__(self, ctx, bet):
        self.ctx = ctx
        self.bet = bet
        self.bet_start_balance = 0
        options = [discord.SelectOption(label=str(i), description=f"Go through {i} doors") for i in range(1, 6)]
        super().__init__(
            placeholder="Select how many doors to go through...", options=options, min_values=1, max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
        doors = int(self.values[0])
        await interaction.response.defer()
        for child in self.view.children:
            child.disabled = True
        embed = discord.Embed(
            title=f"🚪 Door Game - {doors} Doors",
            description=(
                f"You've bet **{econ.add_suffix(self.bet)} coins** and will go through **{doors} doors.**\n\n"
                "Each door gets harder - more risk, less reward. Good luck!"
            ),
            color=16753920,
        )
        embed.set_footer(text="Click a door to begin your journey!")
        self.view.stop()
        await interaction.edit_original_response(
            embed=embed,
            view=DoorGameButton(
                self.ctx, str(self.ctx.author.id), str(self.ctx.guild.id), self.bet, doors, 1, self.bet_start_balance
            ),
        )

    async def set_start_balance(self, balance: int):
        self.bet_start_balance = balance


class DoorGameButton(discord.ui.View):
    def __init__(self, ctx, uid, guild_id, bet, total_doors, current_door, current_balance):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.uid = uid
        self.guild_id = guild_id
        self.bet = bet
        self.total_doors = total_doors
        self.current_door = current_door
        self.current_balance = current_balance
        self.add_buttons()

    def add_buttons(self):
        for i in range(1, 4):
            button = discord.ui.Button(label=f"🚪 Door {i}", style=discord.ButtonStyle.blurple, custom_id=str(i))
            button.callback = self.door_clicked
            self.add_item(button)

    async def door_clicked(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.uid:
            return await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
        current_time = time.time()
        key = (self.guild_id, self.uid)
        last_click_time = button_cooldowns.get(key, 0)
        if current_time - last_click_time < 2:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="⏳ Cooldown", description="Please wait **2 seconds** before clicking again!", color=16711680
                ),
                ephemeral=True,
            )
        button_cooldowns[key] = current_time
        await interaction.response.defer()
        chosen_door = int(interaction.data["custom_id"])
        stage = self.current_door
        lose_chance = min(30 + stage * 10, 80)
        half_chance = min(50 + stage * 5, 90)
        win_chance = max(100 - (lose_chance + half_chance), 5)
        weighted_outcomes = ["x3"] * win_chance + ["0.5x"] * half_chance + ["0x"] * lose_chance
        outcome = random.choice(weighted_outcomes)
        if outcome == "x3":
            await econ.add_balance(int(self.uid), int(self.guild_id), self.bet * 3)
            self.current_balance += self.bet * 3
            result_text = f"🎉 **Door {chosen_door} tripled your bet!**\nYou now have `{econ.add_suffix(self.current_balance)}` coins!"
            color = 5111640
        elif outcome == "0.5x":
            await econ.add_balance(int(self.uid), int(self.guild_id), int(self.bet * 0.5))
            self.current_balance += int(self.bet * 0.5)
            result_text = f"😅 **Door {chosen_door} gave half back.**\nYou now have `{econ.add_suffix(self.current_balance)}` coins."
            color = 16775485
        else:
            # The bet was already taken when the game started, so a loss just ends the game.
            result_text = f"💀 **Door {chosen_door} took your bet!**\nYou now have `{econ.add_suffix(self.current_balance)}` coins."
            color = 16739179
            embed = discord.Embed(
                title=f"🚪 Door {self.current_door}/{self.total_doors} Result", description=result_text, color=color
            )
            embed.add_field(name="Final Result", value="💀 You lost your bet! Game over!", inline=False)
            embed.set_footer(text=f"Played by {interaction.user.name}")
            for child in self.children:
                child.disabled = True
            self.stop()
            await interaction.edit_original_response(embed=embed, view=self)
            return
        for child in self.children:
            child.disabled = True
        game_over = self.current_door >= self.total_doors
        if game_over:
            final_msg = f"🏁 **Game Over!** You finished with `{econ.add_suffix(self.current_balance)}` coins!"
            embed = discord.Embed(
                title=f"🚪 Door {self.current_door}/{self.total_doors} Result", description=result_text, color=color
            )
            embed.add_field(name="Final Result", value=final_msg, inline=False)
            embed.set_footer(text=f"Played by {interaction.user.name}")
            self.stop()
            await interaction.edit_original_response(embed=embed, view=self)
            return
        next_door = self.current_door + 1
        next_view = DoorGameButton(
            self.ctx, self.uid, self.guild_id, self.bet, self.total_doors, next_door, self.current_balance
        )
        next_embed = discord.Embed(
            title=f"🚪 Door {next_door}/{self.total_doors}",
            description="Choose your next door wisely...",
            color=16753920,
        )
        next_embed.add_field(name="Current Balance", value=f"🪙 `{econ.add_suffix(self.current_balance)}`", inline=True)
        next_embed.set_footer(text="It gets harder each door...")
        await interaction.edit_original_response(embed=next_embed, view=next_view)


def calculate_mines_multiplier(minesamount: int, diamonds: int, houseedge: float) -> float:
    def nCr(n: int, r: int) -> int:
        if r > n or r < 0:
            return 0
        f = math.factorial
        return f(n) // f(r) // f(n - r)

    if minesamount >= 25:
        return 1.0
    denominator = nCr(25 - minesamount, diamonds)
    if denominator == 0:
        return 1.0
    return (1 - houseedge) * nCr(25, diamonds) / denominator


def format_with_suffix(amount: float) -> str:
    if amount >= 1000000000:
        return f"{round(amount / 1000000000, 1)}B"
    elif amount >= 1000000:
        return f"{round(amount / 1000000, 1)}M"
    elif amount >= 1000:
        return f"{round(amount / 1000, 1)}K"
    else:
        return str(round(amount, 1))


def generate_board(minesa: int) -> list[list[str]]:
    board = [["s" for _ in range(5)] for _ in range(5)]
    for _ in range(minesa):
        placed = False
        while not placed:
            row = random.randint(0, 4)
            col = random.randint(0, 4)
            if board[row][col] == "s":
                board[row][col] = "m"
                placed = True
    return board


async def get_player(uid: str):
    player = await state.minigameplayerdata_col.find_one({"_id": uid})
    if not player:
        player = {"_id": uid, "wallet": 0, "games_played": 0, "games_won": 0, "games_lost": 0}
        await state.minigameplayerdata_col.insert_one(player)
    return player


async def update_player(uid: str, update: dict):
    await state.minigameplayerdata_col.update_one({"_id": uid}, {"$set": update}, upsert=True)


async def inc_player(uid: str, update: dict):
    await state.minigameplayerdata_col.update_one({"_id": uid}, {"$inc": update}, upsert=True)


class MinesButtons(ui.View):
    def __init__(self, board, bombs, bet, userboard, usersafes, interaction, exploded, house_edge, message=None):
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

    def setup_buttons(self):
        self.clear_items()
        for row in range(5):
            for col in range(5):
                square = self.userboard[row][col] if not self.exploded else self.board[row][col]
                custom_id = f"{row} {col}"
                if not self.exploded:
                    if square == "":
                        btn = ui.Button(label="\u200b", custom_id=custom_id, style=ButtonStyle.gray)
                        btn.callback = self.button_callback
                    elif square == "s":
                        btn = ui.Button(
                            label="", custom_id=custom_id, style=ButtonStyle.green, emoji="<:Mines:1432423463141900319>"
                        )
                        btn.callback = self.button_cashout
                    elif square == "m":
                        btn = ui.Button(
                            label="", custom_id=custom_id, style=ButtonStyle.red, emoji="<:bomb:1432424251574587503>"
                        )
                        btn.callback = self.button_cashout
                else:
                    if self.board[row][col] == "s":
                        btn = ui.Button(
                            label="", custom_id=custom_id, style=ButtonStyle.green, emoji="<:Mines:1432423463141900319>"
                        )
                    elif self.board[row][col] == "m":
                        btn = ui.Button(
                            label="", custom_id=custom_id, style=ButtonStyle.red, emoji="<:bomb:1432424251574587503>"
                        )
                    else:
                        btn = ui.Button(label="\u200b", custom_id=custom_id, style=ButtonStyle.gray)
                    btn.disabled = True
                self.add_item(btn)

    async def button_cashout(self, interaction: discord.Interaction):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        row, col = map(int, interaction.data["custom_id"].split())
        if self.has_cashed_out:
            await interaction.followup.send("❌ You already cashed out!", ephemeral=True)
            return
        self.has_cashed_out = True
        multi = round(calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge), 2)
        winnings = round(self.bet * multi)
        await econ.update_user_balance(interaction.user.id, interaction.guild.id, winnings)
        await inc_player(str(interaction.user.id), {"games_played": 1, "games_won": 1})
        embed = Embed(color=5767002, title=f":bomb: {self.bombs} Mines Cashed Out")
        next_multi = round(calculate_mines_multiplier(self.bombs, self.usersafes + 1, self.house_edge), 2)
        next_winnings = round(self.bet * next_multi)
        embed.add_field(
            name="Stats",
            value=f"💎 Bet: {format_with_suffix(self.bet)}\n💰 Winnings: {format_with_suffix(winnings)}\n📈 Multiplier: {multi}x\n⏱ Next Click: {format_with_suffix(next_winnings)}",
        )
        self.exploded = True
        self.userboard[row][col] = "s"
        self.setup_buttons()
        await self.message.edit(embed=embed, view=self)

    async def button_callback(self, interaction: discord.Interaction):
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
            multi = round(calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge), 2)
            next_multi = round(calculate_mines_multiplier(self.bombs, self.usersafes + 1, self.house_edge), 2)
            next_winnings = round(self.bet * next_multi)
            embed = Embed(color=16753920, title=f":bomb: {self.bombs} Mines")
            embed.add_field(
                name="Stats",
                value=f"💎 Bet: {format_with_suffix(self.bet)}\n💰 Winnings: {format_with_suffix(round(self.bet * multi))}\n📈 Multiplier: {multi}x\n⏱ Next Click: {format_with_suffix(next_winnings)}",
            )
            self.setup_buttons()
            await self.message.edit(embed=embed, view=self)
            if self.usersafes >= self.max_safe_tiles:
                await self.button_cashout(interaction)
        elif self.board[row][col] == "m":
            self.userboard[row][col] = "m"
            self.exploded = True
            await inc_player(str(interaction.user.id), {"games_played": 1, "games_lost": 1})
            embed = Embed(color=16069170, title=f":bomb: {self.bombs} Mines Exploded!")
            multi = round(calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge), 2)
            embed.add_field(
                name="Stats",
                value=f"💎 Bet: {format_with_suffix(self.bet)}\n💰 Lost: {format_with_suffix(round(self.bet * multi))}\n📉 Multiplier: {multi}x",
            )
            self.setup_buttons()
            await self.message.edit(embed=embed, view=self)


class MinesBombSelect(ui.Select):
    def __init__(self, ctx, bet, house_edge):
        self.ctx = ctx
        self.bet = bet
        self.house_edge = house_edge
        options = [SelectOption(label=str(i), description=f"{i} bombs") for i in range(1, 25)]
        super().__init__(placeholder="Select number of bombs", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return
        bombs = int(self.values[0])
        board = generate_board(bombs)
        userboard = [["" for _ in range(5)] for _ in range(5)]
        embed = Embed(color=16753920, title=f":bomb: {bombs} Mines")
        embed.add_field(
            name="Stats",
            value=f"💎 Bet: {format_with_suffix(self.bet)}\n💰 Winnings: {format_with_suffix(self.bet)}\n📈 Multiplier: 1.00x\n⏱ Next Click: {format_with_suffix(self.bet)}",
        )
        view = MinesButtons(board, bombs, self.bet, userboard, 0, interaction, False, self.house_edge)
        await interaction.response.defer()
        game_message = await interaction.followup.send(embed=embed, view=view)
        view.message = game_message


async def ensure_user(uid: str):
    if not await is_registered(uid):
        await state.minigameplayerdata_col.insert_one({"_id": uid, "wins": 0, "losses": 0, "bets": []})


async def is_registered(uid: str) -> bool:
    user = await state.minigameplayerdata_col.find_one({"_id": uid})
    return user is not None


async def add_bet(uid: str, bet: int, win: int):
    await state.minigameplayerdata_col.update_one(
        {"_id": uid}, {"$push": {"bets": {"bet": bet, "win": win}}}, upsert=True
    )


async def update_game_stats(uid: str, result: str):
    if result == "win":
        await state.minigameplayerdata_col.update_one({"_id": uid}, {"$inc": {"wins": 1}}, upsert=True)
    elif result == "loss":
        await state.minigameplayerdata_col.update_one({"_id": uid}, {"$inc": {"losses": 1}}, upsert=True)


def get_towers_stake_multi(layer, difficulty):
    multipliers = {
        "Easy": [1.1, 1.25, 1.45, 1.7, 2.0],
        "Medium": [1.3, 1.6, 2.0, 2.5, 3.2],
        "Hard": [1.5, 2.0, 2.8, 4.0, 6.0],
    }
    base = multipliers.get(difficulty.capitalize(), multipliers["Easy"])
    return base[layer] if layer < len(base) else base[-1]


class DifficultySelect(discord.ui.Select):
    def __init__(self, ctx, bet):
        self.ctx = ctx
        self.bet = bet
        options = [
            discord.SelectOption(label="Easy", description="Low risk, low reward 🟢"),
            discord.SelectOption(label="Medium", description="Balanced challenge 🟡"),
            discord.SelectOption(label="Hard", description="High risk, high reward 🔴"),
        ]
        super().__init__(placeholder="🦆 Choose your difficulty...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
            return
        difficulty = self.values[0].capitalize()
        await interaction.response.defer()
        await econ.subtract_balance(self.ctx.author.id, self.ctx.guild.id, self.bet)
        embed = discord.Embed(
            title="🦆 **Duck Towers**",
            description=f"**Difficulty:** {difficulty}\n**Bet:** {econ.add_suffix(self.bet)}\n**Multiplier:** 1.00x -> {get_towers_stake_multi(0, difficulty)}x\n**Potential:** {econ.add_suffix(round(self.bet * get_towers_stake_multi(0, difficulty)))}",
            color=3437035,
        )
        embed.set_footer(text="Click a tile to begin!")
        view = DuckTowersView(self.ctx, self.bet, difficulty)
        view.message = await interaction.followup.send(embed=embed, view=view)


class DuckTowersView(discord.ui.View):
    def __init__(self, ctx, bet, difficulty):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bet = bet
        self.difficulty = difficulty.capitalize()
        self.layer = 0
        self.multi = 1
        self.safe_towers = []
        self.has_cashed_out = False
        self.buttons = []
        self.message = None
        self.setup_buttons()

    def setup_buttons(self):
        difficulty_settings = {"Easy": (4, 3), "Medium": (3, 2), "Hard": (3, 1)}
        towers_per_layer, safe_towers_per_layer = difficulty_settings[self.difficulty]
        for layer in range(5):
            safe_positions = random.sample(range(towers_per_layer), safe_towers_per_layer)
            self.safe_towers.append(safe_positions)
            row = 4 - layer
            layer_buttons = []
            for tower in range(towers_per_layer):
                btn = discord.ui.Button(
                    label="\u200e", custom_id=f"{layer} {tower}", style=discord.ButtonStyle.gray, row=row
                )
                btn.callback = self.tower_clicked
                if layer != 0:
                    btn.disabled = True
                    btn.style = discord.ButtonStyle.blurple
                layer_buttons.append(btn)
                self.add_item(btn)
            self.buttons.append(layer_buttons)

    async def update_embed(self):
        next_multi = get_towers_stake_multi(self.layer, self.difficulty)
        potential = round(self.bet * next_multi)
        embed = discord.Embed(
            title="🦆 **Duck Towers**",
            description=f"**Difficulty:** {self.difficulty}\n**Bet:** {econ.add_suffix(self.bet)}\n**Multiplier:** {self.multi}x -> {next_multi}x\n**Potential:** {econ.add_suffix(potential)}",
            color=3437035,
        )
        embed.set_footer(text="Click a tile to continue!")
        await self.message.edit(embed=embed, view=self)

    async def tower_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
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
                description=f"**Bet:** {econ.add_suffix(self.bet)}\n**Multiplier:** {self.multi}x\n**Winnings:** 0",
                color=16711680,
            )
            lose_embed.set_footer(text="Try again!")
            await self.message.edit(embed=lose_embed, view=self)
            return
        self.buttons[layer][tower].emoji = "🦆"
        self.buttons[layer][tower].style = discord.ButtonStyle.green
        self.multi = get_towers_stake_multi(layer, self.difficulty)
        self.buttons[layer][tower].callback = self.cash_out
        if layer < 4:
            for b in self.buttons[layer + 1]:
                b.disabled = False
                b.style = discord.ButtonStyle.gray
        self.layer += 1
        if self.layer == 5:
            await self.cash_out(interaction)
            return
        await self.update_embed()

    async def cash_out(self, interaction: discord.Interaction):
        if self.has_cashed_out:
            return
        self.has_cashed_out = True
        winnings = round(self.bet * self.multi)
        await econ.add_balance(interaction.user.id, self.ctx.guild.id, winnings)
        await add_bet(str(interaction.user.id), self.bet, winnings)
        await update_game_stats(str(interaction.user.id), "win")
        for row in self.buttons:
            for b in row:
                b.disabled = True
        embed = discord.Embed(
            title="💰 Cashed Out!",
            description=f"**Bet:** {econ.add_suffix(self.bet)}\n**Winnings:** {econ.add_suffix(winnings)}\n**Multiplier:** {self.multi}x",
            color=65280,
        )
        embed.set_footer(text="Thanks for playing!")
        await self.message.edit(embed=embed, view=self)


def _normalize_riddle_answer(text: str) -> str:
    """Lowercase, strip a leading article, and drop trailing punctuation so answers like
    'It's an egg!' and 'egg' compare equal."""
    text = (text or "").strip().lower()
    text = re.sub(r"^(a|an|the)\s+", "", text)
    text = re.sub(r"[.!?,'\"]+$", "", text)
    return text.strip()


def _riddle_answer_matches(user_text: str, accepted_answers: list) -> bool:
    """Return True if user_text matches any accepted answer, either exactly or as a whole
    word/phrase within a longer reply (e.g. 'I think it's a piano' still matches 'piano')."""
    normalized_user = _normalize_riddle_answer(user_text)
    if not normalized_user:
        return False
    for accepted in accepted_answers:
        normalized_accepted = _normalize_riddle_answer(accepted)
        if not normalized_accepted:
            continue
        if normalized_user == normalized_accepted:
            return True
        if re.search(rf"\b{re.escape(normalized_accepted)}\b", normalized_user):
            return True
    return False


class RiddleLevelView(discord.ui.View):
    """Lets the command invoker pick a riddle difficulty via buttons before the riddle is shown."""

    def __init__(self, ctx):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.choice = None
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This riddle prompt isn't yours.", ephemeral=True)
            return False
        return True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    async def _pick(self, interaction: discord.Interaction, level: str):
        self.choice = level
        self._disable_all()
        label = cfg.RIDDLE_LEVELS[level]["label"]
        await interaction.response.edit_message(
            content=f"🧩 Level selected: **{label}**. Here's your riddle...", view=self
        )
        self.stop()

    @discord.ui.button(label="Easy", style=discord.ButtonStyle.green, emoji="🟢")
    async def easy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "easy")

    @discord.ui.button(label="Medium", style=discord.ButtonStyle.blurple, emoji="🟡")
    async def medium_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "medium")

    @discord.ui.button(label="Hard", style=discord.ButtonStyle.red, emoji="🔴")
    async def hard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "hard")

    async def on_timeout(self):
        if self.choice is None and self.message is not None:
            self._disable_all()
            try:
                await self.message.edit(
                    content="⏰ You didn't pick a difficulty in time. Run the command again.", view=self
                )
            except (discord.HTTPException, discord.NotFound):
                pass


class AnswerButton(discord.ui.Button):
    def __init__(self, label: str, value: int, parent_view):
        super().__init__(style=discord.ButtonStyle.primary, label=label, custom_id=str(value))
        self.value = value
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message("This quiz isn't yours.", ephemeral=True)
        idx = view.current_index
        if view.answered_ids.get(idx):
            return await interaction.response.send_message("You already answered this question.", ephemeral=True)
        view.answered_ids[idx] = True
        correct_answer = view.questions[idx]["answer"]
        if self.value == correct_answer:
            view.score += 1
        view.disable_all_buttons()
        await interaction.response.edit_message(view=view)
        reply = (
            "✅ Correct!"
            if self.value == correct_answer
            else f"❌ Wrong! Answer was: {view.questions[idx]['options'][correct_answer - 1]}"
        )
        await interaction.followup.send(reply, ephemeral=True)
        view.current_index += 1
        await view.show_next(interaction)


class QuizView(discord.ui.View):
    def __init__(self, ctx, quiz_id, questions_list):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.user_id = ctx.author.id
        self.quiz_id = quiz_id
        self.questions = questions_list
        self.current_index = 0
        self.score = 0
        self.answered_ids = {}
        for i in range(1, 5):
            self.add_item(AnswerButton(str(i), i, self))

    def disable_all_buttons(self):
        for item in self.children:
            item.disabled = True

    async def show_next(self, interaction: discord.Interaction = None):
        if self.current_index >= len(self.questions):
            await self.finish_quiz(interaction)
            return
        q = self.questions[self.current_index]
        opts = "\n".join((f"{i + 1}. {opt}" for i, opt in enumerate(q["options"])))
        embed = discord.Embed(
            title=f"Question {self.current_index + 1}/{len(self.questions)}",
            description=q["q"],
            color=discord.Color.teal(),
        )
        embed.add_field(name="Options", value=opts, inline=False)
        embed.set_footer(text="Click a button below to answer.")
        self.clear_items()
        for i in range(1, 5):
            self.add_item(AnswerButton(str(i), i, self))
        if interaction:
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        else:
            await self.ctx.send(embed=embed, view=self, ephemeral=True)

    async def finish_quiz(self, interaction: discord.Interaction = None):
        pct = self.score / len(self.questions) * 100.0
        passed = pct >= cfg.PASS_PCT
        await state.quiz_col.update_one(
            {"_id": self.quiz_id},
            {"$set": {"score": self.score, "completed": datetime.now(timezone.utc), "passed": passed}},
        )
        result = f"📊 You scored **{self.score}/{len(self.questions)}** = **{pct:.1f}%**"
        if passed:
            await econ.increment_monthly_goal(str(self.ctx.guild.id), str(self.ctx.author.id), "quiz_passes", 1)
            await econ.check_and_award_monthly_rewards(self.ctx, self.ctx.guild, self.ctx.author)
            config = await state.config_col.find_one({"guild": str(self.ctx.guild.id)}) or {}
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except Exception:
                    config = {}
            if not isinstance(config, dict):
                config = {}
            raw_role_ids = config.get("ROLE_ID")
            if not raw_role_ids:
                result += "\n⚠️ Staff have not provided a role to award."
            else:
                role_ids = []
                if isinstance(raw_role_ids, int):
                    role_ids = [raw_role_ids]
                elif isinstance(raw_role_ids, str) and raw_role_ids.isdigit():
                    role_ids = [int(raw_role_ids)]
                elif isinstance(raw_role_ids, list):
                    role_ids = [int(r) for r in raw_role_ids if str(r).isdigit()]
                roles_to_add = [self.ctx.guild.get_role(rid) for rid in role_ids if self.ctx.guild.get_role(rid)]
                if roles_to_add:
                    await self.ctx.author.add_roles(*roles_to_add, reason="Passed duck quiz")
                    role_names = ", ".join([r.name for r in roles_to_add])
                    result += f"\n🎉 You passed and earned the **{role_names}** role!"
                else:
                    result += "\n⚠️ Role configured, but could not find it on the server."
        if interaction:
            await interaction.followup.send(result, ephemeral=True)
        else:
            await self.ctx.send(result)
        self.stop()


class GamesGambling(commands.Cog):
    """Gambling and skill minigames: coinflip, duckroll, lottery, doorgame, mines, ducktowers, riddle, duckquiz."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="coinflip", description="Coin flip for coins.", aliases=["cf"])
    @app_commands.describe(amount="Amount to bet (number or 'all')")
    @perms.blacklist_barrier()
    async def coinflip(self, ctx, amount: str):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
        wallet = data.get("wallet", 0)
        if amount.lower() == "all":
            amount = wallet
        else:
            try:
                amount = int(amount)
            except ValueError:
                return await ctx.send("❌ Please enter a valid number or `all`.")
        if amount <= 0:
            return await ctx.send("❌ Invalid amount to coin flip.")
        if amount > wallet:
            return await ctx.send("❌ You can't afford that!")
        luck_buff = data.get("luck_buff", False)
        base_chance = 0.5
        adjusted_chance = base_chance
        if luck_buff:
            await state.economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"}, {"$unset": {"luck_buff": ""}}
            )
        won = random.random() < adjusted_chance
        if won:
            await econ.add_balance(ctx.author.id, ctx.guild.id, amount)
            msg = f"🎉 You won {amount} coins from flipping a coin!"
            await ctx.send(msg)
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "coinflip_wins")
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "coinflip_win_streak")
        else:
            await econ.subtract_balance(ctx.author.id, ctx.guild.id, amount)
            await ctx.send(f"💸 You lost {amount} coins from flipping a coin.")
            await state.badges_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"}, {"$set": {"counters.coinflip_win_streak": 0}}, upsert=True
            )
        fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
        await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)

    @coinflip.error
    async def coinflip_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await errors.send_hybrid_error(ctx, content="❌ You must specify an amount (number or `all`).")
        elif errors.is_discord_service_unavailable_error(error):
            await errors.send_hybrid_error(ctx, content=errors.DISCORD_SERVICE_UNAVAILABLE_MESSAGE)
        else:
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(name="duckroll", description="Guess if the ducks are higher or lower than 50!")
    @perms.blacklist_barrier()
    @xp.xp_earn(10, 20)
    async def duckroll(self, ctx, guess: str):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data.get("wallet", 0)
            guess = guess.lower()
            if guess not in ["high", "low"]:
                return await ctx.send("❌ Invalid choice! Use `.duckroll high` or `.duckroll low`.")
            bet_amount = 150
            if wallet < bet_amount:
                return await ctx.send("❌ You don't have enough coins to play duckroll! (Need at least 150)")
            roll = random.randint(1, 100)
            if roll > 50 and guess == "high" or (roll < 50 and guess == "low"):
                await econ.add_balance(ctx.author.id, ctx.guild.id, bet_amount)
                msg = f"🦆 You rolled **{roll} ducks**!\n✅ Correct guess! You won **{bet_amount} coins** 🎉"
                await ctx.send(msg)
            elif roll == 50:
                await ctx.send("🦆 You rolled exactly **50 ducks**!\n🤷 It's a draw. No win, no loss.")
            else:
                await econ.subtract_balance(ctx.author.id, ctx.guild.id, bet_amount)
                await ctx.send(f"🦆 You rolled **{roll} ducks**!\n❌ Wrong guess! You lost **{bet_amount} coins** 💸")
        except Exception as e:
            await ctx.send(
                "⚠️ Something went wrong while processing your duckroll. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )
            print(f"[ERROR] duckroll command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @commands.hybrid_command(name="lottery", description="Join the lottery.")
    @perms.blacklist_barrier()
    @xp.xp_earn(10, 20)
    async def lottery(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
        now = datetime.now(timezone.utc)
        last_time = data.get("last_lottery")
        if last_time:
            last_time = datetime.fromisoformat(last_time)
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            if now - last_time < timedelta(hours=1):
                rem = timedelta(hours=1) - (now - last_time)
                return await ctx.send(f"🕒 You can try the lottery again in {rem.seconds // 60}m {rem.seconds % 60}s.")
        ticket_price = 300
        jackpot = random.randint(15000, 20000)
        base_chance = 0.05
        if data["wallet"] < ticket_price:
            return await ctx.send("🎟️ You need at least 300 coins to buy a lottery ticket.")
        inventory = data.get("inventory", [])
        luck_boost = 1.0
        for i, item in enumerate(inventory):
            if isinstance(item, dict) and item.get("_id") == "pet_duck":
                luck_boost = 1.3
                item["uses_left"] -= 1
                await ctx.send("🦆 Your Pet Duck boosted your lottery luck by 30%!")
                if item["uses_left"] <= 0:
                    inventory.pop(i)
                    await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
                break
        nitro_used, nitro_expired = econ.consume_nitro_boost(inventory)
        nitro_reduction_seconds = 0
        if nitro_used:
            nitro_reduction_seconds = int(3600 * cfg.NITRO_BOOST_COOLDOWN_REDUCTION_PCT)
            await ctx.send(
                f"🚀 Your Nitro Boost cut your next lottery cooldown by {nitro_reduction_seconds // 60} minutes!"
            )
            if nitro_expired:
                await ctx.send("💨 Your Nitro Boost ran out after 3 uses.")
        chance = base_chance * luck_boost
        data["wallet"] -= ticket_price
        await state.economy_col.update_one({"_id": user_id}, {"$set": {"wallet": data["wallet"]}})
        win = random.random() <= chance
        if win:
            await econ.add_balance(ctx.author.id, ctx.guild.id, jackpot)
            msg = f"🎉 You hit the jackpot and won **{jackpot} coins**!"
            await ctx.send(msg)
        else:
            await ctx.send("😢 No luck this time. Better luck next draw!")
        data["inventory"] = inventory
        effective_lottery_ts = now - timedelta(seconds=nitro_reduction_seconds)
        await state.economy_col.update_one(
            {"_id": user_id}, {"$set": {"inventory": inventory, "last_lottery": effective_lottery_ts.isoformat()}}
        )

    @lottery.error
    async def lottery_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            rem = timedelta(seconds=error.retry_after)
            mins = rem.seconds // 60
            secs = rem.seconds % 60
            return await errors.send_hybrid_error(ctx, content=f"🕒 Try again in {mins}m {secs}s.")

    @commands.hybrid_command(name="doorgame", description="Try your luck through multiple doors!")
    @commands.cooldown(1, 5, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(12, 24)
    async def doorgame(self, ctx):
        try:
            await ctx.send("💰 Please type your **bet amount** (e.g. `100`, `1k`, `1.5m`):")

            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            try:
                msg = await self.bot.wait_for("message", check=check, timeout=30.0)
            except asyncio.TimeoutError:
                return await ctx.send("⌛ You took too long to respond. The game has been cancelled.")
            bet_str = msg.content.strip()
            try:
                bet = econ.suffix_to_int(bet_str)
            except ValueError:
                return await ctx.send("❌ Invalid bet amount! Please enter a number like `100`, `1k`, or `1.5m`.")
            uid = str(ctx.author.id)
            guild_id = str(ctx.guild.id)
            user_doc = await state.economy_col.find_one({"_id": f"{guild_id}-{uid}"})
            wallet = user_doc.get("wallet", 0) if user_doc else 0
            if wallet < bet:
                return await ctx.send("❌ You don't have enough coins for that bet!")
            await state.economy_col.update_one({"_id": f"{guild_id}-{uid}"}, {"$inc": {"wallet": -bet}}, upsert=True)
            user_doc = await state.economy_col.find_one({"_id": f"{guild_id}-{uid}"})
            current_balance = user_doc.get("wallet", 0)
            select = DoorCountSelect(ctx, bet)
            await select.set_start_balance(current_balance)

            class DoorCountView(discord.ui.View):
                def __init__(self, ctx, select):
                    super().__init__(timeout=30.0)
                    self.ctx = ctx
                    self.add_item(select)
                    self.message = None

                async def on_timeout(self):
                    for child in self.children:
                        child.disabled = True
                    try:
                        await self.message.edit(
                            content="⌛ You didn't select a door count in time. Game cancelled.", embed=None, view=self
                        )
                    except discord.Forbidden:
                        await self.ctx.send("⌛ You didn't select a door count in time. Game cancelled.")

            embed = discord.Embed(
                title="🚪 Door Game Setup",
                description=f"Your bet: **{econ.add_suffix(bet)} coins**\nCurrent Balance: `{econ.add_suffix(current_balance)}`\n\nNow choose how many doors you want to go through:",
                color=16753920,
            )
            view = DoorCountView(ctx, select)
            bot_msg = await ctx.send(embed=embed, view=view)
            view.message = bot_msg
        except Exception as e:
            await ctx.send(
                "⚠️ Something went wrong while setting up the game. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )
            print(f"[ERROR] doorgame setup: {type(e).__name__} - {e}")
            traceback.print_exc()

    @commands.hybrid_command(name="mines", description="Play Mines and test your luck!")
    @perms.blacklist_barrier()
    @xp.xp_earn(12, 24)
    async def mines(self, ctx):
        await ctx.send("💎 How much would you like to bet? (Type a number or 'all')")

        def check_bet(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            bet_msg = await self.bot.wait_for("message", check=check_bet, timeout=60.0)
        except asyncio.TimeoutError:
            await ctx.send("⏱ You took too long to respond. Command cancelled.")
            return
        bet_input = bet_msg.content.strip()
        uid = ctx.author.id
        guild_id = ctx.guild.id
        user_balance = await econ.get_balance(uid, guild_id)
        if bet_input.lower() == "all":
            bet = user_balance
        else:
            cleaned_bet = bet_input.replace(",", "").replace("$", "").strip().lower()
            if not any(ch.isdigit() for ch in cleaned_bet):
                await ctx.send("❌ Please enter a valid number (like `100`, `1k`, or `all`).")
                return
            try:
                bet = econ.suffix_to_int(cleaned_bet)
            except ValueError:
                await ctx.send("❌ Invalid bet format! Try something like `500`, `1k`, or `2m`.")
                return
        if bet <= 0:
            await ctx.send("❌ Bet must be greater than 0.")
            return
        if bet > user_balance:
            await ctx.send("💎 You don't have enough balance for that bet!")
            return
        await econ.update_user_balance(uid, guild_id, -bet)
        house_edge = 0.15
        select = MinesBombSelect(ctx, bet, house_edge)
        view = ui.View()
        view.add_item(select)
        await ctx.send("🧨 Choose the number of bombs:", view=view)

    @commands.hybrid_command(name="ducktowers", description="Play a game of Duck Towers!")
    @commands.cooldown(1, 15, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(12, 24)
    async def ducktowers(self, ctx):
        try:
            uid = ctx.author.id
            guild_id = ctx.guild.id
            await ensure_user(str(uid))
            await ctx.send("💎 How much would you like to bet? (Type a number, or 'all')")

            def check_bet(m):
                return m.author == ctx.author and m.channel == ctx.channel

            bet_msg = await self.bot.wait_for("message", check=check_bet, timeout=60.0)
            bet_input = bet_msg.content.strip()
            user_balance = await econ.get_balance(uid, guild_id)
            bet = user_balance if bet_input.lower() == "all" else econ.suffix_to_int(bet_input)
            if bet <= 0:
                return await ctx.send("❌ Bet must be greater than zero.")
            if bet > user_balance:
                return await ctx.send(f"💎 You only have `{econ.add_suffix(user_balance)}`, not enough for that bet!")
            select = DifficultySelect(ctx, bet)
            view = discord.ui.View()
            view.add_item(select)
            await ctx.send("🦆 Choose your difficulty:", view=view)
        except asyncio.TimeoutError:
            await ctx.send("⌛ You took too long to respond - game canceled.")
        except ValueError:
            await ctx.send("⚠️ Invalid bet amount. Try again using a number or 'all'.")
        except Exception as e:
            await ctx.send("⚠️ Something went wrong while starting your Duck Towers game.")
            print(f"[ERROR] ducktowers command: {type(e).__name__} - {e}")

    @commands.hybrid_command(
        name="riddle", description="Answer a riddle correctly within the time limit to earn coins."
    )
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(10, 20)
    async def riddle(self, ctx):
        """Let the user pick a difficulty, pose a random riddle from that difficulty's bank, then
        wait for their answer within the difficulty's time limit. A correct answer pays out a
        random amount of coins from the difficulty's reward range; a wrong or missed answer pays
        nothing and, via xp.xp_earn's failure-message detection, awards no XP either."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            view = RiddleLevelView(ctx)
            prompt_msg = await ctx.send(
                "🧩 Pick a difficulty for your riddle:\n"
                "🟢 **Easy** - smaller reward, more time to answer\n"
                "🟡 **Medium** - bigger reward, less time to answer\n"
                "🔴 **Hard** - biggest reward, least time to answer",
                view=view,
            )
            view.message = prompt_msg
            await view.wait()
            if view.choice is None:
                ctx._skip_xp_award = True
                ctx.command.reset_cooldown(ctx)
                return
            level = view.choice
            level_data = cfg.RIDDLE_LEVELS[level]
            riddle_data = random.choice(cfg.RIDDLES[level])
            min_reward, max_reward = level_data["reward_range"]
            time_limit = level_data["time_limit"]
            embed = discord.Embed(
                title=f"{level_data['emoji']} {level_data['label']} Riddle",
                description=riddle_data["question"],
                color=discord.Color.teal(),
            )
            embed.set_footer(text=f"You have {time_limit} seconds to answer. Reward: {min_reward}-{max_reward} coins.")
            await ctx.send(embed=embed)

            def answer_check(m):
                return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

            try:
                answer_msg = await self.bot.wait_for("message", timeout=time_limit, check=answer_check)
            except asyncio.TimeoutError:
                return await ctx.send(
                    f"⏰ Time's up! The answer was **{riddle_data['answers'][0]}**. No coins this time."
                )

            if _riddle_answer_matches(answer_msg.content, riddle_data["answers"]):
                reward = random.randint(min_reward, max_reward)
                await econ.add_balance(ctx.author.id, ctx.guild.id, reward)
                await econ.increment_monthly_goal(str(ctx.guild.id), str(ctx.author.id), "riddles_solved", 1)
                await econ.check_and_award_monthly_rewards(ctx, ctx.guild, ctx.author)
                await ctx.send(f"✅ Correct! You earned **{reward} coins**! 🎉")
                fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
                await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
            else:
                await ctx.send(
                    f"❌ Wrong answer! The correct answer was **{riddle_data['answers'][0]}**. No coins this time."
                )
        except Exception as e:
            ctx.command.reset_cooldown(ctx)
            print(f"[ERROR] riddle command: {type(e).__name__} - {e}")
            traceback.print_exc()
            await ctx.send("⚠️ Something went wrong with your riddle. Please contact " + cfg.BOT_ADMIN_NAME + ".")

    @riddle.error
    async def riddle_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return await errors.send_hybrid_error(
                ctx, content=f"🕒 You can try another riddle in {minutes}m {seconds}s."
            )
        else:
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(name="duckquiz", description="Standardized Duck Quiz.")
    @perms.blacklist_barrier()
    async def duckquiz(self, ctx):
        cfg_raw = await state.config_col.find_one({"guild": str(ctx.guild.id)}) or {}
        if isinstance(cfg_raw, str):
            try:
                cfg_raw = json.loads(cfg_raw)
            except Exception:
                cfg_raw = {}
        if not isinstance(cfg_raw, dict):
            cfg_raw = {}
        quiz_channels = cfg_raw.get("QUIZ_CHANNEL")
        if isinstance(quiz_channels, str) and quiz_channels.isdigit():
            quiz_channels = [int(quiz_channels)]
        elif isinstance(quiz_channels, list):
            quiz_channels = [int(x) for x in quiz_channels if str(x).isdigit()]
        else:
            quiz_channels = []
        if quiz_channels and ctx.channel.id not in quiz_channels:
            mention = f"<#{quiz_channels[0]}>" if quiz_channels else "`a quiz channel`"
            return await ctx.send(f"❌ Please use this command in {mention}.")
        USER, GUILD = (str(ctx.author.id), str(ctx.guild.id))
        now = datetime.now(timezone.utc)
        role_ids = cfg_raw.get("ROLE_ID", [])
        if isinstance(role_ids, int):
            role_ids = [role_ids]
        elif isinstance(role_ids, str) and role_ids.isdigit():
            role_ids = [int(role_ids)]
        user_roles = [r.id for r in ctx.author.roles]
        if any(rid in user_roles for rid in role_ids):
            await ctx.send("ℹ You've already passed; type `yes` within 30s to retake.")
            try:
                msg = await self.bot.wait_for(
                    "message", timeout=30, check=lambda m: m.author == ctx.author and m.channel == ctx.channel
                )
                if msg.content.strip().lower() != "yes":
                    return await ctx.send("✅ Quiz cancelled.")
            except asyncio.TimeoutError:
                return await ctx.send("⌛ Timed out - quiz cancelled.")
        user_doc = await state.quiz_col.find_one({"guild": GUILD, "user": USER})
        last_use = user_doc.get("last_quiz") if user_doc else None
        if last_use:
            last_dt = datetime.fromisoformat(last_use).replace(tzinfo=timezone.utc)
            if now - last_dt < timedelta(hours=1):
                remaining = timedelta(hours=1) - (now - last_dt)
                mins = int(remaining.total_seconds() // 60)
                return await ctx.send(f"🕒 You can take the quiz again in {mins} minute(s).")
        used = await state.quiz_col.distinct("qid", {"guild": GUILD, "used": True})
        pool = [q for q in questions if isinstance(q.get("id"), (int, str)) and q["id"] not in used]
        if len(pool) < cfg.NUM_Q:
            await state.quiz_col.update_many({"guild": GUILD}, {"$unset": {"used": ""}})
            pool = [q for q in questions if isinstance(q.get("id"), (int, str))]
        selected = random.sample(pool, cfg.NUM_Q)
        quiz_doc = {
            "guild": GUILD,
            "user": USER,
            "started": now,
            "questions": [q["id"] for q in selected],
            "answers": {},
            "score": 0,
            "completed": None,
            "passed": False,
        }
        res = await state.quiz_col.insert_one(quiz_doc)
        await state.quiz_col.update_one(
            {"guild": GUILD, "user": USER}, {"$set": {"last_quiz": now.isoformat()}}, upsert=True
        )
        view = QuizView(ctx, res.inserted_id, selected)
        await view.show_next()

    @duckquiz.error
    async def duckquiz_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            mins = int(error.retry_after // 60)
            await errors.send_hybrid_error(
                ctx, content=f"🕒 Please wait another **{mins} minute(s)** before taking the quiz again."
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await errors.send_hybrid_error(
                ctx,
                content="❌ Missing arguments, type the quiz command without additional input (no parameters required).",
            )
        elif isinstance(error, commands.CheckFailure):
            await errors.send_hybrid_error(ctx, content="❌ You can't use this command right now.")
        else:
            root_error = errors.unwrap_command_error(error)
            if isinstance(root_error, commands.CommandOnCooldown):
                return await errors.send_hybrid_error(
                    ctx, content=f"❌ You are on cooldown. Try again in {root_error.retry_after:.2f}s"
                )
            print(f"[ERROR] duckquiz_error: {root_error}")
            traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GamesGambling(bot))
