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

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
import main


def _suppress_xp_earn_side_effect(monkeypatch):
    """xp_earn only skips its "you earned X xp" bonus message when xp_col looks like a
    real Motor collection (main._looks_like_motor_collection). A disconnected
    AsyncIOMotorCollection satisfies that check without touching the network."""
    from motor.motor_asyncio import AsyncIOMotorClient

    lookalike = AsyncIOMotorClient("mongodb://localhost:27017")["test_db"]["xp"]
    monkeypatch.setattr(main, "xp_col", lookalike)


class FakeEconomyCol:
    """In-memory stand-in for the `economy` Mongo collection, single user doc, supporting
    the $inc/$set/$setOnInsert shapes add_balance() actually issues."""

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


class FakeLevelView:
    """Stands in for RiddleLevelView so tests can pick a difficulty deterministically
    instead of simulating a real Discord button interaction."""

    def __init__(self, ctx, choice):
        self.ctx = ctx
        self.choice = choice
        self.message = None

    async def wait(self):
        return False


def make_ctx(monkeypatch, *, guild_id=1, user_id=2, economy_doc=None):
    _suppress_xp_earn_side_effect(monkeypatch)
    economy = FakeEconomyCol(economy_doc)
    monkeypatch.setattr(main, "economy_col", economy)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())
    increment_spy = AsyncMock()
    award_spy = AsyncMock()
    monkeypatch.setattr(main, "increment_monthly_goal", increment_spy)
    monkeypatch.setattr(main, "check_and_award_monthly_rewards", award_spy)

    guild = SimpleNamespace(id=guild_id)
    author = SimpleNamespace(id=user_id, display_name="Tester", mention=f"<@{user_id}>")
    channel = SimpleNamespace(id=555)
    command = MagicMock()
    sent = []

    async def fake_send(*args, **kwargs):
        content = kwargs.get("content")
        if content is None and args:
            content = args[0]
        sent.append(content)
        return SimpleNamespace(id=999, edit=AsyncMock())

    ctx = SimpleNamespace(
        guild=guild,
        author=author,
        channel=channel,
        command=command,
        send=AsyncMock(side_effect=fake_send),
        interaction=None,
    )
    return ctx, economy, increment_spy, award_spy, sent


@pytest.mark.asyncio
async def test_riddle_correct_answer_awards_coins_and_tracks_progress(monkeypatch):
    ctx, economy, increment_spy, award_spy, sent = make_ctx(monkeypatch, economy_doc={"wallet": 0})
    monkeypatch.setattr(main, "RiddleLevelView", lambda c: FakeLevelView(c, "easy"))
    monkeypatch.setattr(main.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(main.random, "randint", lambda a, b: a)
    monkeypatch.setattr(main.bot, "wait_for", AsyncMock(return_value=SimpleNamespace(content="egg")))

    await main.riddle.callback(ctx)

    assert economy.doc["wallet"] == main.RIDDLE_LEVELS["easy"]["reward_range"][0]
    assert any("Correct" in (s or "") for s in sent)
    # add_balance() itself also bumps the coins_collected counter, so riddles_solved is one
    # of (at least) two increment_monthly_goal calls rather than the only one.
    increment_spy.assert_any_await(str(ctx.guild.id), str(ctx.author.id), "riddles_solved", 1)
    award_spy.assert_awaited_once()
    ctx.command.reset_cooldown.assert_not_called()


@pytest.mark.asyncio
async def test_riddle_wrong_answer_awards_no_coins_and_no_progress(monkeypatch):
    ctx, economy, increment_spy, award_spy, sent = make_ctx(monkeypatch, economy_doc={"wallet": 0})
    monkeypatch.setattr(main, "RiddleLevelView", lambda c: FakeLevelView(c, "easy"))
    monkeypatch.setattr(main.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(main.bot, "wait_for", AsyncMock(return_value=SimpleNamespace(content="banana")))

    await main.riddle.callback(ctx)

    assert economy.doc.get("wallet", 0) == 0
    assert any("Wrong answer" in (s or "") for s in sent)
    increment_spy.assert_not_awaited()
    award_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_riddle_accepts_answer_phrased_as_a_sentence(monkeypatch):
    """'I think it's an egg!' should still match the accepted answer 'egg'."""
    ctx, economy, increment_spy, award_spy, sent = make_ctx(monkeypatch, economy_doc={"wallet": 0})
    monkeypatch.setattr(main, "RiddleLevelView", lambda c: FakeLevelView(c, "easy"))
    monkeypatch.setattr(main.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(main.random, "randint", lambda a, b: a)
    monkeypatch.setattr(main.bot, "wait_for", AsyncMock(return_value=SimpleNamespace(content="I think it's an egg!")))

    await main.riddle.callback(ctx)

    assert economy.doc["wallet"] > 0
    assert any("Correct" in (s or "") for s in sent)


@pytest.mark.asyncio
async def test_riddle_timeout_on_answer_awards_no_coins(monkeypatch):
    ctx, economy, increment_spy, award_spy, sent = make_ctx(monkeypatch, economy_doc={"wallet": 0})
    monkeypatch.setattr(main, "RiddleLevelView", lambda c: FakeLevelView(c, "easy"))
    monkeypatch.setattr(main.random, "choice", lambda seq: seq[0])

    async def raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr(main.bot, "wait_for", raise_timeout)

    await main.riddle.callback(ctx)

    assert economy.doc.get("wallet", 0) == 0
    assert any("Time's up" in (s or "") for s in sent)
    increment_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_riddle_no_level_picked_resets_cooldown_and_skips_xp(monkeypatch):
    ctx, economy, increment_spy, award_spy, sent = make_ctx(monkeypatch, economy_doc={"wallet": 0})
    monkeypatch.setattr(main, "RiddleLevelView", lambda c: FakeLevelView(c, None))

    await main.riddle.callback(ctx)

    ctx.command.reset_cooldown.assert_called_once_with(ctx)
    assert getattr(ctx, "_skip_xp_award", False) is False  # xp_earn wrapper reads then clears the flag
    # Only the initial difficulty prompt should have been sent - no riddle, no result.
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_riddle_answer_matches_ignores_case_and_punctuation():
    assert main._riddle_answer_matches("Egg.", ["an egg", "egg"])
    assert main._riddle_answer_matches("  EGG!  ", ["an egg", "egg"])
    assert not main._riddle_answer_matches("legguminous", ["egg"])
    assert not main._riddle_answer_matches("", ["egg"])
