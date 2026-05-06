"""Regression tests for the per-message-id dedup guard on ``on_message``.

The user has hit a "every command runs twice" symptom multiple times in
production whose root cause is hard to pin down (see ``bot/client.py``
docstring). The dedup guard at the bot level makes the symptom impossible
even if a duplicate ``process_commands`` call sneaks back in, the bot ends
up registered as two listeners, or two bot processes end up sharing a
gateway.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.client import DuckParadiseBot


def _make_message(message_id: int, *, author_bot: bool = False) -> SimpleNamespace:
    """Minimal stand-in for ``discord.Message`` for the dedup pathway."""
    return SimpleNamespace(
        id=message_id,
        author=SimpleNamespace(bot=author_bot, __str__=lambda self: "TestUser"),
        guild=SimpleNamespace(name="TestGuild"),
    )


@pytest.mark.asyncio
async def test_on_message_processes_first_message():
    bot = DuckParadiseBot.__new__(DuckParadiseBot)
    # Initialise just the dedup state we care about, without spinning up
    # discord.py's gateway machinery.
    from collections import OrderedDict

    bot._processed_messages = OrderedDict()
    bot.process_commands = AsyncMock()

    msg = _make_message(1)
    await DuckParadiseBot.on_message(bot, msg)

    bot.process_commands.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_on_message_dedupes_repeated_message():
    """Two on_message calls for the same message id → only one process_commands."""
    from collections import OrderedDict

    bot = DuckParadiseBot.__new__(DuckParadiseBot)
    bot._processed_messages = OrderedDict()
    bot.process_commands = AsyncMock()

    msg = _make_message(42)

    await DuckParadiseBot.on_message(bot, msg)
    await DuckParadiseBot.on_message(bot, msg)
    await DuckParadiseBot.on_message(bot, msg)

    assert bot.process_commands.await_count == 1, (
        "process_commands must run exactly once per unique message id "
        "even if on_message fires multiple times"
    )


@pytest.mark.asyncio
async def test_on_message_skips_bot_messages():
    from collections import OrderedDict

    bot = DuckParadiseBot.__new__(DuckParadiseBot)
    bot._processed_messages = OrderedDict()
    bot.process_commands = AsyncMock()

    bot_msg = _make_message(7, author_bot=True)
    await DuckParadiseBot.on_message(bot, bot_msg)

    bot.process_commands.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_distinct_messages_both_run():
    """Different message ids should both reach process_commands."""
    from collections import OrderedDict

    bot = DuckParadiseBot.__new__(DuckParadiseBot)
    bot._processed_messages = OrderedDict()
    bot.process_commands = AsyncMock()

    await DuckParadiseBot.on_message(bot, _make_message(100))
    await DuckParadiseBot.on_message(bot, _make_message(101))
    await DuckParadiseBot.on_message(bot, _make_message(102))

    assert bot.process_commands.await_count == 3


@pytest.mark.asyncio
async def test_dedup_set_evicts_expired_entries(monkeypatch):
    """Old message ids get evicted so the dedup set doesn't grow forever."""
    from collections import OrderedDict

    import bot.client as client_mod

    bot = DuckParadiseBot.__new__(DuckParadiseBot)
    bot._processed_messages = OrderedDict()
    bot.process_commands = AsyncMock()

    # Shrink the TTL for the test so we don't have to wait.
    monkeypatch.setattr(client_mod, "_MESSAGE_DEDUP_TTL", 0.05)

    await DuckParadiseBot.on_message(bot, _make_message(500))
    assert 500 in bot._processed_messages

    await asyncio.sleep(0.06)
    # The eviction is opportunistic; trigger it by checking another id.
    bot._evict_expired_message_ids()
    assert 500 not in bot._processed_messages


@pytest.mark.asyncio
async def test_dedup_set_is_bounded(monkeypatch):
    """Even without TTL pressure, the set never exceeds the max bound."""
    from collections import OrderedDict

    import bot.client as client_mod

    bot = DuckParadiseBot.__new__(DuckParadiseBot)
    bot._processed_messages = OrderedDict()
    bot.process_commands = AsyncMock()

    # Cap the bound very low to keep the test fast.
    monkeypatch.setattr(client_mod, "_MESSAGE_DEDUP_MAX_ENTRIES", 5)

    for i in range(20):
        await DuckParadiseBot.on_message(bot, _make_message(i))

    assert len(bot._processed_messages) <= 5


if __name__ == "__main__":
    pytest.main([__file__])
