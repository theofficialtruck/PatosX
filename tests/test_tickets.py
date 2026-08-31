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
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import main


@pytest.mark.asyncio
async def test_resolve_ticket_opener_uses_cache_when_available():
    """When the member is already cached, no API calls should be made at all."""
    cached_member = SimpleNamespace(id=42, mention="<@42>")
    guild = SimpleNamespace(
        get_member=MagicMock(return_value=cached_member),
        fetch_member=AsyncMock(),
    )
    result = await main.resolve_ticket_opener(guild, "42")
    assert result is cached_member
    guild.fetch_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_ticket_opener_falls_back_to_fetch_member_on_cache_miss():
    """A cache miss (member not in the local cache, e.g. inactive for a while)
    used to be treated as 'opener not found' - this is what made every closed
    ticket transcript record opener_id as None and display 'Opened by: Unknown'."""
    fetched_member = SimpleNamespace(id=42, mention="<@42>")
    guild = SimpleNamespace(
        get_member=MagicMock(return_value=None),
        fetch_member=AsyncMock(return_value=fetched_member),
    )
    result = await main.resolve_ticket_opener(guild, "42")
    assert result is fetched_member
    guild.fetch_member.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_resolve_ticket_opener_falls_back_to_global_user_fetch_when_member_left():
    """If the opener has since left the guild, fetch_member 404s - fall back to a
    global user fetch so the transcript can still show who opened it."""
    not_found = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), None)
    fetched_user = SimpleNamespace(id=42, mention="<@42>")
    guild = SimpleNamespace(
        get_member=MagicMock(return_value=None),
        fetch_member=AsyncMock(side_effect=not_found),
    )
    monkeypatch_bot = AsyncMock(return_value=fetched_user)
    main.bot.fetch_user = monkeypatch_bot
    result = await main.resolve_ticket_opener(guild, "42")
    assert result is fetched_user
    monkeypatch_bot.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_resolve_ticket_opener_returns_none_for_missing_id():
    guild = SimpleNamespace(get_member=MagicMock(), fetch_member=AsyncMock())
    result = await main.resolve_ticket_opener(guild, None)
    assert result is None
    guild.get_member.assert_not_called()


# === resolve_ticket_access_members: allowed_staff restriction ==================================


@pytest.mark.asyncio
async def test_resolve_ticket_access_members_uses_allowed_staff_when_set(monkeypatch):
    """A button with an explicit allowed_staff list restricts access to just those members."""
    staff_member = SimpleNamespace(id=10)
    guild = SimpleNamespace(get_member=MagicMock(side_effect=lambda uid: staff_member if uid == 10 else None))
    category_support = AsyncMock()
    monkeypatch.setattr(main, "get_category_support_members", category_support)

    members = await main.resolve_ticket_access_members(guild, {"allowed_staff": ["10", "999"]}, "support")

    assert members == [staff_member]
    category_support.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_ticket_access_members_skips_invalid_ids():
    guild = SimpleNamespace(get_member=MagicMock(return_value=None))
    members = await main.resolve_ticket_access_members(guild, {"allowed_staff": ["not-a-number"]}, "support")
    assert members == []


@pytest.mark.asyncio
async def test_resolve_ticket_access_members_falls_back_without_allowed_staff(monkeypatch):
    """Buttons with no allowed_staff key fall through to the category-wide staff permissions."""
    category_member = SimpleNamespace(id=5)
    guild = SimpleNamespace(get_member=MagicMock())
    monkeypatch.setattr(main, "get_category_support_members", AsyncMock(return_value=[category_member]))

    members = await main.resolve_ticket_access_members(guild, {}, "support")

    assert members == [category_member]


# === ping_ticket_roles: allowed_staff restriction ===============================================


@pytest.mark.asyncio
async def test_ping_ticket_roles_restricted_staff_only_pings_allowed_list(monkeypatch):
    """When a ticket's button was restricted to specific staff, only they get pinged -
    the general staff role and the category's normal support members must not be pinged."""
    opener = SimpleNamespace(id=1, mention="<@1>", bot=False)
    allowed_member = SimpleNamespace(id=2, mention="<@2>", bot=False)
    other_member = SimpleNamespace(id=3, mention="<@3>", bot=False)
    staff_role = SimpleNamespace(id=99, mention="<@&99>")

    class FakeGuild:
        id = 123

        def get_member(self, uid):
            return {2: allowed_member}.get(uid)

        def get_role(self, role_id):
            return staff_role

    guild = FakeGuild()
    sent_messages = []
    sent_message = SimpleNamespace(delete=AsyncMock())

    class FakeChannel:
        def __init__(self):
            self.id = 777
            self.guild = guild
            self.members = [opener, allowed_member, other_member]

        def permissions_for(self, member):
            return SimpleNamespace(view_channel=False)

        async def send(self, content=None, allowed_mentions=None):
            sent_messages.append(content)
            return sent_message

    channel = FakeChannel()
    monkeypatch.setattr(
        main.tickets_col, "find_one", AsyncMock(return_value={"category": "support", "allowed_staff": ["2"]})
    )
    monkeypatch.setattr(main.settings_col, "find_one", AsyncMock(return_value={"staff_role": 99}))
    category_support = AsyncMock(return_value=[other_member])
    monkeypatch.setattr(main, "get_category_support_members", category_support)

    await main.ping_ticket_roles(channel, "123", opener_id=1)

    assert len(sent_messages) == 1
    content = sent_messages[0]
    assert "<@2>" in content
    assert "<@&99>" not in content
    assert "<@3>" not in content
    category_support.assert_not_awaited()


# === TicketButtonStaffSelect: validates staff role before persisting ============================


@pytest.mark.asyncio
async def test_ticket_button_staff_select_persists_only_staff_members(monkeypatch):
    """A staff member could accidentally select a non-staff user in the picker; only
    selections that actually hold the configured staff role should be persisted."""
    staff_user = SimpleNamespace(id=10, mention="<@10>")
    non_staff_user = SimpleNamespace(id=20, mention="<@20>")

    async def fake_has_staff_role(member, guild):
        return member.id == 10

    monkeypatch.setattr(main, "has_staff_role", fake_has_staff_role)
    update_calls = []

    class FakePanelsCol:
        async def update_one(self, query, update):
            update_calls.append((query, update))

    monkeypatch.setattr(main, "ticket_panels_col", FakePanelsCol())

    select = main.TicketButtonStaffSelect("123", "Panel", "Support", author_id=1)
    select._values = [staff_user, non_staff_user]
    select._view = SimpleNamespace(message=SimpleNamespace(), stop=MagicMock())

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1),
        guild=SimpleNamespace(
            id=123, get_member=MagicMock(side_effect=lambda uid: {10: staff_user, 20: non_staff_user}.get(uid))
        ),
        response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
    )

    await select.callback(interaction)

    assert len(update_calls) == 1
    _, update = update_calls[0]
    assert update["$set"]["buttons.$.allowed_staff"] == ["10"]
    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticket_button_staff_select_rejects_all_non_staff(monkeypatch):
    non_staff_user = SimpleNamespace(id=20, mention="<@20>")

    monkeypatch.setattr(main, "has_staff_role", AsyncMock(return_value=False))
    panels_col = MagicMock()
    panels_col.update_one = AsyncMock()
    monkeypatch.setattr(main, "ticket_panels_col", panels_col)

    select = main.TicketButtonStaffSelect("123", "Panel", "Support", author_id=1)
    select._values = [non_staff_user]
    select._view = SimpleNamespace(message=SimpleNamespace(), stop=MagicMock())

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1),
        guild=SimpleNamespace(id=123, get_member=MagicMock(return_value=non_staff_user)),
        response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
    )

    await select.callback(interaction)

    panels_col.update_one.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()


def _make_find_router(closed, open_):
    """Return a fake tickets_col.find() that routes to the closed-ticket cursor
    for {"guild_id": ...} queries and the open-ticket cursor for {"guild": ...}
    queries, mirroring the two schemas actually_close_ticket vs the ticket-open
    flow write into the same collection."""

    class FakeCursor:
        def __init__(self, docs):
            self.docs = docs

        async def to_list(self, length=None):
            return list(self.docs)

    def find(query):
        if "guild_id" in query:
            return FakeCursor(closed)
        return FakeCursor(open_)

    return MagicMock(side_effect=find)


@pytest.mark.asyncio
async def test_transcriptlist_sorts_newest_first(monkeypatch):
    """.transcriptlist should show the most recently closed ticket first instead
    of the oldest ones (previously returned in raw insertion order)."""
    closed = [
        {
            "ticket_id": "chan-100",
            "guild_id": "123",
            "opener_id": "1",
            "closer_id": "2",
            "created_at": "2025-08-18 10:00:00+00:00",
            "closed_at": "2025-08-18 11:00:00+00:00",
        },
        {
            "ticket_id": "chan-200",
            "guild_id": "123",
            "opener_id": "1",
            "closer_id": "2",
            "created_at": "2025-08-19 10:00:00+00:00",
            "closed_at": "2025-08-19 11:00:00+00:00",
        },
    ]
    monkeypatch.setattr(main, "tickets_col", SimpleNamespace(find=_make_find_router(closed, [])))

    async def fake_format_user(self, user_id):
        return f"<@{user_id}>"

    monkeypatch.setattr(main.TranscriptPaginationView, "format_user", fake_format_user)

    guild = SimpleNamespace(id=123)
    ctx = SimpleNamespace(guild=guild, interaction=None, send=AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(main, "is_prefix", lambda c: True)

    await main.transcriptlist.callback(ctx)

    ctx.send.assert_awaited_once()
    embed = ctx.send.await_args.kwargs["embed"]
    # the ticket closed on Aug 19 (newer) must render first, ahead of Aug 18
    assert embed.fields[0].name.endswith("#1")
    assert "chan-200" in embed.fields[0].value
    assert "chan-100" in embed.fields[1].value


@pytest.mark.asyncio
async def test_transcriptlist_includes_ongoing_tickets(monkeypatch):
    """Ongoing tickets are stored under a 'guild' field instead of 'guild_id'
    (a different schema than closed-ticket transcripts) and used to be silently
    excluded entirely since the command only ever queried by 'guild_id'."""
    closed = [
        {
            "ticket_id": "chan-100",
            "guild_id": "123",
            "opener_id": "1",
            "closer_id": "2",
            "created_at": "2025-08-18 10:00:00+00:00",
            "closed_at": "2025-08-18 11:00:00+00:00",
        }
    ]
    open_ = [
        {
            "guild": "123",
            "channel_id": "555",
            "owner_id": "1",
            "category": "support",
            "created_at": "2025-08-20 10:00:00+00:00",
        }
    ]
    monkeypatch.setattr(main, "tickets_col", SimpleNamespace(find=_make_find_router(closed, open_)))

    async def fake_format_user(self, user_id):
        return f"<@{user_id}>"

    monkeypatch.setattr(main.TranscriptPaginationView, "format_user", fake_format_user)

    guild = SimpleNamespace(id=123)
    ctx = SimpleNamespace(guild=guild, interaction=None, send=AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(main, "is_prefix", lambda c: True)

    await main.transcriptlist.callback(ctx)

    embed = ctx.send.await_args.kwargs["embed"]
    assert len(embed.fields) == 2
    # opened most recently (Aug 20, still ongoing) must render first
    assert "🟢 Ongoing" in embed.fields[0].name
    assert "<#555>" in embed.fields[0].value
    assert "🔴 Closed" in embed.fields[1].name


@pytest.mark.asyncio
async def test_build_embed_resolves_opener_instead_of_unknown(monkeypatch):
    """Regression test for the 'Opened by: Unknown' bug: once opener_id is
    actually populated (fixed by resolve_ticket_opener), the embed should show
    the resolved user instead of Unknown."""
    ticket = {
        "ticket_id": "chan-1-1755540841",
        "opener_id": "42",
        "closer_id": "99",
        "created_at": "2025-08-18 14:13:00+00:00",
        "closed_at": "2025-08-18 18:14:00+00:00",
    }
    opener_user = SimpleNamespace(id=42, mention="<@42>")
    closer_user = SimpleNamespace(id=99, mention="<@99>")

    def fake_get_user(uid):
        return {42: opener_user, 99: closer_user}.get(uid)

    ctx = SimpleNamespace(bot=SimpleNamespace(get_user=fake_get_user, fetch_user=AsyncMock()))
    view = main.TranscriptPaginationView(ctx, [ticket])
    embed = await view.build_embed()
    field_value = embed.fields[0].value
    assert "Unknown" not in field_value
    assert "<@42>" in field_value
    assert "<@99>" in field_value
