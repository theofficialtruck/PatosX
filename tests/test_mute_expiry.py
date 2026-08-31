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

"""Tests for mute expiry: the shared parse_mute_end/remove_mute_role helpers, the
on_ready restart-reapply path, the on_member_join rejoin-reapply path, and the
mutes_col schema (key types / mute_end format) staying consistent across every
writer and reader. Regression coverage for a bug where an already-expired mute's
DB record was deleted without ever removing the Discord role, leaving members
stuck muted forever after a bot restart or a rejoin."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import main

# === parse_mute_end =============================================================================


def test_parse_mute_end_none_returns_none():
    assert main.parse_mute_end(None) is None


def test_parse_mute_end_aware_datetime_passthrough():
    dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert main.parse_mute_end(dt) == dt


def test_parse_mute_end_naive_datetime_gets_utc():
    dt = datetime(2025, 1, 1)  # noqa: DTZ001 - intentionally naive, testing the UTC-attach path
    result = main.parse_mute_end(dt)
    assert result.tzinfo == timezone.utc


def test_parse_mute_end_iso_string_parses():
    dt = datetime(2025, 6, 1, 12, 30, tzinfo=timezone.utc)
    result = main.parse_mute_end(dt.isoformat())
    assert result == dt


def test_parse_mute_end_legacy_string_format_parses():
    result = main.parse_mute_end("2025-06-01 12:30:00")
    assert result == datetime(2025, 6, 1, 12, 30, tzinfo=timezone.utc)


def test_parse_mute_end_garbage_string_returns_none():
    assert main.parse_mute_end("not a date") is None


# === remove_mute_role ============================================================================


@pytest.mark.asyncio
async def test_remove_mute_role_removes_when_present():
    mute_role = SimpleNamespace(name="Muted")
    guild = SimpleNamespace(roles=[mute_role])
    member = SimpleNamespace(roles=[mute_role], remove_roles=AsyncMock())

    result = await main.remove_mute_role(guild, member, reason="test")

    assert result is True
    member.remove_roles.assert_awaited_once_with(mute_role, reason="test")


@pytest.mark.asyncio
async def test_remove_mute_role_noop_when_member_missing_role():
    mute_role = SimpleNamespace(name="Muted")
    guild = SimpleNamespace(roles=[mute_role])
    member = SimpleNamespace(roles=[], remove_roles=AsyncMock())

    result = await main.remove_mute_role(guild, member, reason="test")

    assert result is False
    member.remove_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_mute_role_noop_when_no_muted_role_in_guild():
    guild = SimpleNamespace(roles=[])
    member = SimpleNamespace(roles=[], remove_roles=AsyncMock())

    result = await main.remove_mute_role(guild, member, reason="test")

    assert result is False
    member.remove_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_mute_role_swallows_forbidden():
    mute_role = SimpleNamespace(name="Muted")
    guild = SimpleNamespace(roles=[mute_role])
    forbidden = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), None)
    member = SimpleNamespace(roles=[mute_role], remove_roles=AsyncMock(side_effect=forbidden))

    result = await main.remove_mute_role(guild, member, reason="test")

    assert result is True  # removal was attempted, even though it failed


# === _reapply_or_clear_mute (on_ready restart path) =============================================


@pytest.mark.asyncio
async def test_reapply_or_clear_mute_removes_role_when_expired(monkeypatch):
    """Regression test for the primary bug: an expired mute must delete the record
    AND remove the role together - never delete without removing."""
    mute_role = SimpleNamespace(name="Muted")
    guild = SimpleNamespace(roles=[mute_role])
    member = SimpleNamespace(roles=[mute_role], add_roles=AsyncMock(), remove_roles=AsyncMock())
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    doc = {"_id": "doc1", "mute_end": past}

    delete_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "delete_one", delete_one)

    await main._reapply_or_clear_mute(guild, member, doc, mute_role)

    member.remove_roles.assert_awaited_once_with(mute_role, reason="Mute expired while bot was offline")
    member.add_roles.assert_not_awaited()
    delete_one.assert_awaited_once_with({"_id": "doc1"})


@pytest.mark.asyncio
async def test_reapply_or_clear_mute_keeps_active_mute(monkeypatch):
    mute_role = SimpleNamespace(name="Muted")
    guild = SimpleNamespace(roles=[mute_role])
    member = SimpleNamespace(roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    doc = {"_id": "doc1", "mute_end": future}

    delete_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "delete_one", delete_one)

    await main._reapply_or_clear_mute(guild, member, doc, mute_role)

    member.add_roles.assert_awaited_once_with(mute_role, reason="Reapplying mute after restart")
    member.remove_roles.assert_not_awaited()
    delete_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_reapply_or_clear_mute_indefinite_mute_reapplies_role(monkeypatch):
    mute_role = SimpleNamespace(name="Muted")
    guild = SimpleNamespace(roles=[mute_role])
    member = SimpleNamespace(roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
    doc = {"_id": "doc1"}  # no mute_end at all -> indefinite mute

    delete_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "delete_one", delete_one)

    await main._reapply_or_clear_mute(guild, member, doc, mute_role)

    member.add_roles.assert_awaited_once_with(mute_role, reason="Reapplying mute after restart")
    delete_one.assert_not_awaited()


# === _handle_rejoin_mute (on_member_join rejoin path) ============================================


@pytest.mark.asyncio
async def test_handle_rejoin_mute_does_not_reapply_expired(monkeypatch):
    """Regression test for the rejoin variant of the primary bug: a member rejoining
    after their mute already expired must NOT be re-muted."""
    mute_role = SimpleNamespace(name="Muted")
    guild = SimpleNamespace(id=1, roles=[mute_role])
    member = SimpleNamespace(id=2, guild=guild, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    doc = {"mute_end": past}

    delete_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "delete_one", delete_one)
    create_task = MagicMock()
    monkeypatch.setattr(main.bot, "loop", SimpleNamespace(create_task=create_task))

    await main._handle_rejoin_mute(member, doc)

    member.add_roles.assert_not_awaited()
    delete_one.assert_awaited_once_with({"guild_id": 1, "user_id": 2})
    create_task.assert_not_called()


@pytest.mark.asyncio
async def test_handle_rejoin_mute_reapplies_active(monkeypatch):
    mute_role = SimpleNamespace(name="Muted")
    guild = SimpleNamespace(id=1, roles=[mute_role])
    member = SimpleNamespace(id=2, guild=guild, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    doc = {"mute_end": future}

    delete_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "delete_one", delete_one)
    # Close the coroutine handed to create_task instead of letting it leak unawaited.
    create_task = MagicMock(side_effect=lambda coro: coro.close())
    monkeypatch.setattr(main.bot, "loop", SimpleNamespace(create_task=create_task))

    await main._handle_rejoin_mute(member, doc)

    member.add_roles.assert_awaited_once_with(mute_role, reason="Reapplying mute after rejoin")
    delete_one.assert_not_awaited()
    create_task.assert_called_once()


# === mutes_col schema consistency ================================================================


@pytest.mark.asyncio
async def test_mute_command_stores_int_ids(monkeypatch):
    """Regression test: .mute must write guild_id/user_id as int so on_ready's restart
    query and .unmute's delete (both int-keyed) can actually find the record."""
    ctx = MagicMock()
    member = MagicMock()
    mute_role = MagicMock()
    ctx.guild.id = 123
    ctx.guild.roles = [mute_role]
    mute_role.name = "Muted"
    ctx.guild.channels = []
    member.roles = []
    member.add_roles = AsyncMock()
    ctx.author = MagicMock(name="Mod", id=999)
    member.id = 888
    member.mention = "@User"
    main.actions_data.clear()
    monkeypatch.setattr(main, "check_target_permission", lambda ctx, m: None)
    update_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "update_one", update_one)

    await main.mute(ctx, member, duration="10s", reason="Spamming")

    update_one.assert_awaited_once()
    call_filter, call_update = update_one.call_args[0][0], update_one.call_args[0][1]
    assert call_filter == {"guild_id": 123, "user_id": 888}
    assert isinstance(call_update["$set"]["guild_id"], int)
    assert isinstance(call_update["$set"]["user_id"], int)
    assert isinstance(call_update["$set"]["mute_end"], str)
    # actions_data must keep using string keys - unrelated to mutes_col's schema
    assert str(ctx.guild.id) in main.actions_data
    assert str(member.id) in main.actions_data[str(ctx.guild.id)]


@pytest.mark.asyncio
async def test_unmute_deletes_mute_written_by_mute_command(monkeypatch):
    """Direct regression guard for the type-mismatch bug: .mute's upsert filter and
    .unmute's delete filter must agree on key types so the delete actually matches."""
    ctx = MagicMock()
    member = MagicMock()
    mute_role = MagicMock()
    ctx.guild.id = 123
    ctx.guild.roles = [mute_role]
    mute_role.name = "Muted"
    ctx.guild.channels = []
    ctx.send = AsyncMock()
    member.roles = []
    member.add_roles = AsyncMock()
    ctx.author = MagicMock(name="Mod", id=999)
    member.id = 888
    member.mention = "@User"
    main.actions_data.clear()
    monkeypatch.setattr(main, "check_target_permission", lambda ctx, m: None)
    mute_update_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "update_one", mute_update_one)

    await main.mute(ctx, member, duration="10s", reason="Spamming")
    mute_filter = mute_update_one.call_args[0][0]

    # .unmute removes the role directly and deletes by the same filter shape
    member.roles = [mute_role]
    member.remove_roles = AsyncMock()
    unmute_delete_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "delete_one", unmute_delete_one)
    monkeypatch.setattr(main, "log_action", AsyncMock())

    await main.unmute(ctx, member)

    unmute_filter = unmute_delete_one.call_args[0][0]
    assert unmute_filter == mute_filter


@pytest.mark.asyncio
async def test_execute_moderation_mute_stores_iso_string_mute_end(monkeypatch):
    """The moderation-panel mute path must store mute_end as an ISO string, matching
    .mute's format, instead of a raw datetime that other readers don't expect."""
    ctx = MagicMock()
    member = MagicMock()
    mute_role = MagicMock()
    ctx.guild.id = 123
    ctx.guild.roles = [mute_role]
    mute_role.name = "Muted"
    ctx.guild.channels = []
    member.roles = []
    member.add_roles = AsyncMock()
    member.id = 888
    member.mention = "@User"
    ctx.author = MagicMock(name="Mod", id=999)

    monkeypatch.setattr(main.mod_col, "update_one", AsyncMock())
    mutes_update_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "update_one", mutes_update_one)
    monkeypatch.setattr(main, "log_action", AsyncMock())

    view = main.ModerationConfirmView(action="mute", member=member, reason="Spamming", duration="10s", ctx=ctx)
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view.execute_moderation(interaction)

    mutes_update_one.assert_awaited_once()
    stored_mute_end = mutes_update_one.call_args[0][1]["$set"]["mute_end"]
    assert isinstance(stored_mute_end, str)
    from dateutil import parser as _parser

    _parser.isoparse(stored_mute_end)  # must not raise


# === schedule_unmute =============================================================================


@pytest.mark.asyncio
async def test_schedule_unmute_handles_member_left(monkeypatch):
    """Regression test: previously reassigning `member` to None (member left) then
    reading member.id on the next line raised AttributeError, silently swallowed and
    skipping the mutes_col cleanup."""
    original_member = SimpleNamespace(id=42)
    guild = SimpleNamespace(id=1, get_member=MagicMock(return_value=None))

    delete_one = AsyncMock()
    monkeypatch.setattr(main.mutes_col, "delete_one", delete_one)
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())

    await main.schedule_unmute(guild, original_member, 0)

    delete_one.assert_awaited_once_with({"guild_id": 1, "user_id": 42})
