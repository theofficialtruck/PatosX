"""Pure helpers for the casino mini-games (Mines, Towers, etc.).

Kept side-effect-free so the math is easy to test in isolation.
"""

from __future__ import annotations

import math
import random


def calculate_mines_multiplier(
    minesamount: int, diamonds: int, houseedge: float
) -> float:
    """Standard Mines payout formula with a configurable house edge."""

    def n_choose_r(n: int, r: int) -> int:
        if r > n or r < 0:
            return 0
        f = math.factorial
        return f(n) // f(r) // f(n - r)

    if minesamount >= 25:
        return 1.0
    denominator = n_choose_r(25 - minesamount, diamonds)
    if denominator == 0:
        return 1.0
    return (1 - houseedge) * n_choose_r(25, diamonds) / denominator


def generate_board(minesa: int) -> list[list[str]]:
    """Build a 5x5 board with ``minesa`` mines randomly placed."""
    board = [["s" for _ in range(5)] for _ in range(5)]
    placed = 0
    while placed < minesa:
        row = random.randint(0, 4)
        col = random.randint(0, 4)
        if board[row][col] == "s":
            board[row][col] = "m"
            placed += 1
    return board


def get_towers_stake_multi(layer: int, difficulty: str) -> float:
    """Return the multiplier for a Duck Towers layer, by difficulty."""
    multipliers = {
        "Easy": [1.10, 1.25, 1.45, 1.70, 2.00],
        "Medium": [1.30, 1.60, 2.00, 2.50, 3.20],
        "Hard": [1.50, 2.00, 2.80, 4.00, 6.00],
    }
    base = multipliers.get(difficulty.capitalize(), multipliers["Easy"])
    return base[layer] if layer < len(base) else base[-1]


# Per-button cooldowns for the door minigame; keyed by (guild_id, user_id).
button_cooldowns: dict[tuple, float] = {}


__all__ = [
    "calculate_mines_multiplier",
    "generate_board",
    "get_towers_stake_multi",
    "button_cooldowns",
]
