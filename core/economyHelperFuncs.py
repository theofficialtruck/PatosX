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

"""Economy helpers shared by many cogs: the per-user economy record (get_user), inventory /
tool / consumable normalisation, wallet and bank balance helpers, amount and duration parsing,
investment math, and the monthly-rewards goal tracking.

Monthly rewards live here rather than in xpHelperFuncs because add_balance() feeds the
coins_collected goal, and the core import graph only flows xp -> economy, never the reverse.
"""

import random
import re
import uuid
from datetime import datetime, timedelta, timezone

import discord
from pymongo.errors import DuplicateKeyError

from core import config as cfg
from core import state


async def get_user(ctx, guild_id, user_id):
    """Fetch the economy record for a user, creating it with defaults if absent.
    Also backfills any missing fields and normalises the inventory on every read."""
    key = f"{guild_id}-{user_id}"
    guild_id = str(guild_id)
    user_id = str(user_id)
    defaults = {
        "_id": key,
        "guild": guild_id,
        "user": user_id,
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
    }
    u = await state.economy_col.find_one({"_id": key})
    if not u:
        await state.economy_col.insert_one(defaults)
        return defaults
    else:
        updated = False
        changes_detected = []
        for k, v in defaults.items():
            if k not in u:
                u[k] = v
                updated = True
                changes_detected.append((k, "None", v))
        for field in ["wallet", "bank"]:
            if field in u:
                try:
                    current_val = int(u[field])
                except (TypeError, ValueError):
                    current_val = u[field]
                if isinstance(current_val, int) and current_val != defaults[field]:
                    changes_detected.append((field, u[field], current_val))
        inventory = u.get("inventory", [])
        normalized_inventory, inventory_changed = normalize_inventory_items(inventory)
        if inventory_changed:
            u["inventory"] = normalized_inventory
            updated = True
        if updated:
            await state.economy_col.update_one({"_id": key}, {"$set": u})
        return u


def normalize_item_key(item):
    """Return a canonical lowercase key for an inventory item regardless of its storage format.
    Items can be plain strings or dicts with _id, name_lower, or name fields."""
    if isinstance(item, str):
        return item.strip().lower()
    if isinstance(item, dict):
        raw = item.get("_id") or item.get("name_lower") or item.get("name")
        if raw is None:
            return None
        return str(raw).strip().lower()
    return None


def normalize_shop_item_name(raw):
    """Lowercase, strip, and convert underscores to spaces for consistent shop item comparison."""
    return str(raw or "").strip().lower().replace("_", " ")


def is_removed_shop_item(raw):
    """Return True if the item key matches one of the retired shop items in cfg.REMOVED_SHOP_ITEM_KEYS."""
    normalized = normalize_shop_item_name(raw)
    removed = {normalize_shop_item_name(key) for key in cfg.REMOVED_SHOP_ITEM_KEYS}
    return normalized in removed


async def purge_removed_shop_items(guild_id: str | None = None):
    """Delete all variants of removed shop items from both the global shop and guild shop collections.
    Pass guild_id to limit the guild shop sweep to a single guild."""
    removed_space = {normalize_shop_item_name(key) for key in cfg.REMOVED_SHOP_ITEM_KEYS}
    removed_underscore = {name.replace(" ", "_") for name in removed_space}
    removed_compact = {name.replace(" ", "") for name in removed_space}
    removed_variants = removed_space | removed_underscore | removed_compact
    await state.shop_col.delete_many(
        {"$or": [{"name_lower": {"$in": list(removed_space)}}, {"_id": {"$in": list(removed_variants)}}]}
    )
    guild_query = {
        "$or": [
            {"name_lower": {"$in": list(removed_space)}},
            {"_id": {"$regex": "-(?:mystery box|mystery_box|mysterybox)$", "$options": "i"}},
        ]
    }
    if guild_id:
        guild_query["guild"] = str(guild_id)
    await state.guild_shop_col.delete_many(guild_query)


def normalize_inventory_items(inventory):
    """Return (normalized_list, changed) where every durable tool and pet_duck entry is
    converted to the canonical dict form with a valid uses_left count."""
    normalized = []
    changed = False
    for item in inventory or []:
        item_key = normalize_item_key(item)
        if item_key in cfg.TOOL_DURABILITIES:
            max_uses = cfg.TOOL_DURABILITIES[item_key]
            if isinstance(item, dict):
                uses_left = item.get("uses_left")
                if not isinstance(uses_left, int):
                    uses_left = max_uses
                    changed = True
                uses_left = max(0, min(uses_left, max_uses))
                canonical = {"_id": item_key, "uses_left": uses_left}
                if item != canonical:
                    changed = True
                normalized.append(canonical)
            else:
                normalized.append({"_id": item_key, "uses_left": max_uses})
                changed = True
            continue
        if item_key in ("pet_duck", "nitro_boost"):
            uses_left = 3
            if isinstance(item, dict) and isinstance(item.get("uses_left"), int):
                uses_left = max(0, item.get("uses_left"))
            canonical = {"_id": item_key, "uses_left": uses_left}
            if item != canonical:
                changed = True
            normalized.append(canonical)
            continue
        normalized.append(item)
    return (normalized, changed)


def consume_tool_use(inventory, tool_name):
    """Decrement the uses_left on the first matching tool in inventory in place.
    Returns (found, broke, uses_left_after). broke is True when the tool durability reached zero.
    Returns (False, False, None) when the tool is not in inventory."""
    tool_key = tool_name.strip().lower()
    max_uses = cfg.TOOL_DURABILITIES.get(tool_key)
    if max_uses is None:
        return (False, False, None)
    for idx, item in enumerate(inventory):
        item_key = normalize_item_key(item)
        if item_key != tool_key:
            continue
        if isinstance(item, dict):
            uses_left = item.get("uses_left")
            if not isinstance(uses_left, int):
                uses_left = max_uses
        else:
            uses_left = max_uses
        uses_left -= 1
        if uses_left <= 0:
            inventory.pop(idx)
            return (True, True, 0)
        inventory[idx] = {"_id": tool_key, "uses_left": uses_left}
        return (True, False, uses_left)
    return (False, False, None)


def consume_nitro_boost(inventory: list):
    """Decrement the uses_left on the first Nitro Boost in inventory in place, matching the
    Pet Duck stacking pattern. Returns (used, expired); expired is True once its last use is spent."""
    for idx, item in enumerate(inventory):
        if isinstance(item, dict) and item.get("_id") == "nitro_boost":
            item["uses_left"] -= 1
            expired = item["uses_left"] <= 0
            if expired:
                inventory.pop(idx)
            return (True, expired)
    return (False, False)


def reduce_command_cooldown(ctx, seconds: float) -> None:
    """Shift ctx.command's discord.py rate-limit bucket backward by `seconds`, so the next
    invocation becomes available that much sooner. No-op for commands with no active cooldown bucket."""
    buckets = getattr(ctx.command, "_buckets", None)
    if buckets is None or not buckets.valid:
        return
    bucket = buckets.get_bucket(ctx)
    if bucket is not None:
        bucket._window -= seconds


def add_suffix(value: int) -> str:
    """Format a large integer as a compact string using B, M, or K suffixes (e.g. 1500 -> 1.50K)."""
    if value >= 1000000000:
        return f"{value / 1000000000:.2f}B"
    elif value >= 1000000:
        return f"{value / 1000000:.2f}M"
    elif value >= 1000:
        return f"{value / 1000:.2f}K"
    else:
        return str(value)


def suffix_to_int(s: str) -> int:
    """Parse a suffixed coin string back to a plain integer (e.g. '1.5K' -> 1500)."""
    s = s.upper().replace(",", "")
    if s.endswith("B"):
        return int(float(s[:-1]) * 1000000000)
    elif s.endswith("M"):
        return int(float(s[:-1]) * 1000000)
    elif s.endswith("K"):
        return int(float(s[:-1]) * 1000)
    else:
        return int(float(s))


def parse_time(duration_str: str) -> int:
    """Parse a human readable duration string (e.g. '1h 30m', '2 days') and return total seconds.
    Raises ValueError when the string contains no recognised time units."""
    multipliers = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
        "w": 604800,
        "week": 604800,
        "weeks": 604800,
        "mo": 2592000,
        "month": 2592000,
        "months": 2592000,
        "y": 31536000,
        "yr": 31536000,
        "year": 31536000,
        "years": 31536000,
    }
    duration_str = duration_str.lower().replace(",", " ").strip()
    pattern = "(\\d+(?:\\.\\d+)?)\\s*(s|sec|second|seconds|m|min|minute|minutes|h|hr|hour|hours|d|day|days|w|week|weeks|mo|month|months|y|yr|year|years)\\b"
    matches = re.findall(pattern, duration_str)
    if not matches:
        raise ValueError(f"Invalid duration format: {duration_str}")
    total_seconds = 0
    for amount_str, unit in matches:
        if unit not in multipliers:
            raise ValueError(f"Unknown time unit: {unit}")
        total_seconds += float(amount_str) * multipliers[unit]
    return int(total_seconds)


# Lookup table used by words_to_number to convert English number words to integers
WORDS_TO_NUM = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1000,
    "million": 1000000,
    "billion": 1000000000,
    "trillion": 1000000000000,
}


def words_to_number(text: str) -> int | None:
    """Convert a written out English number such as 'one hundred' to its integer value.
    Returns None if any word in text is not found in WORDS_TO_NUM."""
    words = text.lower().replace("-", " ").split()
    total, current = (0, 0)
    for word in words:
        if word not in WORDS_TO_NUM:
            return None
        value = WORDS_TO_NUM[word]
        if value == 100:
            current *= value
        elif value >= 1000:
            current *= value
            total += current
            current = 0
        else:
            current += value
    return total + current


def parse_amount(amount_str: str) -> int | None:
    """Parse a coin amount string that may include suffixes (k, m, b, t) or written out words.
    Returns None when the string cannot be interpreted as a valid amount."""
    if not amount_str:
        return None
    s = amount_str.lower().replace(",", "").strip()
    multiplier = 1
    if any(word in s for word in WORDS_TO_NUM):
        result = words_to_number(s)
        if result is not None:
            return result
    if re.search("(k|thousand)$", s):
        multiplier = 1000
        s = re.sub("(k|thousand)$", "", s)
    elif re.search("(m|mil|mm|million)$", s):
        multiplier = 1000000
        s = re.sub("(m|mil|mm|million)$", "", s)
    elif re.search("(b|bil|bn|billion)$", s):
        multiplier = 1000000000
        s = re.sub("(b|bil|bn|billion)$", "", s)
    elif re.search("(t|tr|tril|trillion)$", s):
        multiplier = 1000000000000
        s = re.sub("(t|tr|tril|trillion)$", "", s)
    try:
        return int(float(s) * multiplier)
    except ValueError:
        return None


async def get_balance(uid: int, guild_id: int) -> int:
    """Return the current wallet balance for a user, defaulting to 0 when no record exists."""
    user_id = f"{guild_id}-{uid}"
    data = await state.economy_col.find_one({"_id": user_id})
    return data.get("wallet", 0) if data else 0


async def add_balance(uid: int, guild_id: int, amount: int):
    """Atomically increment the wallet balance for a user by amount."""
    user_id = f"{guild_id}-{uid}"
    await state.economy_col.update_one(
        {"_id": user_id},
        {
            "$inc": {"wallet": amount},
            "$set": {"guild": str(guild_id), "user": str(uid)},
            "$setOnInsert": {"bank": 0, "inventory": []},
        },
        upsert=True,
    )
    if amount > 0:
        await increment_monthly_goal(guild_id, uid, "coins_collected", amount)


async def subtract_balance(uid: int, guild_id: int, amount: int):
    """Atomically decrement the wallet balance for a user by amount."""
    user_id = f"{guild_id}-{uid}"
    await state.economy_col.update_one(
        {"_id": user_id},
        {
            "$inc": {"wallet": -amount},
            "$set": {"guild": str(guild_id), "user": str(uid)},
            "$setOnInsert": {"bank": 0, "inventory": []},
        },
        upsert=True,
    )


async def update_user_balance(uid: int, guild_id: int, amount: int):
    """Adjust the wallet balance for a user by amount (positive adds, negative subtracts)."""
    user_id = f"{guild_id}-{uid}"
    await state.economy_col.update_one(
        {"_id": user_id},
        {
            "$inc": {"wallet": amount},
            "$set": {"guild": str(guild_id), "user": str(uid)},
            "$setOnInsert": {"bank": 0, "inventory": []},
        },
        upsert=True,
    )
    if amount > 0:
        await increment_monthly_goal(guild_id, uid, "coins_collected", amount)


async def check_and_use_food_item(user_id, guild_id, item_id):
    """Search the user's inventory for item_id, remove it, and persist the change.
    Returns True if the item was found and consumed."""
    normalized_id = item_id.replace("_", " ").strip().lower()
    user_data = await get_user(None, guild_id, user_id)
    inventory = user_data.get("inventory", [])
    item_found = False
    for i, item in enumerate(inventory):
        if normalize_item_key(item) == normalized_id:
            inventory.pop(i)
            item_found = True
            break
    if item_found:
        await state.economy_col.update_one(
            {"_id": f"{guild_id}-{user_id}"}, {"$set": {"inventory": inventory}}, upsert=True
        )
        return True
    return False


def pop_food_item(inventory: list, item_id: str) -> bool:
    """Remove one instance of item_id from inventory in place. Returns True if found."""
    normalized_id = item_id.replace("_", " ").strip().lower()
    for i, item in enumerate(inventory):
        if normalize_item_key(item) == normalized_id:
            inventory.pop(i)
            return True
    return False


async def get_work_cooldown_reduction(user_id, guild_id):
    """Return a cooldown multiplier for work. Consumes an energy drink from inventory if present,
    returning 0.5 (half cooldown). Returns 1.0 (no reduction) otherwise."""
    if await check_and_use_food_item(user_id, guild_id, "energy_drink"):
        return 0.5
    return 1.0


async def get_earnings_multiplier(user_id, guild_id):
    """Return an earnings multiplier for work. Consumes a lucky cookie if present, returning 2.0.
    Returns 1.0 (no bonus) otherwise."""
    if await check_and_use_food_item(user_id, guild_id, "lucky_cookie"):
        return 2.0
    return 1.0


async def get_crime_bonus(user_id, guild_id):
    """Return an extra bonus fraction for the crime command. Consumes a coffee cup if present,
    returning 0.25 (25 percent bonus). Returns 0.0 otherwise."""
    if await check_and_use_food_item(user_id, guild_id, "coffee_cup"):
        return 0.25
    return 0.0


async def create_investment(user_id: str, company: str, amount: int):
    """Insert a new investment record with a generated UUID. Sets current_value equal to amount at creation."""
    inv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    await state.investments_col.insert_one(
        {
            "_id": inv_id,
            "user_id": user_id,
            "company": company,
            "amount": amount,
            "current_value": amount,
            "last_status_refresh_date": now.date().isoformat(),
            "date": now_iso,
            "timestamp": now_iso,
            "history": [],
        }
    )


def get_investment_date(inv: dict) -> datetime:
    """Parse the investment creation date from the record, falling back to now when missing or malformed."""
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


async def calculate_investment_value(inv: dict) -> int:
    """Return the current integer value of an investment, falling back to the original amount."""
    current_value = inv.get("current_value")
    if current_value is None:
        current_value = inv.get("amount", 0)
    try:
        return max(0, int(current_value))
    except (TypeError, ValueError):
        return max(0, int(inv.get("amount", 0) or 0))


def pick_daily_investment_change_pct() -> float:
    """Sample a daily percentage change for an investment. Weighted slightly toward small losses."""
    return random.choices([-0.03, -0.02, -0.01, 0.01, 0.02, 0.03], weights=[18, 18, 18, 16, 15, 15], k=1)[0]


async def refresh_user_investments_for_today(investments: list[dict], now: datetime | None = None) -> list[dict]:
    """Apply daily price changes to each investment that has not been refreshed today.
    Returns the updated list of investment dicts."""
    if now is None:
        now = datetime.now(timezone.utc)
    refresh_date = now.date().isoformat()
    refreshed: list[dict] = []
    for inv in investments:
        last_refresh_raw = inv.get("last_status_refresh_date")
        last_refresh_date = None
        if isinstance(last_refresh_raw, str) and last_refresh_raw:
            try:
                last_refresh_date = datetime.fromisoformat(last_refresh_raw).date()
            except (TypeError, ValueError):
                last_refresh_date = None
        if last_refresh_date is None:
            if inv.get("date") or inv.get("timestamp"):
                last_refresh_date = get_investment_date(inv).date()
            else:
                last_refresh_date = now.date() - timedelta(days=1)
        days_elapsed = (now.date() - last_refresh_date).days
        if days_elapsed <= 0:
            if inv.get("last_status_refresh_date") != refresh_date:
                await state.investments_col.update_one(
                    {"_id": inv["_id"]}, {"$set": {"last_status_refresh_date": refresh_date}}
                )
                updated_inv = dict(inv)
                updated_inv["last_status_refresh_date"] = refresh_date
                refreshed.append(updated_inv)
            else:
                refreshed.append(inv)
            continue
        try:
            current_value = max(0, int(inv.get("current_value", inv.get("amount", 0)) or 0))
        except (TypeError, ValueError):
            current_value = max(0, int(inv.get("amount", 0) or 0))
        history = inv.get("history")
        if not isinstance(history, list):
            history = []
        inv_id = str(inv.get("_id") or "")
        for offset in range(1, days_elapsed + 1):
            day = last_refresh_date + timedelta(days=offset)
            day_str = day.isoformat()
            saved_state = random.getstate()
            try:
                random.seed(f"{inv_id}:{day_str}")
                change_pct = pick_daily_investment_change_pct()
            finally:
                random.setstate(saved_state)
            new_value = max(1, round(current_value * (1 + change_pct)))
            history.append(new_value - current_value)
            current_value = new_value
            if len(history) > 180:
                history = history[-180:]
        patch = {"current_value": current_value, "last_status_refresh_date": refresh_date, "history": history}
        await state.investments_col.update_one({"_id": inv["_id"]}, {"$set": patch})
        updated_inv = dict(inv)
        updated_inv.update(patch)
        refreshed.append(updated_inv)
    return refreshed


def _current_month_key() -> str:
    """Return the current calendar month as 'YYYY-MM' in UTC, used to detect month rollover."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _monthly_rewards_default_doc(guild_id: str, user_id: str, month: str) -> dict:
    """Build a fresh monthly rewards document with every goal counter at zero and unclaimed."""
    return {
        "_id": f"{guild_id}-{user_id}",
        "guild": guild_id,
        "user": user_id,
        "month": month,
        "counters": {goal["key"]: 0 for goal in cfg.MONTHLY_REWARD_GOALS},
        "claimed": {goal["key"]: False for goal in cfg.MONTHLY_REWARD_GOALS},
    }


def _monthly_rewards_col_live_in_tests() -> bool:
    """Return True when running under pytest but state.monthly_rewards_col was not swapped for a
    test double, meaning it is still the real Motor collection. Callers use this to skip all
    database access entirely rather than risk touching the live database during a test run."""
    return cfg._running_under_pytest() and cfg._looks_like_motor_collection(state.monthly_rewards_col)


async def get_monthly_rewards_doc(guild_id, user_id) -> dict:
    """Return the caller's monthly rewards document, lazily resetting counters and claims
    when the stored month no longer matches the current UTC month. Missing goal keys (e.g., on
    a document saved before a new goal was added) are backfilled with zero/unclaimed defaults.

    Runs as two separate updates rather than one upsert to avoid a duplicate-key race: two
    commands from the same user can be handled concurrently (discord.py dispatches each
    invocation as its own task), so two calls can both see "no document yet" and both try to
    insert the same _id at once. MongoDB only lets one of those inserts through and rejects the
    other with E11000 - that's expected under concurrency, not a real failure, so it's caught
    and ignored rather than crashing the caller (and silently dropping whatever counter update
    it was in the middle of)."""
    guild_id, user_id = (str(guild_id), str(user_id))
    current_month = _current_month_key()
    default_doc = _monthly_rewards_default_doc(guild_id, user_id, current_month)
    key = default_doc["_id"]
    if _monthly_rewards_col_live_in_tests():
        return default_doc
    # Step 1: guarantee the document exists. $setOnInsert is a no-op against an existing
    # document, so this never disturbs an already-reset month's progress.
    try:
        await state.monthly_rewards_col.update_one({"_id": key}, {"$setOnInsert": default_doc}, upsert=True)
    except DuplicateKeyError:
        pass  # another concurrent call created it first - the document exists either way
    # Step 2: reset counters/claims on month rollover. The document is now guaranteed to exist,
    # so this update never needs upsert and can never race with a concurrent insert.
    await state.monthly_rewards_col.update_one(
        {"_id": key, "month": {"$ne": current_month}},
        {"$set": {k: v for k, v in default_doc.items() if k != "_id"}},
    )
    doc = await state.monthly_rewards_col.find_one({"_id": key}) or default_doc
    counters = dict(doc.get("counters") or {})
    claimed = dict(doc.get("claimed") or {})
    for goal in cfg.MONTHLY_REWARD_GOALS:
        counters.setdefault(goal["key"], 0)
        claimed.setdefault(goal["key"], False)
    doc["counters"] = counters
    doc["claimed"] = claimed
    return doc


async def increment_monthly_goal(guild_id, user_id, counter_key: str, amount: int = 1) -> None:
    """Add amount to a single monthly reward counter for a user, resetting the document
    first if a new month has begun since it was last touched."""
    if _monthly_rewards_col_live_in_tests():
        return
    guild_id, user_id = (str(guild_id), str(user_id))
    try:
        await get_monthly_rewards_doc(guild_id, user_id)
        await state.monthly_rewards_col.update_one(
            {"_id": f"{guild_id}-{user_id}"}, {"$inc": {f"counters.{counter_key}": amount}}
        )
    except Exception as e:
        print(f"[Monthly Rewards] Failed to increment {counter_key} for {guild_id}-{user_id}: {e}")


async def check_and_award_monthly_rewards(ctx_or_channel, guild: discord.Guild, member: discord.Member) -> None:
    """Check every monthly goal for member, mark any newly reached goal as claimed, pay out its
    coin reward, and announce it. Already-claimed goals are skipped so a goal can only ever pay
    out once per month. Skipped entirely under pytest unless the collection was test-doubled."""
    if _monthly_rewards_col_live_in_tests():
        return
    try:
        guild_id, user_id = (str(guild.id), str(member.id))
        doc = await get_monthly_rewards_doc(guild_id, user_id)
        counters = doc.get("counters", {})
        claimed = doc.get("claimed", {})
        newly_completed = [
            goal
            for goal in cfg.MONTHLY_REWARD_GOALS
            if not claimed.get(goal["key"]) and counters.get(goal["key"], 0) >= goal["target"]
        ]
        if not newly_completed:
            return
        await state.monthly_rewards_col.update_one(
            {"_id": f"{guild_id}-{user_id}"},
            {"$set": {f"claimed.{goal['key']}": True for goal in newly_completed}},
        )
        total_reward = sum(goal["reward"] for goal in newly_completed)
        # Credited directly rather than via add_balance() so payouts don't feed back into
        # (and inflate) the coins_collected counter they may have just helped complete.
        await state.economy_col.update_one(
            {"_id": f"{guild_id}-{user_id}"},
            {
                "$inc": {"wallet": total_reward},
                "$set": {"guild": guild_id, "user": user_id},
                "$setOnInsert": {"bank": 0, "inventory": []},
            },
            upsert=True,
        )
        for goal in newly_completed:
            announcement = (
                f"{goal['emoji']} {member.mention} completed the monthly goal **{goal['title']}** "
                f"and earned **{goal['reward']} coins**!"
            )
            try:
                if hasattr(ctx_or_channel, "send"):
                    await ctx_or_channel.send(announcement)
            except (discord.Forbidden, discord.HTTPException):
                print(f"[Monthly Rewards] Could not announce goal {goal['key']} in guild {guild_id}.")
    except Exception as e:
        print(f"[Monthly Rewards check error] {type(e).__name__}: {e}")
