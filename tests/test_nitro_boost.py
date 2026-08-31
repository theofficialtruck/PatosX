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

"""Tests for the Nitro Boost shop item: purchasing, inventory normalization, and the
cooldown reduction it grants on beg, lottery, work, fish, swim, crime, and bugcatch."""

import datetime as dt_module
import random as _random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main

# === consume_nitro_boost helper ================================================================


def test_consume_nitro_boost_decrements_uses():
    inv = [{"_id": "nitro_boost", "uses_left": 3}]
    used, expired = main.consume_nitro_boost(inv)
    assert used is True
    assert expired is False
    assert inv == [{"_id": "nitro_boost", "uses_left": 2}]


def test_consume_nitro_boost_removes_when_exhausted():
    inv = [{"_id": "nitro_boost", "uses_left": 1}]
    used, expired = main.consume_nitro_boost(inv)
    assert used is True
    assert expired is True
    assert inv == []


def test_consume_nitro_boost_returns_false_when_absent():
    inv = ["fishing rod"]
    used, expired = main.consume_nitro_boost(inv)
    assert used is False
    assert expired is False
    assert inv == ["fishing rod"]


# === reduce_command_cooldown helper ============================================================


class FakeBucket:
    def __init__(self, window):
        self._window = window


class FakeBucketMapping:
    def __init__(self, bucket, valid=True):
        self.valid = valid
        self._bucket = bucket

    def get_bucket(self, ctx):
        return self._bucket


def test_reduce_command_cooldown_shifts_window_back():
    bucket = FakeBucket(window=1000.0)
    ctx = SimpleNamespace(command=SimpleNamespace(_buckets=FakeBucketMapping(bucket)))
    main.reduce_command_cooldown(ctx, 300)
    assert bucket._window == 700.0


def test_reduce_command_cooldown_noop_when_invalid():
    bucket = FakeBucket(window=1000.0)
    ctx = SimpleNamespace(command=SimpleNamespace(_buckets=FakeBucketMapping(bucket, valid=False)))
    main.reduce_command_cooldown(ctx, 300)
    assert bucket._window == 1000.0


def test_reduce_command_cooldown_noop_without_buckets():
    ctx = SimpleNamespace(command=SimpleNamespace())
    main.reduce_command_cooldown(ctx, 300)  # must not raise


# === normalize_inventory_items =================================================================


def test_normalize_inventory_items_canonicalizes_nitro_boost():
    normalized, changed = main.normalize_inventory_items(["nitro_boost"])
    assert changed is True
    assert normalized == [{"_id": "nitro_boost", "uses_left": 3}]


# === shop seeding and purchase ==================================================================


@pytest.mark.asyncio
async def test_ensure_shop_items_seeds_nitro_boost(monkeypatch):
    class FakeCol:
        def __init__(self):
            self.calls = []

        async def update_one(self, query, update, upsert=False):
            self.calls.append((query, update, upsert))

        async def delete_many(self, query):
            return None

    shop = FakeCol()
    guild_shop = FakeCol()
    monkeypatch.setattr(main, "shop_col", shop)
    monkeypatch.setattr(main, "guild_shop_col", guild_shop)

    await main.ensure_shop_items()

    nitro_calls = [c for c in shop.calls if c[0] == {"_id": "nitro_boost"}]
    assert len(nitro_calls) == 1
    _, update, upsert = nitro_calls[0]
    item = update["$set"]
    assert item["price"] == 1000
    assert item["uses_left"] == 3
    assert upsert is True


@pytest.mark.asyncio
async def test_process_shop_purchase_nitro_boost_stacks(monkeypatch):
    member = SimpleNamespace(id=42)
    guild = SimpleNamespace(id=123)
    store_item = {"_id": "123-nitro_boost", "name": "Nitro Boost", "name_lower": "nitro boost", "price": 1000}

    class FakeEconomyCol:
        def __init__(self):
            self.calls = []

        async def update_one(self, query, update, upsert=False):
            self.calls.append((query, update, upsert))

    economy = FakeEconomyCol()
    monkeypatch.setattr(main, "economy_col", economy)
    result = await main.process_shop_purchase(member, guild, store_item, {"wallet": 2000, "inventory": []})
    assert result["ok"] is True
    assert result["purchase_type"] == "nitro_boost"
    assert economy.calls == [
        ({"_id": "123-42"}, {"$set": {"wallet": 1000, "inventory": [{"_id": "nitro_boost", "uses_left": 3}]}}, False)
    ]


# === beg command ================================================================================


@pytest.mark.asyncio
async def test_beg_nitro_boost_reduces_cooldown_and_consumed(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        "_id": "100-200",
        "wallet": 0,
        "bank": 0,
        "inventory": [{"_id": "nitro_boost", "uses_left": 1}],
        "last_beg": None,
    }
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())

    await main.beg.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any("Nitro Boost cut your next beg cooldown" in t for t in sent_texts)
    assert any("ran out after 3 uses" in t for t in sent_texts)

    saved = [
        call.args[1]["$set"]["last_beg"]
        for call in mock_col.update_one.call_args_list
        if "last_beg" in call.args[1].get("$set", {})
    ]
    assert saved
    saved_ts = dt_module.datetime.fromisoformat(saved[0])
    delta = (dt_module.datetime.now(dt_module.timezone.utc) - saved_ts).total_seconds()
    assert 175 <= delta <= 185  # 20% of 900s = 180s


# === lottery command =============================================================================


@pytest.mark.asyncio
async def test_lottery_nitro_boost_reduces_cooldown_and_consumed(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        "_id": "100-200",
        "wallet": 1000,
        "bank": 0,
        "inventory": [{"_id": "nitro_boost", "uses_left": 1}],
        "last_lottery": None,
    }
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(_random, "random", lambda: 0.99)  # lose the draw

    await main.lottery.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any("Nitro Boost cut your next lottery cooldown" in t for t in sent_texts)
    assert any("ran out after 3 uses" in t for t in sent_texts)

    saved = [
        call.args[1]["$set"]["last_lottery"]
        for call in mock_col.update_one.call_args_list
        if "last_lottery" in call.args[1].get("$set", {})
    ]
    assert saved
    saved_ts = dt_module.datetime.fromisoformat(saved[0])
    delta = (dt_module.datetime.now(dt_module.timezone.utc) - saved_ts).total_seconds()
    assert 715 <= delta <= 725  # 20% of 3600s = 720s


# === work command ================================================================================


@pytest.mark.asyncio
async def test_work_nitro_boost_reduces_cooldown_and_consumed(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.author.display_avatar.url = "https://example.com/av.png"
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        "_id": "100-200",
        "wallet": 0,
        "bank": 0,
        "inventory": [{"_id": "nitro_boost", "uses_left": 1}],
        "job": "duck",
        "promotion_level": 0,
    }
    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=None)  # no existing cooldown
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())

    await main.work.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any("Nitro Boost cut your next work cooldown" in t for t in sent_texts)
    assert any("ran out after 3 uses" in t for t in sent_texts)

    saved = [
        call.args[1]["$set"]["timestamp"]
        for call in mock_col.update_one.call_args_list
        if "timestamp" in call.args[1].get("$set", {})
    ]
    assert saved
    saved_ts = dt_module.datetime.fromisoformat(saved[0])
    delta = (dt_module.datetime.now(dt_module.timezone.utc) - saved_ts).total_seconds()
    assert 8630 <= delta <= 8650  # 20% of 43200s = 8640s


# === crime command ================================================================================


@pytest.mark.asyncio
async def test_crime_nitro_boost_reduces_cooldown_on_success(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        "_id": "100-200",
        "wallet": 2000,
        "bank": 0,
        "inventory": [{"_id": "nitro_boost", "uses_left": 1}],
        "last_crime": None,
    }
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(_random, "random", lambda: 0.0)  # always succeed

    await main.crime.callback(ctx, choice="shoplift")

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any("Nitro Boost cut your next crime cooldown" in t for t in sent_texts)
    assert any("ran out after 3 uses" in t for t in sent_texts)

    saved = [
        call.args[1]["$set"]["last_crime"]
        for call in mock_col.update_one.call_args_list
        if "last_crime" in call.args[1].get("$set", {})
    ]
    assert saved
    saved_ts = dt_module.datetime.fromisoformat(saved[0])
    delta = (dt_module.datetime.now(dt_module.timezone.utc) - saved_ts).total_seconds()
    assert 17270 <= delta <= 17290  # 20% of 86400s = 17280s


# === fish / swim / bugcatch (discord.py decorator cooldown) ======================================


@pytest.mark.asyncio
async def test_fish_nitro_boost_calls_reduce_cooldown_and_consumed(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        "_id": "100-200",
        "inventory": [{"_id": "fishing rod", "uses_left": 5}, {"_id": "nitro_boost", "uses_left": 1}],
    }
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())
    monkeypatch.setattr(main, "increment_badge_counter", AsyncMock())
    reduce_mock = MagicMock()
    monkeypatch.setattr(main, "reduce_command_cooldown", reduce_mock)

    await main.fish.callback(ctx)

    reduce_mock.assert_called_once_with(ctx, int(3600 * main.NITRO_BOOST_COOLDOWN_REDUCTION_PCT))
    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any("Nitro Boost cut your next fish cooldown" in t for t in sent_texts)
    assert any("ran out after 3 uses" in t for t in sent_texts)


@pytest.mark.asyncio
async def test_swim_nitro_boost_calls_reduce_cooldown_and_consumed(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None
    ctx._skip_xp_award = False

    user_data = {
        "_id": "100-200",
        "inventory": [{"_id": "scuba gear", "uses_left": 5}, {"_id": "nitro_boost", "uses_left": 1}],
    }
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())
    monkeypatch.setattr(main, "increment_badge_counter", AsyncMock())
    reduce_mock = MagicMock()
    monkeypatch.setattr(main, "reduce_command_cooldown", reduce_mock)

    await main.swim.callback(ctx)

    reduce_mock.assert_called_once_with(ctx, int(3600 * main.NITRO_BOOST_COOLDOWN_REDUCTION_PCT))
    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any("Nitro Boost cut your next swim cooldown" in t for t in sent_texts)
    assert any("ran out after 3 uses" in t for t in sent_texts)


@pytest.mark.asyncio
async def test_bugcatch_nitro_boost_calls_reduce_cooldown_and_consumed(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        "_id": "100-200",
        "inventory": [{"_id": "butterfly net", "uses_left": 5}, {"_id": "nitro_boost", "uses_left": 1}],
    }
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())
    monkeypatch.setattr(main, "increment_badge_counter", AsyncMock())
    reduce_mock = MagicMock()
    monkeypatch.setattr(main, "reduce_command_cooldown", reduce_mock)

    await main.bugcatch.callback(ctx)

    reduce_mock.assert_called_once_with(ctx, int(3600 * main.NITRO_BOOST_COOLDOWN_REDUCTION_PCT))
    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any("Nitro Boost cut your next bugcatch cooldown" in t for t in sent_texts)
    assert any("ran out after 3 uses" in t for t in sent_texts)
