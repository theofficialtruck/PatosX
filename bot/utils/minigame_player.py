"""Persistence for the casino mini-games' per-player stats document."""

from __future__ import annotations

from bot.database import minigameplayerdata_col


async def get_player(uid: str) -> dict:
    """Return the player's mini-game record, inserting defaults if missing."""
    player = await minigameplayerdata_col.find_one({"_id": uid})
    if not player:
        player = {
            "_id": uid,
            "wallet": 0,
            "games_played": 0,
            "games_won": 0,
            "games_lost": 0,
        }
        await minigameplayerdata_col.insert_one(player)
    return player


async def update_player(uid: str, update: dict) -> None:
    """Set arbitrary fields on a player document."""
    await minigameplayerdata_col.update_one(
        {"_id": uid}, {"$set": update}, upsert=True
    )


async def inc_player(uid: str, update: dict) -> None:
    """``$inc`` arbitrary fields on a player document."""
    await minigameplayerdata_col.update_one(
        {"_id": uid}, {"$inc": update}, upsert=True
    )


async def ensure_user(uid: str) -> None:
    """Insert the bare-bones bet-history document if not present."""
    if not await is_registered(uid):
        await minigameplayerdata_col.insert_one(
            {"_id": uid, "wins": 0, "losses": 0, "bets": []}
        )


async def is_registered(uid: str) -> bool:
    return (await minigameplayerdata_col.find_one({"_id": uid})) is not None


async def add_bet(uid: str, bet: int, win: int) -> None:
    await minigameplayerdata_col.update_one(
        {"_id": uid},
        {"$push": {"bets": {"bet": bet, "win": win}}},
        upsert=True,
    )


async def update_game_stats(uid: str, result: str) -> None:
    if result == "win":
        await minigameplayerdata_col.update_one(
            {"_id": uid}, {"$inc": {"wins": 1}}, upsert=True
        )
    elif result == "loss":
        await minigameplayerdata_col.update_one(
            {"_id": uid}, {"$inc": {"losses": 1}}, upsert=True
        )


__all__ = [
    "get_player",
    "update_player",
    "inc_player",
    "ensure_user",
    "is_registered",
    "add_bet",
    "update_game_stats",
]
