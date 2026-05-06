"""Idempotent database seeding for one-time defaults (e.g. shop catalogue)."""

from __future__ import annotations

from bot.config.constants import INITIAL_SHOP_ITEMS
from bot.database import shop_col


async def ensure_shop_items() -> None:
    """Upsert the default shop catalogue.

    Safe to call repeatedly — each item is keyed on its ``_id``.
    """
    for item in INITIAL_SHOP_ITEMS:
        await shop_col.update_one(
            {"_id": item["_id"]},
            {"$set": item},
            upsert=True,
        )
    print("✅ Shop synced with initial items.")


__all__ = ["ensure_shop_items"]
