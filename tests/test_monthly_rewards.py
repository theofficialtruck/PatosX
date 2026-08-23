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

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pymongo.errors import DuplicateKeyError
import pytest
import main


def _apply_dotted(doc: dict, path: str, value, op):
    """Apply a $set-or-$inc style dotted-path update to an in-memory dict, mirroring how
    MongoDB interprets keys like 'counters.duck_uses' or 'claimed.riddles_solved'."""
    parts = path.split(".")
    node = doc
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    last = parts[-1]
    if op == "set":
        node[last] = value
    else:
        node[last] = node.get(last, 0) + value


class FakeMonthlyRewardsCol:
    """In-memory stand-in for the `monthly_rewards` Mongo collection, single user doc.
    Faithfully mirrors the two update shapes get_monthly_rewards_doc/increment_monthly_goal/
    check_and_award_monthly_rewards actually issue, including the 'month': {'$ne': ...}
    reset-on-rollover filter and dotted-path $set/$inc semantics."""

    def __init__(self, initial=None):
        self.doc = dict(initial) if initial else None
        self.update_calls = []

    async def find_one(self, query):
        key = query.get("_id")
        if self.doc and self.doc.get("_id") == key:
            return copy.deepcopy(self.doc)
        return None

    async def update_one(self, query, update, upsert=False):
        """Mirrors real MongoDB semantics for the two calls get_monthly_rewards_doc issues:
        1) a plain {"_id": key} upsert carrying only $setOnInsert (ensure-exists), and
        2) a {"_id": key, "month": {"$ne": ...}} conditional $set (reset-on-rollover) that
        never upserts. $setOnInsert only ever applies on the call that actually inserts."""
        self.update_calls.append((dict(query), copy.deepcopy(update), upsert))
        key = query.get("_id")
        exists = self.doc is not None and self.doc.get("_id") == key
        month_filter = query.get("month")
        filter_matches_existing = True
        if exists and isinstance(month_filter, dict) and "$ne" in month_filter:
            filter_matches_existing = self.doc.get("month") != month_filter["$ne"]
        if exists and not filter_matches_existing:
            return  # mirrors Mongo: filter doesn't match this existing document

        just_inserted = False
        if not exists:
            if not upsert:
                return
            self.doc = {"_id": key}
            just_inserted = True

        if "$setOnInsert" in update and just_inserted:
            for k, v in update["$setOnInsert"].items():
                _apply_dotted(self.doc, k, v, "set")
        if "$set" in update:
            for k, v in update["$set"].items():
                _apply_dotted(self.doc, k, v, "set")
        if "$inc" in update:
            for k, v in update["$inc"].items():
                _apply_dotted(self.doc, k, v, "inc")


class RaceConditionMonthlyRewardsCol(FakeMonthlyRewardsCol):
    """Simulates the exact production crash: two commands from the same user got dispatched
    concurrently, so both saw no document and both tried to insert it. MongoDB let one insert
    through and rejected the other with E11000 (DuplicateKeyError) - by the time our own call
    fails, the "winning" concurrent insert has already landed."""

    def __init__(self, doc_after_race):
        super().__init__(None)
        self._doc_after_race = doc_after_race
        self._raised = False

    async def update_one(self, query, update, upsert=False):
        if not self._raised and upsert and "month" not in query:
            self._raised = True
            self.doc = dict(self._doc_after_race)
            raise DuplicateKeyError("E11000 duplicate key error collection: discord_bot.monthly_rewards index: _id_")
        return await super().update_one(query, update, upsert=upsert)


class FakeEconomyCol:
    """In-memory stand-in for the `economy` Mongo collection, single user doc."""

    def __init__(self, initial=None):
        self.doc = dict(initial) if initial else {}

    async def find_one(self, query):
        return dict(self.doc) if self.doc else None

    async def update_one(self, query, update, upsert=False):
        if "$setOnInsert" in update and not self.doc:
            for k, v in update["$setOnInsert"].items():
                self.doc.setdefault(k, v)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                self.doc[k] = self.doc.get(k, 0) + v
        if "$set" in update:
            self.doc.update(update["$set"])


ALL_GOAL_KEYS = {g["key"] for g in main.MONTHLY_REWARD_GOALS}


def target_for(key: str) -> int:
    return next(g["target"] for g in main.MONTHLY_REWARD_GOALS if g["key"] == key)


def reward_for(key: str) -> int:
    return next(g["reward"] for g in main.MONTHLY_REWARD_GOALS if g["key"] == key)


# ---------------------------------------------------------------------------
# get_monthly_rewards_doc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_monthly_rewards_doc_creates_fresh_doc_with_every_goal_zeroed(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    doc = await main.get_monthly_rewards_doc(123, 456)

    assert doc["counters"].keys() == ALL_GOAL_KEYS
    assert all(v == 0 for v in doc["counters"].values())
    assert doc["claimed"].keys() == ALL_GOAL_KEYS
    assert all(v is False for v in doc["claimed"].values())
    assert doc["month"] == main._current_month_key()


@pytest.mark.asyncio
async def test_get_monthly_rewards_doc_preserves_progress_within_same_month(monkeypatch):
    current_month = main._current_month_key()
    existing = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": current_month,
        "counters": {**{k: 0 for k in ALL_GOAL_KEYS}, "duck_uses": 7},
        "claimed": {k: False for k in ALL_GOAL_KEYS},
    }
    col = FakeMonthlyRewardsCol(existing)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    doc = await main.get_monthly_rewards_doc(123, 456)

    assert doc["counters"]["duck_uses"] == 7


@pytest.mark.asyncio
async def test_get_monthly_rewards_doc_resets_counters_and_claims_on_month_rollover(monkeypatch):
    stale = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": "2020-01",  # guaranteed to differ from the current month
        "counters": {**{k: 0 for k in ALL_GOAL_KEYS}, "duck_uses": 999, "commands_used": 500},
        "claimed": {**{k: False for k in ALL_GOAL_KEYS}, "duck_uses": True},
    }
    col = FakeMonthlyRewardsCol(stale)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    doc = await main.get_monthly_rewards_doc(123, 456)

    assert doc["month"] == main._current_month_key()
    assert all(v == 0 for v in doc["counters"].values())
    assert all(v is False for v in doc["claimed"].values())


@pytest.mark.asyncio
async def test_get_monthly_rewards_doc_survives_a_concurrent_insert_race(monkeypatch):
    """Regression test for a production crash: two commands from the same user were dispatched
    concurrently and both tried to create that user's monthly-rewards document at the same
    instant. MongoDB let one insert through and rejected the other with E11000 - that must be
    swallowed as "someone else already created it", not raised up to the caller."""
    current_month = main._current_month_key()
    winning_doc = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": current_month,
        "counters": {k: 0 for k in ALL_GOAL_KEYS},
        "claimed": {k: False for k in ALL_GOAL_KEYS},
    }
    col = RaceConditionMonthlyRewardsCol(winning_doc)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    doc = await main.get_monthly_rewards_doc(123, 456)  # must not raise DuplicateKeyError

    assert doc["month"] == current_month
    assert doc["counters"].keys() == ALL_GOAL_KEYS


@pytest.mark.asyncio
async def test_increment_monthly_goal_does_not_lose_an_update_to_the_insert_race(monkeypatch):
    """End-to-end version of the same regression: the increment that triggered the race must
    still land, not silently vanish because the underlying insert raced."""
    current_month = main._current_month_key()
    winning_doc = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": current_month,
        "counters": {k: 0 for k in ALL_GOAL_KEYS},
        "claimed": {k: False for k in ALL_GOAL_KEYS},
    }
    col = RaceConditionMonthlyRewardsCol(winning_doc)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    await main.increment_monthly_goal(123, 456, "duck_uses", 1)

    doc = await main.get_monthly_rewards_doc(123, 456)
    assert doc["counters"]["duck_uses"] == 1


@pytest.mark.asyncio
async def test_get_monthly_rewards_doc_backfills_goal_keys_missing_from_an_older_document(monkeypatch):
    """Simulates a document saved before a new goal existed: it's missing that goal's
    counter/claimed entries entirely. Reading it must not KeyError and must treat the
    missing goal as zero progress / unclaimed rather than crashing."""
    current_month = main._current_month_key()
    partial = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": current_month,
        "counters": {"duck_uses": 3},
        "claimed": {"duck_uses": False},
    }
    col = FakeMonthlyRewardsCol(partial)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    doc = await main.get_monthly_rewards_doc(123, 456)

    assert doc["counters"].keys() == ALL_GOAL_KEYS
    assert doc["claimed"].keys() == ALL_GOAL_KEYS
    assert doc["counters"]["duck_uses"] == 3
    assert doc["counters"]["riddles_solved"] == 0


# ---------------------------------------------------------------------------
# increment_monthly_goal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_increment_monthly_goal_only_touches_the_named_counter(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    await main.increment_monthly_goal(123, 456, "duck_uses", 1)
    await main.increment_monthly_goal(123, 456, "duck_uses", 1)
    await main.increment_monthly_goal(123, 456, "work_uses", 5)

    doc = await main.get_monthly_rewards_doc(123, 456)
    assert doc["counters"]["duck_uses"] == 2
    assert doc["counters"]["work_uses"] == 5
    assert doc["counters"]["fish_uses"] == 0


@pytest.mark.asyncio
async def test_increment_monthly_goal_resets_stale_month_before_incrementing(monkeypatch):
    """A counter left over from last month must not simply have this month's increment added
    on top of it - the whole document resets first, then the increment applies to zero."""
    stale = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": "2020-01",
        "counters": {**{k: 0 for k in ALL_GOAL_KEYS}, "duck_uses": 999},
        "claimed": {k: False for k in ALL_GOAL_KEYS},
    }
    col = FakeMonthlyRewardsCol(stale)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    await main.increment_monthly_goal(123, 456, "duck_uses", 1)

    doc = await main.get_monthly_rewards_doc(123, 456)
    assert doc["counters"]["duck_uses"] == 1


@pytest.mark.asyncio
async def test_increment_monthly_goal_string_and_int_ids_write_the_same_document(monkeypatch):
    """increment_monthly_goal is called with raw ints from add_balance/on_command_completion
    and with strs from riddle/duckquiz - both must resolve to the same underlying document."""
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)

    await main.increment_monthly_goal(123, 456, "commands_used", 1)
    await main.increment_monthly_goal("123", "456", "commands_used", 1)

    doc = await main.get_monthly_rewards_doc(123, 456)
    assert doc["counters"]["commands_used"] == 2


# ---------------------------------------------------------------------------
# check_and_award_monthly_rewards
# ---------------------------------------------------------------------------


def make_guild_and_member(guild_id=123, user_id=456):
    guild = SimpleNamespace(id=guild_id)
    member = SimpleNamespace(id=user_id, mention=f"<@{user_id}>")
    return guild, member


@pytest.mark.asyncio
async def test_check_and_award_pays_out_and_marks_claimed_when_target_reached(monkeypatch):
    doc = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": main._current_month_key(),
        "counters": {**{k: 0 for k in ALL_GOAL_KEYS}, "duck_uses": target_for("duck_uses")},
        "claimed": {k: False for k in ALL_GOAL_KEYS},
    }
    rewards_col = FakeMonthlyRewardsCol(doc)
    economy = FakeEconomyCol({"wallet": 0})
    monkeypatch.setattr(main, "monthly_rewards_col", rewards_col)
    monkeypatch.setattr(main, "economy_col", economy)
    guild, member = make_guild_and_member()
    channel = SimpleNamespace(send=AsyncMock())

    await main.check_and_award_monthly_rewards(channel, guild, member)

    assert economy.doc["wallet"] == reward_for("duck_uses")
    updated = await main.get_monthly_rewards_doc(123, 456)
    assert updated["claimed"]["duck_uses"] is True
    assert any("Duck Fanatic" in (c.args[0] if c.args else "") for c in channel.send.await_args_list)


@pytest.mark.asyncio
async def test_check_and_award_does_not_reaward_an_already_claimed_goal(monkeypatch):
    doc = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": main._current_month_key(),
        "counters": {**{k: 0 for k in ALL_GOAL_KEYS}, "duck_uses": target_for("duck_uses") + 50},
        "claimed": {**{k: False for k in ALL_GOAL_KEYS}, "duck_uses": True},
    }
    rewards_col = FakeMonthlyRewardsCol(doc)
    economy = FakeEconomyCol({"wallet": 0})
    monkeypatch.setattr(main, "monthly_rewards_col", rewards_col)
    monkeypatch.setattr(main, "economy_col", economy)
    guild, member = make_guild_and_member()
    channel = SimpleNamespace(send=AsyncMock())

    await main.check_and_award_monthly_rewards(channel, guild, member)

    assert economy.doc["wallet"] == 0
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_and_award_sums_multiple_goals_completed_in_the_same_check(monkeypatch):
    doc = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": main._current_month_key(),
        "counters": {
            **{k: 0 for k in ALL_GOAL_KEYS},
            "duck_uses": target_for("duck_uses"),
            "work_uses": target_for("work_uses"),
        },
        "claimed": {k: False for k in ALL_GOAL_KEYS},
    }
    rewards_col = FakeMonthlyRewardsCol(doc)
    economy = FakeEconomyCol({"wallet": 0})
    monkeypatch.setattr(main, "monthly_rewards_col", rewards_col)
    monkeypatch.setattr(main, "economy_col", economy)
    guild, member = make_guild_and_member()
    channel = SimpleNamespace(send=AsyncMock())

    await main.check_and_award_monthly_rewards(channel, guild, member)

    assert economy.doc["wallet"] == reward_for("duck_uses") + reward_for("work_uses")
    updated = await main.get_monthly_rewards_doc(123, 456)
    assert updated["claimed"]["duck_uses"] is True
    assert updated["claimed"]["work_uses"] is True
    assert channel.send.await_count == 2


@pytest.mark.asyncio
async def test_check_and_award_payout_does_not_recurse_into_coins_collected(monkeypatch):
    """The payout is credited directly to economy_col rather than via add_balance(), so
    claiming a goal never bumps coins_collected as a side effect of its own reward."""
    doc = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": main._current_month_key(),
        "counters": {**{k: 0 for k in ALL_GOAL_KEYS}, "duck_uses": target_for("duck_uses")},
        "claimed": {k: False for k in ALL_GOAL_KEYS},
    }
    rewards_col = FakeMonthlyRewardsCol(doc)
    economy = FakeEconomyCol({"wallet": 0})
    monkeypatch.setattr(main, "monthly_rewards_col", rewards_col)
    monkeypatch.setattr(main, "economy_col", economy)
    increment_spy = AsyncMock()
    monkeypatch.setattr(main, "increment_monthly_goal", increment_spy)
    guild, member = make_guild_and_member()
    channel = SimpleNamespace(send=AsyncMock())

    await main.check_and_award_monthly_rewards(channel, guild, member)

    increment_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_and_award_below_target_pays_nothing(monkeypatch):
    doc = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": main._current_month_key(),
        "counters": {**{k: 0 for k in ALL_GOAL_KEYS}, "duck_uses": target_for("duck_uses") - 1},
        "claimed": {k: False for k in ALL_GOAL_KEYS},
    }
    rewards_col = FakeMonthlyRewardsCol(doc)
    economy = FakeEconomyCol({"wallet": 0})
    monkeypatch.setattr(main, "monthly_rewards_col", rewards_col)
    monkeypatch.setattr(main, "economy_col", economy)
    guild, member = make_guild_and_member()
    channel = SimpleNamespace(send=AsyncMock())

    await main.check_and_award_monthly_rewards(channel, guild, member)

    assert economy.doc["wallet"] == 0
    channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# on_command_completion listener
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_command_completion_increments_generic_and_mapped_counters(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)
    monkeypatch.setattr(main, "check_and_award_monthly_rewards", AsyncMock())
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, command=SimpleNamespace(name="duck"))

    await main.on_command_completion(ctx)

    doc = await main.get_monthly_rewards_doc(123, 456)
    assert doc["counters"]["commands_used"] == 1
    assert doc["counters"]["duck_uses"] == 1
    assert doc["counters"]["work_uses"] == 0


@pytest.mark.asyncio
async def test_on_command_completion_unmapped_command_only_bumps_commands_used(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)
    monkeypatch.setattr(main, "check_and_award_monthly_rewards", AsyncMock())
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, command=SimpleNamespace(name="balance"))

    await main.on_command_completion(ctx)

    doc = await main.get_monthly_rewards_doc(123, 456)
    assert doc["counters"]["commands_used"] == 1
    assert all(doc["counters"][k] == 0 for k in ALL_GOAL_KEYS if k != "commands_used")


@pytest.mark.asyncio
async def test_on_command_completion_skips_staff_commands(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)
    award_spy = AsyncMock()
    monkeypatch.setattr(main, "check_and_award_monthly_rewards", award_spy)
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, command=SimpleNamespace(name="ban"))

    await main.on_command_completion(ctx)

    assert col.doc is None
    award_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_command_completion_ignores_dm_context_with_no_guild(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)
    ctx = SimpleNamespace(guild=None, author=SimpleNamespace(id=456), command=SimpleNamespace(name="duck"))

    await main.on_command_completion(ctx)

    assert col.doc is None


@pytest.mark.asyncio
async def test_on_command_completion_checks_for_newly_completed_goals(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)
    award_spy = AsyncMock()
    monkeypatch.setattr(main, "check_and_award_monthly_rewards", award_spy)
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, command=SimpleNamespace(name="duck"))

    await main.on_command_completion(ctx)

    award_spy.assert_awaited_once_with(ctx, guild, author)


# ---------------------------------------------------------------------------
# duckquiz -> quiz_passes wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quizview_finish_quiz_tracks_quiz_passes_on_a_passing_score(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)
    monkeypatch.setattr(main, "quiz_col", SimpleNamespace(update_one=AsyncMock()))
    monkeypatch.setattr(main, "config_col", SimpleNamespace(find_one=AsyncMock(return_value={})))
    guild = SimpleNamespace(id=123, get_role=lambda rid: None)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    view = main.QuizView(ctx, "quiz-1", [{"q": "?", "options": ["a"], "answer": 1}] * 10)
    view.score = 10  # 100% - clears the PASS_PCT threshold

    await view.finish_quiz()

    doc = await main.get_monthly_rewards_doc(123, 456)
    assert doc["counters"]["quiz_passes"] == 1


@pytest.mark.asyncio
async def test_quizview_finish_quiz_does_not_track_quiz_passes_on_a_failing_score(monkeypatch):
    col = FakeMonthlyRewardsCol(None)
    monkeypatch.setattr(main, "monthly_rewards_col", col)
    monkeypatch.setattr(main, "quiz_col", SimpleNamespace(update_one=AsyncMock()))
    guild = SimpleNamespace(id=123, get_role=lambda rid: None)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    view = main.QuizView(ctx, "quiz-1", [{"q": "?", "options": ["a"], "answer": 1}] * 10)
    view.score = 0  # 0% - well under PASS_PCT

    await view.finish_quiz()

    assert col.doc is None  # get_monthly_rewards_doc/increment_monthly_goal were never called


# ---------------------------------------------------------------------------
# .monthlyrewards command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monthlyrewards_command_shows_progress_and_total_claimed(monkeypatch):
    doc = {
        "_id": "123-456",
        "guild": "123",
        "user": "456",
        "month": main._current_month_key(),
        "counters": {**{k: 0 for k in ALL_GOAL_KEYS}, "duck_uses": target_for("duck_uses"), "work_uses": 3},
        "claimed": {**{k: False for k in ALL_GOAL_KEYS}, "duck_uses": True},
    }
    col = FakeMonthlyRewardsCol(doc)
    monkeypatch.setattr(main, "monthly_rewards_col", col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    await main.monthlyrewards.callback(ctx)

    ctx.send.assert_awaited_once()
    embed = ctx.send.await_args.kwargs["embed"]
    field_names = [f.name for f in embed.fields]
    assert any("Duck Fanatic" in n for n in field_names)
    duck_field = next(f for f in embed.fields if "Duck Fanatic" in f.name)
    assert "Claimed" in duck_field.value
    work_field = next(f for f in embed.fields if "Hard Worker" in f.name)
    assert f"3/{target_for('work_uses')}" in work_field.value
    assert f"Earned this month: {reward_for('duck_uses')} coins" in embed.footer.text


@pytest.mark.asyncio
async def test_monthlyrewards_command_has_no_xp_earn_decorator():
    """xp_earn-wrapped commands set __wrapped__ via functools.wraps; monthlyrewards must not
    be one of them since the task explicitly requires no XP for checking progress."""
    assert not hasattr(main.monthlyrewards.callback, "__wrapped__")


# ---------------------------------------------------------------------------
# Safety guard: must never touch a live database during test runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monthly_reward_helpers_are_safe_noops_against_a_real_looking_collection(monkeypatch):
    """When monthly_rewards_col has NOT been swapped for a test double, every helper must
    bail out immediately rather than risk a network call to the live database."""
    from motor.motor_asyncio import AsyncIOMotorClient

    lookalike = AsyncIOMotorClient("mongodb://localhost:27017")["test_db"]["monthly_rewards"]
    monkeypatch.setattr(main, "monthly_rewards_col", lookalike)
    guild, member = make_guild_and_member()
    channel = SimpleNamespace(send=AsyncMock())

    # None of these should raise, hang, or attempt network I/O.
    doc = await main.get_monthly_rewards_doc(123, 456)
    assert doc["counters"].keys() == ALL_GOAL_KEYS
    await main.increment_monthly_goal(123, 456, "duck_uses", 1)
    await main.check_and_award_monthly_rewards(channel, guild, member)
    channel.send.assert_not_awaited()
