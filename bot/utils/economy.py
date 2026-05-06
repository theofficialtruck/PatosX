"""Economy helpers — wallet/bank manipulation and food-item buffs.

Every function here mutates ``economy_col`` directly so cogs can stay free
of repeated boilerplate ``update_one(...)`` calls. The shape of the user
document is fixed by ``get_user`` — when a new field is needed system-wide
it should be added there *and* to ``defaults``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bot.database import economy_col


async def get_user(ctx, guild_id, user_id) -> dict:
    """Return the user's economy document, inserting defaults if missing.

    Backfills any field that is missing from a pre-existing document so the
    rest of the bot can rely on a stable shape.
    """
    guild_id_s = str(guild_id)
    user_id_s = str(user_id)
    key = f"{guild_id_s}-{user_id_s}"

    defaults: dict[str, Any] = {
        "_id": key,
        "guild": guild_id_s,
        "user": user_id_s,
        "wallet": 0,
        "bank": 0,
        "inventory": [],
        "job": None,
        "job_start": None,
        "promoted": False,
        "last_beg": None,
        "last_fished": None,
        "last_daily": None,
        "daily_streak": 0,
        "fish_count": 0,
        "hunt_count": 0,
        "mine_count": 0,
        "dig_count": 0,
        "bugcatch_count": 0,
    }

    user = await economy_col.find_one({"_id": key})
    if not user:
        await economy_col.insert_one(defaults)
        return defaults

    updated = False
    for field, default_value in defaults.items():
        if field not in user:
            user[field] = default_value
            updated = True

    if updated:
        await economy_col.update_one({"_id": key}, {"$set": user})

    return user


async def get_balance(uid: int, guild_id: int) -> int:
    """Return the wallet (not bank) balance for a user."""
    data = await economy_col.find_one({"_id": f"{guild_id}-{uid}"})
    return data.get("wallet", 0) if data else 0


async def add_balance(uid: int, guild_id: int, amount: int) -> None:
    """Increment the wallet by ``amount`` (negative values are allowed)."""
    await economy_col.update_one(
        {"_id": f"{guild_id}-{uid}"},
        {"$inc": {"wallet": amount}},
        upsert=True,
    )


async def subtract_balance(uid: int, guild_id: int, amount: int) -> None:
    """Decrement the wallet by ``amount``."""
    await economy_col.update_one(
        {"_id": f"{guild_id}-{uid}"},
        {"$inc": {"wallet": -amount}},
        upsert=True,
    )


async def update_user_balance(uid: int, guild_id: int, amount: int) -> None:
    """Convenience alias kept for backwards compatibility."""
    await add_balance(uid, guild_id, amount)


# --- food/consumable item buffs -------------------------------------------

async def check_and_use_food_item(user_id, guild_id, item_id) -> bool:
    """Consume a one-shot food item from the user's inventory.

    Returns ``True`` if the item was found and consumed, ``False`` otherwise.
    """
    user_data = await get_user(None, guild_id, user_id)
    inventory = user_data.get("inventory", [])

    for index, item in enumerate(inventory):
        if isinstance(item, str) and item == item_id:
            inventory.pop(index)
            await economy_col.update_one(
                {"_id": f"{guild_id}-{user_id}"},
                {"$set": {"inventory": inventory}},
                upsert=True,
            )
            return True
    return False


async def get_work_cooldown_reduction(user_id, guild_id) -> float:
    """Returns the work-cooldown multiplier (1.0 = no buff, 0.5 = 50% off)."""
    if await check_and_use_food_item(user_id, guild_id, "energy_drink"):
        return 0.5
    return 1.0


async def get_earnings_multiplier(user_id, guild_id) -> float:
    """Returns the earnings multiplier (1.0 = no buff, 2.0 = double)."""
    if await check_and_use_food_item(user_id, guild_id, "lucky_cookie"):
        return 2.0
    return 1.0


async def get_crime_bonus(user_id, guild_id) -> float:
    """Returns the additive crime success bonus (0.0 = no buff)."""
    if await check_and_use_food_item(user_id, guild_id, "coffee_cup"):
        return 0.25
    return 0.0


def now_utc_iso() -> str:
    """Tiny helper kept here so cogs don't import datetime inline."""
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "get_user",
    "get_balance",
    "add_balance",
    "subtract_balance",
    "update_user_balance",
    "check_and_use_food_item",
    "get_work_cooldown_reduction",
    "get_earnings_multiplier",
    "get_crime_bonus",
    "now_utc_iso",
]
