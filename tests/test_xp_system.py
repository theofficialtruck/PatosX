"""Tests dedicated to the XP awarding pipeline.

Specifically guards against the regression the user reported in production:
"the XP message doesn't show up after a successful command".
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


@pytest.mark.asyncio
async def test_xp_awarded_on_prefix_invocation(monkeypatch):
    mock_xp_col, _ = _patch_economy(monkeypatch)
    cog = EconomyCog(MagicMock())
    ctx = _build_ctx(interaction=None)

    await cog.balance.callback(cog, ctx, None)

    mock_xp_col.update_one.assert_awaited_once()
    sends = ctx.send.await_args_list
    xp_sends = [c for c in sends if c.args and "earned" in str(c.args[0]) and "xp" in str(c.args[0])]
    assert xp_sends, "XP announcement message was not sent on prefix command"


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

    mock_xp_col.update_one.assert_awaited_once()
    # On slash invocations the wrapper still uses ctx.send first (which is
    # hybrid-aware in discord.py 2.x); we only need to confirm the XP
    # message landed somewhere.
    all_calls = ctx.send.await_args_list + followup.send.await_args_list
    assert any(
        c.args and "earned" in str(c.args[0]) and "xp" in str(c.args[0])
        for c in all_calls
    ), "XP announcement message was not sent on slash command"


@pytest.mark.asyncio
async def test_xp_message_sent_even_when_db_fails(monkeypatch):
    """A flaky Mongo write must not block the user-facing XP announcement."""
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

    xp_sends = [
        c
        for c in ctx.send.await_args_list
        if c.args and "earned" in str(c.args[0]) and "xp" in str(c.args[0])
    ]
    assert xp_sends, "XP message must still send even when Mongo update fails"


@pytest.mark.asyncio
async def test_xp_falls_back_to_followup_when_send_fails(monkeypatch):
    """If ``ctx.send`` raises, the wrapper must try interaction.followup."""
    mock_xp_col, _ = _patch_economy(monkeypatch)

    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(
        response=SimpleNamespace(is_done=MagicMock(return_value=True)),
        followup=followup,
    )

    cog = EconomyCog(MagicMock())
    ctx = _build_ctx(interaction=interaction)

    # The first ctx.send (the embed) succeeds; the second (the XP message)
    # raises so we exercise the followup fallback path.
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


@pytest.mark.asyncio
async def test_skip_xp_award_short_circuits(monkeypatch):
    """``ctx._skip_xp_award`` must skip both the DB write and the message."""
    mock_xp_col, _ = _patch_economy(monkeypatch)
    cog = EconomyCog(MagicMock())
    ctx = _build_ctx()

    # Coinflip is the canonical caller of `_skip_xp_award`; balance does
    # not set it, so we set it manually to prove the wrapper respects it.
    ctx._skip_xp_award = True

    await cog.balance.callback(cog, ctx, None)

    mock_xp_col.update_one.assert_not_awaited()
    xp_sends = [
        c
        for c in ctx.send.await_args_list
        if c.args and "earned" in str(c.args[0]) and "xp" in str(c.args[0])
    ]
    assert not xp_sends, "XP must not be awarded when _skip_xp_award is set"


if __name__ == "__main__":
    pytest.main([__file__])
