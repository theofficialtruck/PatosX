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

"""Investment price movement: gains are a little more common than losses, and a fresh purchase stays
flat for its first INVESTMENT_GRACE_DAYS days."""

import random
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import economyHelperFuncs as econ
from core import state


@pytest.fixture
def update_one(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(state, "investments_col", SimpleNamespace(update_one=mock))
    return mock


def fresh_investment(amount=1000, bought="2026-05-10", inv_id="inv-1"):
    return {
        "_id": inv_id,
        "amount": amount,
        "current_value": amount,
        "history": [],
        "date": f"{bought}T08:00:00+00:00",
        "last_status_refresh_date": bought,
    }


def at(day):
    return datetime.fromisoformat(f"{day}T12:00:00+00:00").astimezone(timezone.utc)


def test_gains_are_more_common_than_losses_but_not_dramatic():
    pcts, weights = econ._DAILY_CHANGE_PCTS, econ._DAILY_CHANGE_WEIGHTS
    total = sum(weights)
    p_gain = sum(w for pct, w in zip(pcts, weights, strict=True) if pct > 0) / total
    expected = sum(pct * w for pct, w in zip(pcts, weights, strict=True)) / total
    assert 0.5 < p_gain <= 0.65
    assert 0 < expected <= 0.01  # at most about 1 percent a day on average, so no sky rocketing
    assert max(abs(pct) for pct in pcts) <= 0.03


def test_sampled_changes_drift_upwards():
    random.seed(1234)
    samples = [econ.pick_daily_investment_change_pct() for _ in range(20000)]
    assert sum(1 for pct in samples if pct > 0) > sum(1 for pct in samples if pct < 0)
    assert sum(samples) > 0


@pytest.mark.asyncio
async def test_value_is_flat_for_the_two_days_after_purchase(update_one):
    for day in ("2026-05-10", "2026-05-11", "2026-05-12"):
        refreshed = await econ.refresh_user_investments_for_today([fresh_investment()], now=at(day))
        assert refreshed[0]["current_value"] == 1000, day
        assert refreshed[0]["history"] == []


@pytest.mark.asyncio
async def test_first_change_happens_on_the_third_day(monkeypatch, update_one):
    monkeypatch.setattr(econ, "pick_daily_investment_change_pct", lambda: 0.02)
    refreshed = await econ.refresh_user_investments_for_today([fresh_investment()], now=at("2026-05-13"))
    assert refreshed[0]["current_value"] == 1020
    assert refreshed[0]["history"] == [20]


@pytest.mark.asyncio
async def test_grace_period_is_kept_when_the_first_check_comes_late(monkeypatch, update_one):
    """Checking a week after purchase must still skip only the two grace days, not the whole gap."""
    monkeypatch.setattr(econ, "pick_daily_investment_change_pct", lambda: 0.01)
    refreshed = await econ.refresh_user_investments_for_today([fresh_investment()], now=at("2026-05-17"))
    assert len(refreshed[0]["history"]) == 5  # May 13 to May 17
    assert refreshed[0]["current_value"] > 1000


@pytest.mark.asyncio
async def test_records_without_a_purchase_date_are_not_held_back(monkeypatch, update_one):
    monkeypatch.setattr(econ, "pick_daily_investment_change_pct", lambda: 0.02)
    legacy = {"_id": "old", "amount": 1000, "current_value": 1000, "history": []}
    refreshed = await econ.refresh_user_investments_for_today([legacy], now=at("2026-05-13"))
    assert refreshed[0]["current_value"] == 1020


@pytest.mark.asyncio
async def test_investments_grow_a_few_hundred_coins_a_week_on_average(update_one):
    """A week of real (seeded per investment and day) price moves, after the grace period, on a big
    position should average a modest gain: enough to feel hopeful, nowhere near exponential."""
    investments = [fresh_investment(amount=5000, inv_id=f"inv-{i}") for i in range(300)]
    refreshed = await econ.refresh_user_investments_for_today(investments, now=at("2026-05-19"))  # 7 live days
    gains = [inv["current_value"] - 5000 for inv in refreshed]
    average = sum(gains) / len(gains)
    assert 0 < average < 600
    assert sum(1 for gain in gains if gain > 0) > sum(1 for gain in gains if gain < 0)
