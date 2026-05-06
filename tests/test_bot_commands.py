"""Smoke tests for the refactored DuckParadise bot.

The test suite focuses on pure utilities and on Cog-method behaviour that
can be exercised without spinning up the gateway. Each test mocks the
narrow database surface a particular code path needs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main  # noqa: F401 — verifies the public re-export surface still imports
from bot.cogs.economy import EconomyCog
from bot.cogs.games import GamesCog
from bot.cogs.investments import InvestmentsCog
from bot.cogs.moderation import ModerationCog
from bot.cogs.tickets import TicketsCog
from bot.utils import economy as economy_utils
from bot.utils import investments as investments_utils
from bot.utils.errors import (
    is_discord_service_unavailable_error,
    send_hybrid_error,
)
from discord.ext import commands


# ---------------------------------------------------------------------------
# Discord-service-unavailable detection
# ---------------------------------------------------------------------------

def test_detect_discord_service_unavailable_from_wrapped_error():
    wrapped = commands.CommandInvokeError(
        RuntimeError(
            "DiscordServerError: 503 Service Unavailable (error code: 0): "
            "upstream connect error"
        )
    )
    assert is_discord_service_unavailable_error(wrapped) is True


def test_do_not_treat_generic_invoke_error_as_service_unavailable():
    wrapped = commands.CommandInvokeError(RuntimeError("some unrelated failure"))
    assert is_discord_service_unavailable_error(wrapped) is False


# ---------------------------------------------------------------------------
# Hybrid error responses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_hybrid_error_uses_ephemeral_initial_response_for_slash():
    response = SimpleNamespace(
        is_done=MagicMock(return_value=False),
        send_message=AsyncMock(),
    )
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())

    await send_hybrid_error(ctx, content="⚠️ test")

    response.send_message.assert_awaited_once()
    kwargs = response.send_message.await_args.kwargs
    assert kwargs["content"] == "⚠️ test"
    assert kwargs["ephemeral"] is True
    followup.send.assert_not_awaited()
    ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_hybrid_error_uses_ephemeral_followup_when_response_done_for_slash():
    response = SimpleNamespace(
        is_done=MagicMock(return_value=True),
        send_message=AsyncMock(),
    )
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())

    await send_hybrid_error(ctx, content="⚠️ test")

    followup.send.assert_awaited_once()
    kwargs = followup.send.await_args.kwargs
    assert kwargs["content"] == "⚠️ test"
    assert kwargs["ephemeral"] is True
    response.send_message.assert_not_awaited()
    ctx.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Coinflip cog behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coinflip_error_hides_wrapped_503_details():
    cog = GamesCog(MagicMock())
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.interaction = None
    wrapped = commands.CommandInvokeError(
        RuntimeError("503 Service Unavailable: upstream connect error")
    )

    await cog.coinflip_error.__func__(cog, ctx, wrapped)

    # ``send_hybrid_error`` forwards through keyword arguments; assert on the
    # ``content`` key rather than a positional value.
    ctx.send.assert_awaited_once()
    kwargs = ctx.send.await_args.kwargs
    assert kwargs["content"] == main.DISCORD_SERVICE_UNAVAILABLE_MESSAGE


@pytest.mark.asyncio
async def test_coinflip_zero_bet_does_not_award_xp(monkeypatch):
    cog = GamesCog(MagicMock())
    ctx = MagicMock()
    ctx.guild.id = 123
    ctx.author.id = 456
    ctx.author.mention = "@Tester"
    ctx.command = MagicMock()
    ctx.command.name = "coinflip"
    ctx.send = AsyncMock()

    mock_xp_col = MagicMock()
    mock_xp_col.update_one = AsyncMock()
    monkeypatch.setattr("bot.utils.checks.xp_col", mock_xp_col)
    monkeypatch.setattr(
        "bot.cogs.games.check_channel",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "bot.cogs.games.get_user",
        AsyncMock(return_value={"wallet": 100}),
    )

    # ``coinflip`` is wrapped by the xp_earn decorator; reach the underlying
    # callback via the HybridCommand's ``callback`` attribute.
    await cog.coinflip.callback(cog, ctx, "0")

    ctx.send.assert_awaited_once_with("❌ Invalid amount to coin flip.")
    mock_xp_col.update_one.assert_not_awaited()


# ---------------------------------------------------------------------------
# Ticket close prompt visibility
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ticketclose_slash_prompt_is_public(monkeypatch):
    cog = TicketsCog(MagicMock())
    opener = SimpleNamespace(mention="@opener")
    guild = SimpleNamespace(id=123, get_member=MagicMock(return_value=opener))
    channel = SimpleNamespace(id=456, guild=guild)

    response = SimpleNamespace(
        is_done=MagicMock(return_value=False),
        send_message=AsyncMock(),
    )
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)

    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        interaction=interaction,
        send=AsyncMock(),
    )

    fake_tickets_col = SimpleNamespace(
        find_one=AsyncMock(return_value={"_id": "ticket-1", "owner_id": "999"}),
        update_one=AsyncMock(),
    )
    monkeypatch.setattr("bot.cogs.tickets.tickets_col", fake_tickets_col)

    await cog.ticketclose.callback(cog, ctx)

    response.send_message.assert_awaited_once()
    send_kwargs = response.send_message.await_args.kwargs
    assert send_kwargs["ephemeral"] is False
    assert "confirm closing this ticket" in send_kwargs["content"]
    followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticketclose_slash_prompt_uses_public_followup_when_response_done(monkeypatch):
    cog = TicketsCog(MagicMock())
    opener = SimpleNamespace(mention="@opener")
    guild = SimpleNamespace(id=123, get_member=MagicMock(return_value=opener))
    channel = SimpleNamespace(id=456, guild=guild)

    response = SimpleNamespace(
        is_done=MagicMock(return_value=True),
        send_message=AsyncMock(),
    )
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)

    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        interaction=interaction,
        send=AsyncMock(),
    )

    fake_tickets_col = SimpleNamespace(
        find_one=AsyncMock(return_value={"_id": "ticket-1", "owner_id": "999"}),
        update_one=AsyncMock(),
    )
    monkeypatch.setattr("bot.cogs.tickets.tickets_col", fake_tickets_col)

    await cog.ticketclose.callback(cog, ctx)

    followup.send.assert_awaited_once()
    send_kwargs = followup.send.await_args.kwargs
    assert send_kwargs["ephemeral"] is False
    assert "confirm closing this ticket" in send_kwargs["content"]
    response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticketclose_slash_error_is_ephemeral(monkeypatch):
    cog = TicketsCog(MagicMock())
    guild = SimpleNamespace(id=123, get_member=MagicMock())
    channel = SimpleNamespace(id=456, guild=guild)

    response = SimpleNamespace(
        is_done=MagicMock(return_value=False),
        send_message=AsyncMock(),
    )
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)

    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        interaction=interaction,
        send=AsyncMock(),
    )

    fake_tickets_col = SimpleNamespace(
        find_one=AsyncMock(side_effect=RuntimeError("db failed")),
        update_one=AsyncMock(),
    )
    monkeypatch.setattr("bot.cogs.tickets.tickets_col", fake_tickets_col)

    await cog.ticketclose.callback(cog, ctx)

    response.send_message.assert_awaited_once()
    kwargs = response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs.get("embed") is not None
    followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Investment migrations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_investstatus_handles_legacy_timestamp_without_date(monkeypatch):
    cog = InvestmentsCog(MagicMock())
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456, display_name="Tester")
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    class _Cursor:
        async def to_list(self, length=None):
            return [
                {
                    "_id": "inv-1",
                    "user_id": "123-456",
                    "company": "Techify",
                    "amount": 1000,
                    "timestamp": "2026-04-10T10:00:00+00:00",
                    "history": [],
                }
            ]

    investments_col = SimpleNamespace(
        find=MagicMock(return_value=_Cursor()),
        update_one=AsyncMock(),
    )

    monkeypatch.setattr(
        "bot.cogs.investments.check_channel",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "bot.cogs.investments.investments_col", investments_col
    )
    monkeypatch.setattr(
        "bot.utils.investments.investments_col", investments_col
    )

    await cog.investstatus.callback(cog, ctx)

    # The investstatus callback sends an embed; the @xp_earn wrapper then
    # sends a second "you earned X xp" message. We only care about the
    # embed assertion here.
    assert ctx.send.await_count >= 1
    embed_call = next(
        (c for c in ctx.send.await_args_list if c.kwargs.get("embed")),
        None,
    )
    assert embed_call is not None, "expected an embed to be sent"
    sent_embed = embed_call.kwargs["embed"]
    assert sent_embed is not None
    assert sent_embed.fields
    assert "Date: <t:" in sent_embed.fields[0].value


def test_get_investment_date_handles_invalid_or_missing_values():
    dt_missing = investments_utils.get_investment_date({})
    dt_invalid = investments_utils.get_investment_date({"date": "not-a-date"})
    dt_legacy = investments_utils.get_investment_date(
        {"timestamp": "2026-04-10T10:00:00+00:00"}
    )

    assert dt_missing.tzinfo is not None
    assert dt_invalid.tzinfo is not None
    assert dt_legacy.year == 2026


@pytest.mark.asyncio
async def test_backfill_investment_dates_from_timestamp_is_non_destructive(monkeypatch):
    docs = [
        {"_id": "a", "timestamp": "2026-04-10T10:00:00+00:00"},
        {"_id": "b", "timestamp": "bad-date"},
        {
            "_id": "c",
            "date": "2026-04-09T10:00:00+00:00",
            "timestamp": "2026-04-01T10:00:00+00:00",
        },
    ]

    class _Result:
        def __init__(self, modified_count):
            self.modified_count = modified_count

    class _Cursor:
        def __init__(self, data):
            self._iter = iter(data)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    class _InvestmentsCol:
        def __init__(self, data):
            self.data = data

        def find(self, query):
            filtered = [d for d in self.data if "date" not in d and "timestamp" in d]
            return _Cursor(filtered)

        async def update_one(self, query, update):
            for d in self.data:
                if d.get("_id") == query.get("_id") and "date" not in d:
                    d["date"] = update["$set"]["date"]
                    return _Result(1)
            return _Result(0)

    fake_col = _InvestmentsCol(docs)
    monkeypatch.setattr(
        "bot.utils.investments.investments_col", fake_col
    )

    stats = await investments_utils.backfill_investment_dates_from_timestamp()

    assert stats["scanned"] == 2
    assert stats["updated"] == 1
    assert stats["invalid_timestamp"] == 1
    assert docs[0].get("date") == "2026-04-10T10:00:00+00:00"
    assert docs[1].get("date") is None
    assert docs[2]["date"] == "2026-04-09T10:00:00+00:00"


@pytest.mark.asyncio
async def test_investmigrate_reports_backfill_stats(monkeypatch):
    cog = InvestmentsCog(MagicMock())
    monkeypatch.setattr(
        "bot.cogs.investments.backfill_investment_dates_from_timestamp",
        AsyncMock(
            return_value={
                "scanned": 4,
                "updated": 3,
                "invalid_timestamp": 1,
                "skipped_conflict": 0,
                "write_errors": 0,
            }
        ),
    )

    ctx = SimpleNamespace(send=AsyncMock())
    await cog.investmigrate.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    content = ctx.send.await_args.args[0]
    assert "Updated: `3`" in content
    assert "Write errors: `0`" in content


if __name__ == "__main__":
    pytest.main([__file__])
