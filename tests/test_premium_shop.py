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

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs import premium_shop as ps
from core import config as cfg
from core import economyHelperFuncs as econ
from core import permsHelperFuncs as perms
from core import state

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakePremiumCol:
    """Just enough of a Mongo collection for the premium shop's queries: whole-doc $set upserts and
    the positional stock $inc guarded by $elemMatch."""

    def __init__(self, docs=None):
        self.docs = docs or {}

    async def find_one(self, query):
        doc = self.docs.get(query["_id"])
        return dict(doc) if doc else None

    async def update_one(self, query, update, upsert=False):
        doc = self.docs.get(query["_id"])
        if doc is None:
            if not upsert:
                return SimpleNamespace(modified_count=0)
            doc = self.docs[query["_id"]] = {"_id": query["_id"]}
        if "is_open" in query and doc.get("is_open") != query["is_open"]:
            return SimpleNamespace(modified_count=0)
        target = None
        if "$inc" in update:
            match = query.get("items", {}).get("$elemMatch") or {"key": query.get("items.key")}
            for item in doc.get("items", []):
                if item["key"] == match["key"] and ("stock" not in match or item["stock"] > match["stock"]["$gt"]):
                    target = item
            if target is None:
                return SimpleNamespace(modified_count=0)
            target["stock"] += update["$inc"]["items.$.stock"]
        doc.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1)


class FakeEconomyCol:
    def __init__(self, wallet):
        self.doc = {"_id": "1-2", "wallet": wallet, "inventory": []}

    async def find_one(self, query):
        return dict(self.doc)

    async def insert_one(self, doc):
        pass

    async def update_one(self, query, update, upsert=False):
        if "$set" in update:  # get_user backfilling missing default fields
            self.doc.update(update["$set"])
            return SimpleNamespace(modified_count=1)
        if self.doc["wallet"] < query["wallet"]["$gte"]:
            return SimpleNamespace(modified_count=0)
        self.doc["wallet"] += update["$inc"]["wallet"]
        self.doc["inventory"].append(update["$push"]["inventory"])
        return SimpleNamespace(modified_count=1)


def open_doc(items, closes_in=timedelta(minutes=30)):
    return {
        "_id": "1",
        "is_open": True,
        "closes_at": (datetime.now(timezone.utc) + closes_in).isoformat(),
        "items": items,
    }


# === pure helpers ===============================================================================


def test_roll_stock_picks_a_random_subset_with_bounded_stock():
    low, high = cfg.PREMIUM_SHOP_ITEMS_PER_OPENING
    seen_sets = set()
    for seed in range(50):
        stock = ps.roll_stock(random.Random(seed))
        keys = [entry["key"] for entry in stock]
        assert low <= len(keys) <= high
        assert len(set(keys)) == len(keys)
        for entry in stock:
            lo, hi = cfg.PREMIUM_SHOP_ITEMS[entry["key"]]["stock_range"]
            assert lo <= entry["stock"] <= hi
        seen_sets.add(frozenset(keys))
    assert len(seen_sets) > 1
    assert len(cfg.PREMIUM_SHOP_ITEMS) > high  # not every premium item shows up every time


def test_schedule_rolls_stay_in_configured_ranges():
    for seed in range(50):
        rng = random.Random(seed)
        assert timedelta(hours=cfg.PREMIUM_SHOP_GAP_HOURS[0]) <= ps.roll_gap(rng)
        assert ps.roll_gap(rng) <= timedelta(hours=cfg.PREMIUM_SHOP_GAP_HOURS[1])
        duration = ps.roll_open_duration(rng)
        assert timedelta(minutes=cfg.PREMIUM_SHOP_OPEN_MINUTES[0]) <= duration
        assert duration <= timedelta(minutes=cfg.PREMIUM_SHOP_OPEN_MINUTES[1])
    assert cfg.PREMIUM_SHOP_OPEN_MINUTES[0] >= 20  # "not too short"


def test_premium_items_are_single_use_and_boosted():
    for key, spec in cfg.PREMIUM_SHOP_ITEMS.items():
        entry = ps.build_inventory_entry(key)
        if spec["inventory_kind"] == "dict":
            assert entry == {"_id": key, "uses_left": 1}
        else:
            assert entry == spec["inventory_key"]
    assert cfg.PET_DUCK_VARIANTS["premium_pet_duck"]["bonus"] == 2 * cfg.PET_DUCK_VARIANTS["pet_duck"]["bonus"]
    assert cfg.PREMIUM_SHOP_ITEMS["premium_pet_duck"]["price"] > 1000 / 3  # cost boosted with the value


@pytest.mark.parametrize(
    "raw,expected",
    [(123, 123), ("456", 456), ("<#789>", 789), ([111, "222"], 111), ("all", None), (None, None), ([], None)],
)
def test_economy_channel_id(raw, expected):
    assert ps.economy_channel_id({"economy_channel": raw}) == expected


def test_is_open_now():
    soon = (NOW + timedelta(minutes=5)).isoformat()
    stocked = [{"key": "premium_pet_duck", "stock": 1}]
    assert ps.is_open_now({"is_open": True, "closes_at": soon, "items": stocked}, NOW)
    assert not ps.is_open_now({"is_open": True, "closes_at": soon, "items": [{"key": "x", "stock": 0}]}, NOW)
    assert not ps.is_open_now(
        {"is_open": True, "closes_at": (NOW - timedelta(seconds=1)).isoformat(), "items": stocked}, NOW
    )
    assert not ps.is_open_now({"is_open": False, "closes_at": soon, "items": stocked}, NOW)
    assert not ps.is_open_now(None, NOW)


# === purchasing =================================================================================


@pytest.mark.asyncio
async def test_purchase_takes_stock_and_coins_and_grants_single_use_item(monkeypatch):
    shop_col = FakePremiumCol({"1": open_doc([{"key": "premium_pet_duck", "stock": 2}])})
    economy = FakeEconomyCol(wallet=1000)
    monkeypatch.setattr(state, "premium_shop_col", shop_col)
    monkeypatch.setattr(state, "economy_col", economy)
    result = await ps.purchase_premium_item(1, 2, "premium_pet_duck")
    assert result["ok"] is True
    assert result["new_wallet"] == 1000 - cfg.PREMIUM_SHOP_ITEMS["premium_pet_duck"]["price"]
    assert economy.doc["inventory"] == [{"_id": "premium_pet_duck", "uses_left": 1}]
    assert shop_col.docs["1"]["items"][0]["stock"] == 1


@pytest.mark.asyncio
async def test_purchase_runs_out_of_stock(monkeypatch):
    shop_col = FakePremiumCol(
        {"1": open_doc([{"key": "premium_pet_duck", "stock": 1}, {"key": "premium_coffee_cup", "stock": 3}])}
    )
    economy = FakeEconomyCol(wallet=5000)
    monkeypatch.setattr(state, "premium_shop_col", shop_col)
    monkeypatch.setattr(state, "economy_col", economy)
    assert (await ps.purchase_premium_item(1, 2, "premium_pet_duck"))["ok"] is True
    second = await ps.purchase_premium_item(1, 2, "premium_pet_duck")
    assert second["ok"] is False
    assert "sold out" in second["message"]
    assert economy.doc["wallet"] == 5000 - cfg.PREMIUM_SHOP_ITEMS["premium_pet_duck"]["price"]


@pytest.mark.asyncio
async def test_purchase_rejects_broke_buyer_without_touching_stock(monkeypatch):
    shop_col = FakePremiumCol({"1": open_doc([{"key": "premium_pet_duck", "stock": 2}])})
    economy = FakeEconomyCol(wallet=10)
    monkeypatch.setattr(state, "premium_shop_col", shop_col)
    monkeypatch.setattr(state, "economy_col", economy)
    result = await ps.purchase_premium_item(1, 2, "premium_pet_duck")
    assert result["ok"] is False
    assert shop_col.docs["1"]["items"][0]["stock"] == 2
    assert economy.doc["inventory"] == []


@pytest.mark.asyncio
async def test_purchase_returns_stock_when_the_charge_loses_a_race(monkeypatch):
    shop_col = FakePremiumCol({"1": open_doc([{"key": "premium_pet_duck", "stock": 2}])})
    economy = FakeEconomyCol(wallet=1000)
    monkeypatch.setattr(state, "premium_shop_col", shop_col)
    monkeypatch.setattr(state, "economy_col", economy)
    # The wallet check passes, then the coins vanish before the atomic charge runs.
    real_get_user = econ.get_user

    async def drain_after_read(*args, **kwargs):
        data = await real_get_user(*args, **kwargs)
        economy.doc["wallet"] = 0
        return data

    monkeypatch.setattr(econ, "get_user", drain_after_read)
    result = await ps.purchase_premium_item(1, 2, "premium_pet_duck")
    assert result["ok"] is False
    assert shop_col.docs["1"]["items"][0]["stock"] == 2


@pytest.mark.asyncio
async def test_purchase_rejected_when_shop_closed(monkeypatch):
    doc = open_doc([{"key": "premium_pet_duck", "stock": 2}], closes_in=timedelta(minutes=-1))
    monkeypatch.setattr(state, "premium_shop_col", FakePremiumCol({"1": doc}))
    result = await ps.purchase_premium_item(1, 2, "premium_pet_duck")
    assert result["ok"] is False
    assert "closed" in result["message"]


# === scheduler ==================================================================================


def make_guild(channel):
    return SimpleNamespace(id=1, get_channel=lambda cid: channel if cid == channel.id else None)


def make_channel():
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 99
    channel.send = AsyncMock(return_value=SimpleNamespace(id=555))
    partial = MagicMock()
    partial.edit = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.partial = partial
    return channel


@pytest.fixture
def cog(bot):
    return ps.PremiumShop(bot)


@pytest.mark.asyncio
async def test_first_tick_schedules_then_opens_then_closes(monkeypatch, cog):
    channel = make_channel()
    guild = make_guild(channel)
    shop_col = FakePremiumCol()
    monkeypatch.setattr(state, "premium_shop_col", shop_col)
    monkeypatch.setattr(state, "config_col", SimpleNamespace(find_one=AsyncMock(return_value={"economy_channel": 99})))

    await cog._tick_guild(guild, NOW)  # nothing scheduled yet -> schedule only, never open straight away
    doc = shop_col.docs["1"]
    assert doc["is_open"] is False
    channel.send.assert_not_awaited()
    next_open = ps.parse_time(doc["next_open_at"])
    assert NOW + timedelta(hours=cfg.PREMIUM_SHOP_GAP_HOURS[0]) <= next_open
    assert next_open <= NOW + timedelta(hours=cfg.PREMIUM_SHOP_GAP_HOURS[1])

    await cog._tick_guild(guild, next_open - timedelta(seconds=1))  # not time yet
    channel.send.assert_not_awaited()

    await cog._tick_guild(guild, next_open)  # opens in the economy channel
    channel.send.assert_awaited_once()
    doc = shop_col.docs["1"]
    assert doc["is_open"] is True
    assert doc["message_id"] == 555
    assert doc["channel_id"] == 99
    assert ps.parse_time(doc["closes_at"]) - next_open >= timedelta(minutes=cfg.PREMIUM_SHOP_OPEN_MINUTES[0])

    await cog._tick_guild(guild, next_open + timedelta(minutes=1))  # still open: nothing changes
    assert shop_col.docs["1"]["is_open"] is True

    closes_at = ps.parse_time(doc["closes_at"])
    await cog._tick_guild(guild, closes_at + timedelta(seconds=1))  # expired -> closed and rescheduled
    doc = shop_col.docs["1"]
    assert doc["is_open"] is False
    assert ps.parse_time(doc["next_open_at"]) > closes_at
    channel.partial.edit.assert_awaited()
    assert channel.partial.edit.call_args.kwargs["view"] is None


@pytest.mark.asyncio
async def test_sold_out_shop_closes_early(monkeypatch, cog):
    channel = make_channel()
    guild = make_guild(channel)
    doc = open_doc([{"key": "premium_pet_duck", "stock": 0}])
    doc.update({"channel_id": 99, "message_id": 555})
    shop_col = FakePremiumCol({"1": doc})
    monkeypatch.setattr(state, "premium_shop_col", shop_col)
    await cog._tick_guild(guild, datetime.now(timezone.utc))
    assert shop_col.docs["1"]["is_open"] is False


@pytest.mark.asyncio
async def test_no_economy_channel_means_no_shop(monkeypatch, cog):
    channel = make_channel()
    shop_col = FakePremiumCol({"1": {"_id": "1", "is_open": False, "next_open_at": NOW.isoformat()}})
    monkeypatch.setattr(state, "premium_shop_col", shop_col)
    monkeypatch.setattr(state, "config_col", SimpleNamespace(find_one=AsyncMock(return_value={})))
    await cog._tick_guild(make_guild(channel), NOW + timedelta(days=1))
    channel.send.assert_not_awaited()


# === boosted items in the commands ==============================================================


def test_premium_duck_is_double_strength_and_expires_after_one_use():
    inv = [{"_id": "premium_pet_duck", "uses_left": 1}]
    use = econ.consume_pet_duck(inv)
    assert use.bonus == 0.6
    assert use.pct == 60
    assert use.farewell is not None
    assert inv == []


def test_regular_duck_keeps_three_uses_and_thirty_percent():
    inv = [{"_id": "pet_duck", "uses_left": 3}]
    use = econ.consume_pet_duck(inv)
    assert (use.bonus, use.pct, use.farewell) == (0.3, 30, None)
    assert inv == [{"_id": "pet_duck", "uses_left": 2}]
    assert econ.consume_pet_duck(["fishing rod"]) is None


def test_pop_variant_item_finds_premium_cookie_and_leaves_the_rest():
    inv = ["fishing rod", "premium lucky cookie", "lucky cookie"]
    variant = econ.pop_variant_item(inv, cfg.LUCKY_COOKIE_VARIANTS)
    assert variant["multiplier"] == 3.0
    assert inv == ["fishing rod", "lucky cookie"]
    assert econ.pop_variant_item(["fishing rod"], cfg.LUCKY_COOKIE_VARIANTS) is None


@pytest.mark.asyncio
async def test_beg_with_premium_cookie_triples_earnings(monkeypatch, economy_cog):
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None
    user_data = {"_id": "100-200", "wallet": 0, "bank": 0, "inventory": ["premium lucky cookie"], "last_beg": None}
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()
    add_balance = AsyncMock()
    monkeypatch.setattr(econ, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(state, "economy_col", mock_col)
    monkeypatch.setattr(perms, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(econ, "add_balance", add_balance)
    monkeypatch.setattr(random, "randint", lambda a, b: 100)

    await economy_cog.beg.callback(economy_cog, ctx)

    add_balance.assert_awaited_once_with(200, 100, 300)
    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any("Premium Lucky Cookie consumed" in t and "tripled" in t for t in sent_texts)
    assert user_data["inventory"] == []


@pytest.mark.asyncio
async def test_inventory_lists_premium_items(monkeypatch, shop_cog):
    ctx = MagicMock()
    ctx.author.display_name = "Tester"
    ctx.send = AsyncMock()
    inventory = [{"_id": "premium_pet_duck", "uses_left": 1}, "premium coffee cup", "premium coffee cup"]
    monkeypatch.setattr(perms, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(econ, "get_user", AsyncMock(return_value={"inventory": inventory}))
    await shop_cog.inventory.callback(shop_cog, ctx)
    embed = ctx.send.call_args.kwargs["embed"]
    names = [field.name for field in embed.fields]
    assert "🦆 Premium Pet Duck x1" in names
    assert "☕ Premium Coffee Cup x2" in names
    assert not any("no longer sold" in field.value for field in embed.fields)
