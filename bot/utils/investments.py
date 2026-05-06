"""Investment lifecycle helpers used by the ``invest`` cog and tests.

These were originally embedded inside command callbacks; pulling them out
makes them unit-testable (the original test suite mocks ``investments_col``
and asserts on these functions directly).
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from bot.database import investments_col


async def create_investment(user_id: str, company: str, amount: int) -> str:
    """Insert a new investment document and return its id."""
    inv_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    await investments_col.insert_one(
        {
            "_id": inv_id,
            "user_id": user_id,
            "company": company,
            "amount": amount,
            "date": now_iso,
            "timestamp": now_iso,
            "history": [],
        }
    )
    return inv_id


def get_investment_date(inv: dict) -> datetime:
    """Resolve an investment's creation time across legacy/new schemas.

    Older documents only had ``timestamp``; we now persist ``date`` as the
    canonical field. This always returns a tz-aware UTC datetime so callers
    can subtract reliably.
    """
    date_raw = inv.get("date") or inv.get("timestamp")
    if not date_raw:
        return datetime.now(timezone.utc)

    try:
        parsed = datetime.fromisoformat(date_raw)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


async def backfill_investment_dates_from_timestamp() -> dict:
    """Backfill ``date`` from legacy ``timestamp`` values, non-destructively.

    Returns a stats dict so the operator can confirm what changed.
    """
    stats = {
        "scanned": 0,
        "updated": 0,
        "invalid_timestamp": 0,
        "skipped_conflict": 0,
        "write_errors": 0,
    }

    query = {"date": {"$exists": False}, "timestamp": {"$exists": True}}

    async for inv in investments_col.find(query):
        stats["scanned"] += 1
        ts_raw = inv.get("timestamp")

        try:
            parsed = datetime.fromisoformat(ts_raw)
        except (TypeError, ValueError):
            stats["invalid_timestamp"] += 1
            continue

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        try:
            result = await investments_col.update_one(
                {"_id": inv["_id"], "date": {"$exists": False}},
                {"$set": {"date": parsed.isoformat()}},
            )
            if result.modified_count:
                stats["updated"] += 1
            else:
                stats["skipped_conflict"] += 1
        except Exception:
            stats["write_errors"] += 1

    return stats


async def calculate_investment_value(inv: dict) -> int:
    """Roll forward an investment's value based on randomised daily history.

    The function persists generated history days so the value is monotonic
    and reproducible across calls within the same UTC day.
    """
    amount = inv["amount"]
    date_obj = get_investment_date(inv)
    now = datetime.now(timezone.utc)
    days_passed = (now - date_obj).days

    if days_passed < 2:
        return amount

    value = amount
    history = inv.get("history", [])

    for day in range(len(history), days_passed):
        if day == 0:
            change = int(value * random.uniform(0.06, 0.10))
        else:
            if random.random() < 0.30:
                change = -int(value * random.uniform(0.01, 0.10))
            else:
                change = int(value * random.uniform(0.02, 0.10))

        if inv["company"] == "Oceanic":
            change = int(change * 3)

        history.append(change)

    for change in history:
        value += change
        if value < 0:
            value = 0

    await investments_col.update_one(
        {"_id": inv["_id"]},
        {"$set": {"history": history}},
    )

    return int(value)


__all__ = [
    "create_investment",
    "get_investment_date",
    "backfill_investment_dates_from_timestamp",
    "calculate_investment_value",
]
