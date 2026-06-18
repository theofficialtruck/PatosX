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

"""Tests for the birthday event bonus (+100 coins on all coin-earning activities)."""

import pytest
from unittest.mock import AsyncMock, MagicMock
import main


# === Constants ================================================================


def test_birthday_event_bonus_is_100():
    assert main.BIRTHDAY_EVENT_BONUS == 100


def test_birthday_event_active_is_bool():
    assert isinstance(main.BIRTHDAY_EVENT_ACTIVE, bool)


def test_birthday_event_active_is_true():
    """Birthday event must be active today (2026-06-18)."""
    assert main.BIRTHDAY_EVENT_ACTIVE is True


# === beg command ==============================================================


@pytest.mark.asyncio
async def test_beg_birthday_bonus_applied_when_active(monkeypatch):
    """When BIRTHDAY_EVENT_ACTIVE is True, beg amount increases by BIRTHDAY_EVENT_BONUS."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {"_id": "100-200", "wallet": 0, "bank": 0, "inventory": [], "last_beg": None}
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "BIRTHDAY_EVENT_ACTIVE", True)
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(_random, "randint", lambda a, b: 100)  # fixed base: 100

    await main.beg.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    # Base 100 + birthday bonus 100 = 200 coins total
    assert any("200 coins" in t for t in sent_texts), f"Expected 200 coins in output; got: {sent_texts}"
    assert any("birthday" in t.lower() for t in sent_texts), f"Expected birthday message; got: {sent_texts}"


@pytest.mark.asyncio
async def test_beg_birthday_bonus_not_applied_when_inactive(monkeypatch):
    """When BIRTHDAY_EVENT_ACTIVE is False, beg amount is not increased and no birthday message."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {"_id": "100-200", "wallet": 0, "bank": 0, "inventory": [], "last_beg": None}
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "BIRTHDAY_EVENT_ACTIVE", False)
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(_random, "randint", lambda a, b: 100)

    await main.beg.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert not any("birthday" in t.lower() for t in sent_texts), f"Expected no birthday message; got: {sent_texts}"
    # Should only earn 100, not 200
    assert any("100 coins" in t for t in sent_texts), f"Expected 100 coins (no bonus); got: {sent_texts}"


# === work command =============================================================


@pytest.mark.asyncio
async def test_work_birthday_bonus_applied_when_active(monkeypatch):
    """When BIRTHDAY_EVENT_ACTIVE is True, work earnings increase by BIRTHDAY_EVENT_BONUS."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        "_id": "100-200",
        "wallet": 0,
        "bank": 0,
        "inventory": [],
        "job": "duck",
        "promotion_level": 0,
    }
    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=None)  # no active cooldown
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "BIRTHDAY_EVENT_ACTIVE", True)
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())
    monkeypatch.setattr(_random, "randint", lambda a, b: 200)  # fixed base earnings: 200

    await main.work.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    # Base 200 + birthday bonus 100 = 300 coins total
    assert any("300 coins" in t for t in sent_texts), f"Expected 300 coins in output; got: {sent_texts}"
    assert any("birthday" in t.lower() for t in sent_texts), f"Expected birthday message; got: {sent_texts}"


@pytest.mark.asyncio
async def test_work_birthday_bonus_not_applied_when_inactive(monkeypatch):
    """When BIRTHDAY_EVENT_ACTIVE is False, work earnings are not boosted."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        "_id": "100-200",
        "wallet": 0,
        "bank": 0,
        "inventory": [],
        "job": "duck",
        "promotion_level": 0,
    }
    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=None)
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "BIRTHDAY_EVENT_ACTIVE", False)
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())
    monkeypatch.setattr(_random, "randint", lambda a, b: 200)

    await main.work.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert not any("birthday" in t.lower() for t in sent_texts), f"Expected no birthday message; got: {sent_texts}"
    assert any("200 coins" in t for t in sent_texts), f"Expected 200 coins (no bonus); got: {sent_texts}"


# === bugcatch command ==========================================================


@pytest.mark.asyncio
async def test_bugcatch_birthday_bonus_applied_when_active(monkeypatch):
    """When BIRTHDAY_EVENT_ACTIVE is True, bugcatch earnings increase by BIRTHDAY_EVENT_BONUS."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None
    ctx.command = MagicMock()
    ctx.command.reset_cooldown = MagicMock()

    user_data = {"_id": "100-200", "wallet": 0, "bank": 0, "inventory": ["butterfly net"]}
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    def fake_consume(inventory, key):
        for i, item in enumerate(inventory):
            if item == key:
                inventory.pop(i)
                return True, False, None
        return True, False, None  # pretend consumed, not broken

    monkeypatch.setattr(main, "BIRTHDAY_EVENT_ACTIVE", True)
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(main, "increment_badge_counter", AsyncMock())
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())
    monkeypatch.setattr(main, "consume_tool_use", fake_consume)
    monkeypatch.setattr(_random, "choice", lambda seq: ("🦋 butterfly", 180))

    await main.bugcatch.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    # Base 180 + birthday bonus 100 = 280 coins total
    assert any("280 coins" in t for t in sent_texts), f"Expected 280 coins; got: {sent_texts}"
    assert any("birthday" in t.lower() for t in sent_texts), f"Expected birthday message; got: {sent_texts}"


@pytest.mark.asyncio
async def test_bugcatch_birthday_bonus_not_applied_when_inactive(monkeypatch):
    """When BIRTHDAY_EVENT_ACTIVE is False, bugcatch earnings are not boosted."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None
    ctx.command = MagicMock()
    ctx.command.reset_cooldown = MagicMock()

    user_data = {"_id": "100-200", "wallet": 0, "bank": 0, "inventory": ["butterfly net"]}
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    def fake_consume(inventory, key):
        return True, False, None

    monkeypatch.setattr(main, "BIRTHDAY_EVENT_ACTIVE", False)
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, "economy_col", mock_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "add_balance", AsyncMock())
    monkeypatch.setattr(main, "increment_badge_counter", AsyncMock())
    monkeypatch.setattr(main, "check_and_award_badges", AsyncMock())
    monkeypatch.setattr(main, "consume_tool_use", fake_consume)
    monkeypatch.setattr(_random, "choice", lambda seq: ("🦋 butterfly", 180))

    await main.bugcatch.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert not any("birthday" in t.lower() for t in sent_texts), f"Expected no birthday message; got: {sent_texts}"
    assert any("180 coins" in t for t in sent_texts), f"Expected 180 coins (no bonus); got: {sent_texts}"
