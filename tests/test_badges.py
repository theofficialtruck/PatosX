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

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import main


class FakeBadgesCol:
    """In-memory stand-in for the `badges` Mongo collection, single user doc."""

    def __init__(self, initial=None):
        self.doc = dict(initial) if initial else None

    async def find_one(self, query):
        if self.doc and self.doc.get("_id") == query.get("_id"):
            return dict(self.doc)
        return None

    async def update_one(self, query, update, upsert=False):
        key = query["_id"]
        if self.doc is None or self.doc.get("_id") != key:
            self.doc = {"_id": key, "counters": {}, "earned": []}
        if "$inc" in update:
            for k, v in update["$inc"].items():
                _, counter = k.split(".", 1)
                self.doc["counters"][counter] = self.doc["counters"].get(counter, 0) + v
        if "$set" in update:
            self.doc.update(update["$set"])


class FakeXpCol:
    """In-memory stand-in for the `xp` Mongo collection, single user doc."""

    def __init__(self, xp=0):
        self.doc = {"xp": xp} if xp else {}

    async def find_one(self, query):
        return dict(self.doc) if self.doc else None

    async def update_one(self, query, update, upsert=False):
        if "$inc" in update:
            for k, v in update["$inc"].items():
                self.doc[k] = self.doc.get(k, 0) + v
        if "$set" in update:
            self.doc.update(update["$set"])


class StatefulEconomyCol:
    """In-memory stand-in for the `economy` Mongo collection, single user doc."""

    def __init__(self, initial):
        self.doc = dict(initial)

    async def find_one(self, query):
        return dict(self.doc)

    async def update_one(self, query, update, upsert=False):
        if "$set" in update:
            self.doc.update(update["$set"])


class FakeGuildShopCol:
    def __init__(self, item):
        self.item = item

    async def find_one(self, query):
        return self.item


def make_guild_and_member():
    guild = SimpleNamespace(
        id=123,
        roles=[],
        create_role=AsyncMock(side_effect=lambda name, reason=None: SimpleNamespace(id=hash(name) % 100000, name=name)),
    )
    member = SimpleNamespace(id=42, mention="<@42>", roles=[], add_roles=AsyncMock())
    return guild, member


@pytest.mark.asyncio
async def test_buy_bulk_ten_items_awards_shopaholic_badge(monkeypatch):
    """Buying 10 units of an item in a single `.buy item 10` call must count as 10
    shop purchases (not 1), so the Shopaholic badge (10 purchases) actually unlocks."""
    guild, member = make_guild_and_member()
    store_item = {
        "_id": "123-fishing rod",
        "guild": "123",
        "name": "Fishing Rod",
        "name_lower": "fishing rod",
        "price": 100,
    }
    # wallet spends down to exactly 0 so the wallet based "pocket_change" badge
    # (>= 1000 coins) doesn't also fire and muddy the add_roles assertion below.
    economy = StatefulEconomyCol({"wallet": 1000, "inventory": []})
    badges = FakeBadgesCol()
    xp = FakeXpCol()
    sent_messages = []

    async def fake_send(*args, **kwargs):
        content = kwargs.get("content")
        if content is None and args:
            content = args[0]
        sent_messages.append(content)

    async def fake_check_channel(*args, **kwargs):
        return True

    async def fake_get_user(ctx, guild_id, user_id):
        return await economy.find_one({"_id": f"{guild_id}-{user_id}"})

    ctx = SimpleNamespace(
        guild=guild,
        author=member,
        command=SimpleNamespace(name="buy"),
        send=AsyncMock(side_effect=fake_send),
        interaction=None,
    )
    monkeypatch.setattr(main, "check_channel", fake_check_channel)
    monkeypatch.setattr(main, "get_user", fake_get_user)
    monkeypatch.setattr(main, "guild_shop_col", FakeGuildShopCol(store_item))
    monkeypatch.setattr(main, "economy_col", economy)
    monkeypatch.setattr(main, "badges_col", badges)
    monkeypatch.setattr(main, "xp_col", xp)

    await main.buy.callback(ctx, item="fishing rod 10")

    assert badges.doc["counters"]["shop_purchases"] == 10
    assert "shopaholic" in badges.doc["earned"]
    member.add_roles.assert_awaited_once()
    assert any("Shopaholic" in (m or "") for m in sent_messages)


@pytest.mark.asyncio
async def test_buy_bulk_nine_items_does_not_award_shopaholic_badge(monkeypatch):
    """Sanity check on the fix: 9 purchases in one call should not cross the 10 threshold."""
    guild, member = make_guild_and_member()
    store_item = {
        "_id": "123-fishing rod",
        "guild": "123",
        "name": "Fishing Rod",
        "name_lower": "fishing rod",
        "price": 100,
    }
    economy = StatefulEconomyCol({"wallet": 900, "inventory": []})
    badges = FakeBadgesCol()
    xp = FakeXpCol()

    async def fake_check_channel(*args, **kwargs):
        return True

    async def fake_get_user(ctx, guild_id, user_id):
        return await economy.find_one({"_id": f"{guild_id}-{user_id}"})

    ctx = SimpleNamespace(
        guild=guild,
        author=member,
        command=SimpleNamespace(name="buy"),
        send=AsyncMock(),
        interaction=None,
    )
    monkeypatch.setattr(main, "check_channel", fake_check_channel)
    monkeypatch.setattr(main, "get_user", fake_get_user)
    monkeypatch.setattr(main, "guild_shop_col", FakeGuildShopCol(store_item))
    monkeypatch.setattr(main, "economy_col", economy)
    monkeypatch.setattr(main, "badges_col", badges)
    monkeypatch.setattr(main, "xp_col", xp)

    await main.buy.callback(ctx, item="fishing rod 9")

    assert badges.doc["counters"]["shop_purchases"] == 9
    assert "shopaholic" not in badges.doc.get("earned", [])
    member.add_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_and_award_badges_awards_wallet_threshold_badge(monkeypatch):
    """pocket_change unlocks at wallet+bank >= 1000, independent of any counter."""
    guild, member = make_guild_and_member()
    ctx_channel = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(main, "badges_col", FakeBadgesCol())
    monkeypatch.setattr(main, "xp_col", FakeXpCol())
    await main.check_and_award_badges(ctx_channel, guild, member, {"wallet": 600, "bank": 500})
    member.add_roles.assert_awaited_once()
    assert any("Pocket Change" in (c.args[0] if c.args else "") for c in ctx_channel.send.await_args_list)


@pytest.mark.asyncio
async def test_check_and_award_badges_awards_xp_threshold_badge(monkeypatch):
    """apprentice unlocks at 500 xp, read from the xp collection rather than economy_data."""
    guild, member = make_guild_and_member()
    ctx_channel = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(main, "badges_col", FakeBadgesCol())
    monkeypatch.setattr(main, "xp_col", FakeXpCol(xp=500))
    await main.check_and_award_badges(ctx_channel, guild, member, {"wallet": 0, "bank": 0})
    member.add_roles.assert_awaited_once()
    assert any("Apprentice" in (c.args[0] if c.args else "") for c in ctx_channel.send.await_args_list)


@pytest.mark.asyncio
async def test_check_and_award_badges_awards_inventory_based_badge(monkeypatch):
    """duck_whisperer unlocks by owning a pet_duck inventory entry."""
    guild, member = make_guild_and_member()
    ctx_channel = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(main, "badges_col", FakeBadgesCol())
    monkeypatch.setattr(main, "xp_col", FakeXpCol())
    economy_data = {"wallet": 0, "bank": 0, "inventory": [{"_id": "pet_duck", "uses_left": 3}]}
    await main.check_and_award_badges(ctx_channel, guild, member, economy_data)
    member.add_roles.assert_awaited_once()
    assert any("Duck Whisperer" in (c.args[0] if c.args else "") for c in ctx_channel.send.await_args_list)


@pytest.mark.asyncio
async def test_check_and_award_badges_skips_already_earned_badge(monkeypatch):
    """A badge already recorded as earned must not be re-awarded (no duplicate role/announcement)."""
    guild, member = make_guild_and_member()
    ctx_channel = SimpleNamespace(send=AsyncMock())
    key = f"{guild.id}-{member.id}"
    monkeypatch.setattr(main, "badges_col", FakeBadgesCol({"_id": key, "earned": ["pocket_change"], "counters": {}}))
    monkeypatch.setattr(main, "xp_col", FakeXpCol())
    await main.check_and_award_badges(ctx_channel, guild, member, {"wallet": 600, "bank": 500})
    member.add_roles.assert_not_awaited()
    ctx_channel.send.assert_not_awaited()
