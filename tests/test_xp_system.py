"""Regression tests for the XP awarding pipeline.

The user has reported two specific bugs in the past:

1. XP was awarded **twice** per command (different amounts each time, e.g.
   ``.work`` would yield "33 xp" then "34 xp"). Caused by ``on_message``
   calling ``bot.process_commands`` on top of the implicit call inside
   ``commands.Bot``.

2. XP was awarded for ``.fish`` even when the command was on cooldown.
   Caused by the same duplicate-``process_commands`` regression: the first
   pass succeeded and awarded XP, the second pass tripped the cooldown
   check and surfaced the cooldown error.

These tests pin down both behaviours so they cannot silently regress.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.cogs.economy as econ_mod
import bot.utils.checks as checks_mod
from bot.cogs.economy import EconomyCog


def _build_ctx(*, interaction=None) -> SimpleNamespace:
    guild = SimpleNamespace(id=123, members=[])
    author = SimpleNamespace(
        id=456, mention="@TestUser", display_name="TestUser"
    )
    cmd = SimpleNamespace(name="balance")
    return SimpleNamespace(
        guild=guild,
        author=author,
        command=cmd,
        send=AsyncMock(),
        interaction=interaction,
    )


def _patch_economy(monkeypatch) -> tuple[MagicMock, MagicMock]:
    """Stub the database access used by the balance cog command."""
    mock_xp_col = MagicMock()
    mock_xp_col.update_one = AsyncMock()
    monkeypatch.setattr(checks_mod, "xp_col", mock_xp_col)
    monkeypatch.setattr(
        econ_mod, "check_channel", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        econ_mod,
        "get_user",
        AsyncMock(return_value={"wallet": 100, "bank": 50}),
    )
    mock_econ = MagicMock()
    mock_econ.find_one = AsyncMock(return_value={"wallet": 100, "bank": 50})
    monkeypatch.setattr(econ_mod, "economy_col", mock_econ)
    return mock_xp_col, mock_econ


def _xp_messages(send_mock: AsyncMock) -> list:
    """Pull only the XP-announcement messages out of a send mock."""
    return [
        c
        for c in send_mock.await_args_list
        if c.args and "earned" in str(c.args[0]) and "xp" in str(c.args[0])
    ]


# ---------------------------------------------------------------------------
# Single-award regression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_xp_awarded_exactly_once_per_invocation(monkeypatch):
    """``xp_earn`` writes the DB once and sends one chat message."""
    mock_xp_col, _ = _patch_economy(monkeypatch)
    cog = EconomyCog(MagicMock())
    ctx = _build_ctx()

    await cog.balance.callback(cog, ctx, None)

    assert mock_xp_col.update_one.call_count == 1, (
        "DB write should fire exactly once per command"
    )
    assert len(_xp_messages(ctx.send)) == 1, (
        "Exactly one XP announcement should be sent per command"
    )


@pytest.mark.asyncio
async def test_double_invocation_only_awards_once(monkeypatch):
    """If the wrapper somehow runs twice for the same Context, only the
    first run awards XP; the second is a no-op thanks to the
    ``_xp_already_awarded`` sentinel.

    This protects against the historical duplicate-``process_commands``
    regression that produced the double-XP screenshots.
    """
    mock_xp_col, _ = _patch_economy(monkeypatch)
    cog = EconomyCog(MagicMock())
    ctx = _build_ctx()

    await cog.balance.callback(cog, ctx, None)
    await cog.balance.callback(cog, ctx, None)

    assert mock_xp_col.update_one.call_count == 1, (
        "Re-running the wrapper on the same Context must not double-award XP"
    )
    assert len(_xp_messages(ctx.send)) == 1, (
        "Re-running the wrapper on the same Context must not send a second "
        "XP message"
    )


# ---------------------------------------------------------------------------
# Slash invocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_xp_awarded_on_slash_invocation(monkeypatch):
    mock_xp_col, _ = _patch_economy(monkeypatch)

    response = SimpleNamespace(
        is_done=MagicMock(return_value=True),
        send_message=AsyncMock(),
    )
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)

    cog = EconomyCog(MagicMock())
    ctx = _build_ctx(interaction=interaction)

    await cog.balance.callback(cog, ctx, None)

    assert mock_xp_col.update_one.call_count == 1
    all_calls = ctx.send.await_args_list + followup.send.await_args_list
    xp_calls = [
        c
        for c in all_calls
        if c.args and "earned" in str(c.args[0]) and "xp" in str(c.args[0])
    ]
    assert len(xp_calls) == 1


# ---------------------------------------------------------------------------
# Resilience — DB and send failures must not crash the whole invocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_xp_message_sent_even_when_db_fails(monkeypatch):
    """A flaky Mongo write must not block the user-facing announcement."""
    mock_xp_col = MagicMock()
    mock_xp_col.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
    monkeypatch.setattr(checks_mod, "xp_col", mock_xp_col)
    monkeypatch.setattr(
        econ_mod, "check_channel", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        econ_mod,
        "get_user",
        AsyncMock(return_value={"wallet": 100, "bank": 50}),
    )
    mock_econ = MagicMock()
    mock_econ.find_one = AsyncMock(return_value={"wallet": 100, "bank": 50})
    monkeypatch.setattr(econ_mod, "economy_col", mock_econ)

    cog = EconomyCog(MagicMock())
    ctx = _build_ctx()

    await cog.balance.callback(cog, ctx, None)

    assert _xp_messages(ctx.send), (
        "XP message must still send even when Mongo update fails"
    )


@pytest.mark.asyncio
async def test_xp_falls_back_to_followup_when_send_fails(monkeypatch):
    """If ``ctx.send`` raises, the wrapper falls back to interaction followup."""
    mock_xp_col, _ = _patch_economy(monkeypatch)

    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(
        response=SimpleNamespace(is_done=MagicMock(return_value=True)),
        followup=followup,
    )

    cog = EconomyCog(MagicMock())
    ctx = _build_ctx(interaction=interaction)

    # First send (the embed) succeeds, second send (the XP message) fails so
    # we exercise the followup fallback.
    call_count = {"n": 0}

    async def flaky_send(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("missing perms")

    ctx.send = AsyncMock(side_effect=flaky_send)

    await cog.balance.callback(cog, ctx, None)

    followup.send.assert_awaited_once()
    sent_text = followup.send.await_args.args[0]
    assert "earned" in sent_text and "xp" in sent_text


# ---------------------------------------------------------------------------
# Skip-XP escape hatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_xp_award_short_circuits(monkeypatch):
    """``ctx._skip_xp_award`` must skip both the DB write and the message."""
    mock_xp_col, _ = _patch_economy(monkeypatch)
    cog = EconomyCog(MagicMock())
    ctx = _build_ctx()

    # Coinflip is the canonical caller of ``_skip_xp_award``; balance does
    # not set it, so we set it manually to prove the wrapper respects it.
    ctx._skip_xp_award = True

    await cog.balance.callback(cog, ctx, None)

    mock_xp_col.update_one.assert_not_awaited()
    assert not _xp_messages(ctx.send), (
        "XP must not be awarded when _skip_xp_award is set"
    )


# ---------------------------------------------------------------------------
# Cooldown protection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wrapper_does_not_run_when_cooldown_blocks(monkeypatch):
    """If discord.py never invokes the callback (cooldown / check failure),
    the wrapper logically can't fire — but if it *did* fire and the inner
    function raised ``CommandOnCooldown``, the wrapper must not award XP.

    This pins the contract: an exception inside the inner function bubbles
    up cleanly and XP is *not* awarded.
    """
    from discord.ext import commands as dcommands

    mock_xp_col = MagicMock()
    mock_xp_col.update_one = AsyncMock()
    monkeypatch.setattr(checks_mod, "xp_col", mock_xp_col)

    @checks_mod.xp_earn(10, 20)
    async def fake_command(ctx):
        # Mimic discord.py raising the cooldown error from the callback path.
        raise dcommands.CommandOnCooldown(  # type: ignore[call-arg]
            cooldown=dcommands.Cooldown(rate=1, per=60),
            retry_after=42.0,
            type=dcommands.BucketType.member,
        )

    ctx = _build_ctx()

    with pytest.raises(dcommands.CommandOnCooldown):
        await fake_command(ctx)

    mock_xp_col.update_one.assert_not_awaited()
    assert not _xp_messages(ctx.send)


if __name__ == "__main__":
    pytest.main([__file__])
