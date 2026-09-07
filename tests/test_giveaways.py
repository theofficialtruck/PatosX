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

"""Giveaway, poll and drop regression tests.

Covers the defects found in the original giveaway system: the winner draw looped forever when
fewer people entered than there were winners, the creation modal's prize label exceeded Discord's
45 character limit (so the form could not open), the giveaway document was never awaited into
MongoDB and used int dict keys Mongo rejects, role requirements were displayed but never enforced,
`draw` left the scheduled end running so winners were announced twice, and the slash `/poll`
path crashed on an invalid kwarg."""

import asyncio
import copy
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs import giveaways_polls as gp
from core import economyHelperFuncs as econ
from core import state

# --- fakes -------------------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

    async def to_list(self, length=None):
        return list(self._items)


class FakeCol:
    """In-memory Mongo collection: exact-match filters, $set updates, insert/find/find_one."""

    def __init__(self, docs=None):
        self.docs = {d["_id"]: copy.deepcopy(d) for d in (docs or [])}
        self.inserted = []
        self.updates = []

    @staticmethod
    def _matches(doc, query):
        return all(doc.get(k) == v for k, v in query.items())

    async def find_one(self, query):
        for doc in self.docs.values():
            if self._matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query):
        return FakeCursor(copy.deepcopy(d) for d in self.docs.values() if self._matches(d, query))

    async def insert_one(self, doc):
        key = doc.get("_id", f"auto-{len(self.docs) + 1}")  # Mongo generates _id when it is absent
        self.inserted.append(copy.deepcopy(doc))
        self.docs[key] = copy.deepcopy(doc)
        return SimpleNamespace(inserted_id=key)

    async def update_one(self, query, update, upsert=False):
        self.updates.append((copy.deepcopy(query), copy.deepcopy(update)))
        for doc in self.docs.values():
            if self._matches(doc, query):
                for key, value in update.get("$set", {}).items():
                    doc[key] = copy.deepcopy(value)
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def make_role(role_id):
    return SimpleNamespace(id=role_id, mention=f"<@&{role_id}>")


def make_member(user_id, role_ids=()):
    return SimpleNamespace(
        id=user_id,
        mention=f"<@{user_id}>",
        display_name=f"user{user_id}",
        roles=[make_role(r) for r in role_ids],
    )


def make_guild(role_ids=()):
    roles = {r: make_role(r) for r in role_ids}
    return SimpleNamespace(id=999, get_role=lambda rid: roles.get(rid), roles=list(roles.values()))


def make_message(message_id, channel, embed=None):
    return SimpleNamespace(
        id=message_id,
        channel=channel,
        embeds=[embed] if embed is not None else [],
        edit=AsyncMock(),
        delete=AsyncMock(),
    )


def make_channel(channel_id=777, guild=None):
    channel = SimpleNamespace(id=channel_id, guild=guild, send=AsyncMock())
    channel.send.return_value = make_message(555, channel)
    return channel


def make_interaction(user, guild, channel, client=None):
    return SimpleNamespace(
        id=424242,
        user=user,
        guild=guild,
        channel=channel,
        client=client or SimpleNamespace(get_user=lambda uid: None, fetch_user=AsyncMock(return_value=None)),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), is_done=MagicMock(return_value=False)),
        followup=SimpleNamespace(send=AsyncMock()),
        message=None,
    )


def giveaway_doc(gid="555", **overrides):
    doc = {
        "_id": gid,
        "guild_id": 999,
        "channel_id": 777,
        "message_id": int(gid),
        "prize": "Nitro",
        "host_id": 1,
        "end_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "winners_count": 1,
        "participants": {},
        "ended": False,
        "winners": [],
        "required_roles": [],
        "bonus_roles": {},
    }
    doc.update(overrides)
    return doc


@pytest.fixture(autouse=True)
async def _clear_registry():
    """Every test starts with no live giveaways, and any end timer a test scheduled is cancelled
    inside the test's own event loop so nothing leaks into the next test."""
    gp.ACTIVE_GIVEAWAYS.clear()
    yield
    for view in list(gp.ACTIVE_GIVEAWAYS.values()):
        if view.end_task is not None and not view.end_task.done():
            view.end_task.cancel()
    gp.ACTIVE_GIVEAWAYS.clear()
    await asyncio.sleep(0)


# --- pick_winners -------------------------------------------------------------------------------


def test_pick_winners_terminates_when_fewer_entrants_than_winners():
    """The old loop kept drawing from a pool it never shrank, so asking for more winners than
    unique entrants spun the event loop forever (and hung the whole bot)."""
    winners = gp.pick_winners({"1": 3, "2": 5}, 5)
    assert sorted(winners) == [1, 2]


def test_pick_winners_returns_distinct_winners_up_to_count():
    participants = {str(uid): 1 for uid in range(1, 11)}
    winners = gp.pick_winners(participants, 3)
    assert len(winners) == 3
    assert len(set(winners)) == 3
    assert all(1 <= w <= 10 for w in winners)


def test_pick_winners_weights_by_tickets(monkeypatch):
    seen = {}

    def fake_choices(population, weights=None, k=1):
        seen["weights"] = dict(zip(population, weights))
        return [max(population, key=lambda uid: seen["weights"][uid])]

    monkeypatch.setattr(random, "choices", fake_choices)
    assert gp.pick_winners({"1": 1, "2": 10}, 1) == [2]
    assert seen["weights"] == {1: 1, 2: 10}


def test_pick_winners_ignores_invalid_or_empty_entries():
    assert gp.pick_winners({"abc": 1, "5": 0, "6": 2, 7: "x"}, 3) == [6]
    assert gp.pick_winners({}, 3) == []
    assert gp.pick_winners(None, 3) == []


# --- parsing helpers ----------------------------------------------------------------------------


def test_parse_role_ids_accepts_ids_and_mentions_and_dedupes():
    raw = "123456789012345678, <@&234567890123456789> 123456789012345678"
    assert gp.parse_role_ids(raw) == [123456789012345678, 234567890123456789]
    assert gp.parse_role_ids("") == []
    assert gp.parse_role_ids(None) == []


def test_parse_bonus_roles_uses_string_keys_and_skips_bad_entries():
    raw = "<@&123456789012345678>|2, 234567890123456789|x, 345678901234567890|0, nonsense, 456789012345678901|3"
    assert gp.parse_bonus_roles(raw) == {"123456789012345678": 2, "456789012345678901": 3}
    assert gp.parse_bonus_roles("") == {}


# --- creation modal -----------------------------------------------------------------------------


def test_giveaway_modal_fields_fit_discord_limits():
    """Discord rejects modal text inputs with labels over 45 characters; the old prize label was a
    broken quote that ran the placeholder into the label."""
    for item in (
        gp.GiveawayModal.prize,
        gp.GiveawayModal.winners,
        gp.GiveawayModal.duration,
        gp.GiveawayModal.role_requirements,
        gp.GiveawayModal.bonus_roles,
    ):
        assert len(item.label) <= 45, item.label
    assert gp.GiveawayModal.prize.label == "Prize"
    assert gp.GiveawayModal.prize.placeholder == "What's the giveaway prize?"


def _fill_modal(modal, **values):
    for name, value in values.items():
        getattr(modal, name)._value = value


@pytest.mark.asyncio
async def test_modal_saves_giveaway_to_db_and_schedules_end(monkeypatch):
    col = FakeCol()
    monkeypatch.setattr(state, "giveaway_col", col)
    bonus_role, required_role = 111111111111111111, 222222222222222222
    guild = make_guild(role_ids=(bonus_role, required_role))
    channel = make_channel(guild=guild)
    interaction = make_interaction(make_member(1), guild, channel)
    modal = gp.GiveawayModal()
    _fill_modal(
        modal,
        prize="Nitro",
        winners="2",
        duration="1h",
        role_requirements=str(required_role),
        bonus_roles=f"<@&{bonus_role}>|2",
    )

    await modal.on_submit(interaction)

    assert len(col.inserted) == 1, "the giveaway document must actually be awaited into MongoDB"
    saved = col.inserted[0]
    assert saved["_id"] == "555"
    assert saved["winners_count"] == 2
    assert saved["required_roles"] == [required_role]
    assert saved["bonus_roles"] == {str(bonus_role): 2}, "Mongo only allows string keys in embedded documents"
    assert saved["ended"] is False
    view = gp.ACTIVE_GIVEAWAYS["555"]
    assert view.end_task is not None and not view.end_task.done()
    channel.send.return_value.edit.assert_awaited_once()
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once_with("✅ Giveaway created!", ephemeral=True)


@pytest.mark.asyncio
async def test_modal_rejects_roles_that_do_not_exist(monkeypatch):
    col = FakeCol()
    monkeypatch.setattr(state, "giveaway_col", col)
    guild = make_guild(role_ids=())
    channel = make_channel(guild=guild)
    interaction = make_interaction(make_member(1), guild, channel)
    modal = gp.GiveawayModal()
    _fill_modal(
        modal, prize="Nitro", winners="1", duration="10m", role_requirements="123456789012345678", bonus_roles=""
    )

    await modal.on_submit(interaction)

    interaction.response.send_message.assert_awaited_once()
    assert "don't exist" in interaction.response.send_message.await_args.args[0]
    assert col.inserted == []
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_modal_rejects_bad_winner_count_and_duration(monkeypatch):
    monkeypatch.setattr(state, "giveaway_col", FakeCol())
    guild = make_guild()
    channel = make_channel(guild=guild)
    for winners, duration in (("0", "1h"), ("abc", "1h"), ("1", "soon")):
        interaction = make_interaction(make_member(1), guild, channel)
        modal = gp.GiveawayModal()
        _fill_modal(modal, prize="Nitro", winners=winners, duration=duration, role_requirements="", bonus_roles="")
        await modal.on_submit(interaction)
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.args[0].startswith("❌")
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_modal_deletes_message_when_db_insert_fails(monkeypatch):
    class ExplodingCol(FakeCol):
        async def insert_one(self, doc):
            raise RuntimeError("db down")

    monkeypatch.setattr(state, "giveaway_col", ExplodingCol())
    guild = make_guild()
    channel = make_channel(guild=guild)
    interaction = make_interaction(make_member(1), guild, channel)
    modal = gp.GiveawayModal()
    _fill_modal(modal, prize="Nitro", winners="1", duration="1h", role_requirements="", bonus_roles="")

    await modal.on_submit(interaction)

    channel.send.return_value.delete.assert_awaited_once()
    assert "555" not in gp.ACTIVE_GIVEAWAYS
    assert "could not be saved" in interaction.followup.send.await_args.args[0]


# --- entry button -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_button_enforces_required_roles_and_counts_bonus_tickets(monkeypatch):
    col = FakeCol([giveaway_doc(required_roles=[10], bonus_roles={"20": 2})])
    monkeypatch.setattr(state, "giveaway_col", col)
    guild = make_guild(role_ids=(10, 20))
    channel = make_channel(guild=guild)
    embed = gp.build_giveaway_embed("Nitro", "<@1>", datetime.now(timezone.utc), 1, [10], {"20": 2})
    message = make_message(555, channel, embed)
    view = gp.GiveawayView(
        message, "555", datetime.now(timezone.utc), 1, "Nitro", required_roles=[10], bonus_roles={"20": 2}
    )

    outsider = make_interaction(make_member(2), guild, channel)
    await view.entry_button.callback(outsider)
    assert "need one of these roles" in outsider.response.send_message.await_args.args[0]
    assert view.participants == {}

    insider = make_interaction(make_member(3, role_ids=(10, 20)), guild, channel)
    await view.entry_button.callback(insider)
    assert view.participants == {3: 3}
    assert "3 ticket(s)" in insider.response.send_message.await_args.args[0]
    assert col.docs["555"]["participants"] == {"3": 3}
    participants_field = next(f for f in message.embeds[0].fields if f.name == "Participants")
    assert participants_field.value == "1"

    await view.entry_button.callback(insider)  # second click leaves
    assert view.participants == {}
    assert col.docs["555"]["participants"] == {}


@pytest.mark.asyncio
async def test_entry_button_refuses_after_end(monkeypatch):
    monkeypatch.setattr(state, "giveaway_col", FakeCol([giveaway_doc()]))
    guild = make_guild()
    channel = make_channel(guild=guild)
    view = gp.GiveawayView(make_message(555, channel), "555", datetime.now(timezone.utc), 1, "Nitro")
    view.ended = True
    interaction = make_interaction(make_member(3), guild, channel)
    await view.entry_button.callback(interaction)
    assert "already ended" in interaction.response.send_message.await_args.args[0]
    assert view.participants == {}


# --- ending ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_giveaway_persists_before_announcing_and_is_idempotent(monkeypatch):
    col = FakeCol([giveaway_doc(participants={"3": 1})])
    monkeypatch.setattr(state, "giveaway_col", col)
    guild = make_guild()
    channel = make_channel(guild=guild)
    embed = gp.build_giveaway_embed("Nitro", "<@1>", datetime.now(timezone.utc), 1, [], {})
    message = make_message(555, channel, embed)
    view = gp.GiveawayView(message, "555", datetime.now(timezone.utc), 1, "Nitro")
    view.participants[3] = 1

    winners = await view.end_giveaway()

    assert winners == [3]
    assert col.docs["555"]["ended"] is True
    assert col.docs["555"]["winners"] == [3]
    message.edit.assert_awaited_once()
    assert message.edit.await_args.kwargs["view"] is None
    fields = {f.name: f.value for f in message.embeds[0].fields}
    assert "Ended" in fields and "Ends" not in fields
    assert fields["Winners"] == "<@3>"
    channel.send.assert_awaited_once()
    assert "Congratulations <@3>" in channel.send.await_args.args[0]
    assert "555" not in gp.ACTIVE_GIVEAWAYS
    assert view.ended is True

    assert await view.end_giveaway() is None
    channel.send.assert_awaited_once()  # no second announcement


@pytest.mark.asyncio
async def test_end_giveaway_with_no_participants(monkeypatch):
    col = FakeCol([giveaway_doc()])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    view = gp.GiveawayView(make_message(555, channel), "555", datetime.now(timezone.utc), 1, "Nitro")

    assert await view.end_giveaway() == []
    assert col.docs["555"]["ended"] is True
    assert col.docs["555"]["winners"] == []
    assert "No one joined" in channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_end_giveaway_does_nothing_when_already_ended_in_db(monkeypatch):
    col = FakeCol([giveaway_doc(ended=True, participants={"3": 1})])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    view = gp.GiveawayView(make_message(555, channel), "555", datetime.now(timezone.utc), 1, "Nitro")
    view.participants[3] = 1

    assert await view.end_giveaway() is None
    channel.send.assert_not_awaited()
    assert col.updates == []
    assert view.ended is True
    assert "555" not in gp.ACTIVE_GIVEAWAYS


@pytest.mark.asyncio
async def test_end_giveaway_survives_deleted_message(monkeypatch):
    col = FakeCol([giveaway_doc(participants={"3": 1})])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    message = make_message(
        555, channel, gp.build_giveaway_embed("Nitro", "<@1>", datetime.now(timezone.utc), 1, [], {})
    )
    message.edit = AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), None))
    view = gp.GiveawayView(message, "555", datetime.now(timezone.utc), 1, "Nitro")
    view.participants[3] = 1

    assert await view.end_giveaway() == [3]
    assert col.docs["555"]["ended"] is True
    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_end_after_delay_finishes_giveaway(monkeypatch):
    col = FakeCol([giveaway_doc(participants={"3": 1})])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    view = gp.GiveawayView(make_message(555, channel), "555", datetime.now(timezone.utc), 1, "Nitro")
    view.participants[3] = 1
    task = view.schedule_end(0)
    await asyncio.wait_for(task, timeout=2)
    assert col.docs["555"]["ended"] is True
    assert view.end_task is task and task.done() and not task.cancelled()


# --- draw / reroll commands ---------------------------------------------------------------------


def make_ctx(bot):
    return SimpleNamespace(
        guild=SimpleNamespace(id=999), author=make_member(1), send=AsyncMock(), bot=bot, interaction=None
    )


@pytest.mark.asyncio
async def test_draw_ends_live_giveaway_through_view_and_cancels_timer(monkeypatch, giveaways_cog, bot):
    col = FakeCol([giveaway_doc(participants={"3": 1, "4": 1}, winners_count=1)])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    view = gp.GiveawayView(make_message(555, channel), "555", datetime.now(timezone.utc), 1, "Nitro")
    view.participants.update({3: 1, 4: 1})
    timer = view.schedule_end(3600)
    ctx = make_ctx(bot)

    await giveaways_cog.draw.callback(giveaways_cog, ctx, 555)
    await asyncio.sleep(0.05)  # let the cancellation propagate

    assert col.docs["555"]["ended"] is True
    assert len(col.docs["555"]["winners"]) == 1
    assert timer.cancelled()
    channel.send.assert_awaited_once()
    assert "Congratulations" in channel.send.await_args.args[0]
    assert "Winners drawn" in ctx.send.await_args.args[0]
    assert "555" not in gp.ACTIVE_GIVEAWAYS


@pytest.mark.asyncio
async def test_draw_refuses_ended_giveaway_and_empty_giveaway(monkeypatch, giveaways_cog, bot):
    col = FakeCol([giveaway_doc(gid="555", ended=True), giveaway_doc(gid="556", message_id=556)])
    monkeypatch.setattr(state, "giveaway_col", col)
    ctx = make_ctx(bot)

    await giveaways_cog.draw.callback(giveaways_cog, ctx, 555)
    assert "already ended" in ctx.send.await_args.args[0]

    await giveaways_cog.draw.callback(giveaways_cog, ctx, 556)
    assert "stays open" in ctx.send.await_args.args[0]
    assert col.docs["556"]["ended"] is False


@pytest.mark.asyncio
async def test_draw_falls_back_to_stored_data_when_not_live(monkeypatch, giveaways_cog, bot):
    col = FakeCol([giveaway_doc(participants={"3": 2})])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    message = make_message(
        555, channel, gp.build_giveaway_embed("Nitro", "<@1>", datetime.now(timezone.utc), 1, [], {})
    )
    channel.fetch_message = AsyncMock(return_value=message)
    monkeypatch.setattr(bot, "get_channel", lambda cid: channel)
    ctx = make_ctx(bot)

    await giveaways_cog.draw.callback(giveaways_cog, ctx, 555)

    assert col.docs["555"]["ended"] is True
    assert col.docs["555"]["winners"] == [3]
    message.edit.assert_awaited_once()
    assert message.edit.await_args.kwargs["view"] is None
    assert "Congratulations <@3>" in channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_reroll_requires_ended_giveaway_and_updates_winners(monkeypatch, giveaways_cog, bot):
    col = FakeCol(
        [
            giveaway_doc(gid="555", participants={"3": 1}),
            giveaway_doc(gid="556", message_id=556, ended=True, participants={"7": 1}, winners=[3]),
        ]
    )
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    message = make_message(
        556, channel, gp.build_giveaway_embed("Nitro", "<@1>", datetime.now(timezone.utc), 1, [], {})
    )
    channel.fetch_message = AsyncMock(return_value=message)
    monkeypatch.setattr(bot, "get_channel", lambda cid: channel)
    ctx = make_ctx(bot)

    await giveaways_cog.reroll.callback(giveaways_cog, ctx, 555)
    assert "hasn't ended yet" in ctx.send.await_args.args[0]

    await giveaways_cog.reroll.callback(giveaways_cog, ctx, 556)
    assert col.docs["556"]["winners"] == [7]
    assert "Reroll!" in channel.send.await_args.args[0]
    assert "Reroll complete" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_giveaway_command_requires_slash(giveaways_cog, bot):
    ctx = make_ctx(bot)
    await giveaways_cog.giveaway.callback(giveaways_cog, ctx)
    assert "/giveaway" in ctx.send.await_args.args[0]

    ctx.interaction = SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock()))
    await giveaways_cog.giveaway.callback(giveaways_cog, ctx)
    ctx.interaction.response.send_modal.assert_awaited_once()
    assert isinstance(ctx.interaction.response.send_modal.await_args.args[0], gp.GiveawayModal)


# --- resume -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_marks_giveaway_with_deleted_message_as_ended(monkeypatch, bot):
    col = FakeCol([giveaway_doc()])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), None)
    )
    monkeypatch.setattr(bot, "get_channel", lambda cid: channel)

    await gp.resume_giveaways(bot)

    assert col.docs["555"]["ended"] is True
    assert col.docs["555"]["ended_reason"] == "message deleted"
    assert gp.ACTIVE_GIVEAWAYS == {}


@pytest.mark.asyncio
async def test_resume_reattaches_view_and_schedules_end(monkeypatch, bot):
    col = FakeCol([giveaway_doc(participants={"3": 2}, bonus_roles={"20": 2}, required_roles=[10])])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    message = make_message(555, channel)
    channel.fetch_message = AsyncMock(return_value=message)
    monkeypatch.setattr(bot, "get_channel", lambda cid: channel)

    await gp.resume_giveaways(bot)

    view = gp.ACTIVE_GIVEAWAYS["555"]
    assert view.participants == {3: 2}
    assert view.bonus_roles == {20: 2}
    assert view.required_roles == [10]
    assert view.end_task is not None and not view.end_task.done()
    message.edit.assert_awaited_once_with(view=view)
    assert col.docs["555"]["ended"] is False

    # a second resume pass must not attach a second view to the same giveaway
    await gp.resume_giveaways(bot)
    assert gp.ACTIVE_GIVEAWAYS["555"] is view
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_ends_giveaway_that_expired_offline(monkeypatch, bot):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    col = FakeCol([giveaway_doc(participants={"3": 1}, end_time=past)])
    monkeypatch.setattr(state, "giveaway_col", col)
    channel = make_channel(guild=make_guild())
    message = make_message(
        555, channel, gp.build_giveaway_embed("Nitro", "<@1>", datetime.now(timezone.utc), 1, [], {})
    )
    channel.fetch_message = AsyncMock(return_value=message)
    monkeypatch.setattr(bot, "get_channel", lambda cid: channel)

    await gp.resume_giveaways(bot)

    assert col.docs["555"]["ended"] is True
    assert col.docs["555"]["winners"] == [3]
    assert "555" not in gp.ACTIVE_GIVEAWAYS


# --- polls ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_slash_invocation_opens_the_modal(giveaways_cog, bot):
    ctx = make_ctx(bot)
    ctx.interaction = SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock()))
    await giveaways_cog.poll.callback(giveaways_cog, ctx)
    ctx.interaction.response.send_modal.assert_awaited_once()
    assert isinstance(ctx.interaction.response.send_modal.await_args.args[0], gp.PollModal)


@pytest.mark.asyncio
async def test_poll_modal_creates_poll(monkeypatch):
    """The slash path used to pass an unsupported `timeout=` kwarg to PollView and always failed."""
    col = FakeCol()
    monkeypatch.setattr(state, "polls_col", col)
    guild = make_guild()
    post_channel = make_channel(channel_id=321, guild=guild)
    interaction = make_interaction(
        make_member(1),
        guild,
        post_channel,
        client=SimpleNamespace(get_channel=lambda cid: post_channel if cid == 321 else None),
    )
    modal = gp.PollModal()
    _fill_modal(
        modal,
        question="Best duck?",
        option1="Mallard",
        option2="Pekin",
        option3="",
        option4="",
        option5="",
        channel="321",
        duration="10m",
    )

    await modal.on_submit(interaction)

    post_channel.send.assert_awaited_once()
    assert isinstance(post_channel.send.await_args.kwargs["view"], gp.PollView)
    assert len(col.inserted) == 1
    assert col.inserted[0]["question"] == "Best duck?"
    interaction.followup.send.assert_awaited_once_with("✅ Poll created!", ephemeral=True)


@pytest.mark.asyncio
async def test_poll_modal_rejects_unknown_channel(monkeypatch):
    col = FakeCol()
    monkeypatch.setattr(state, "polls_col", col)
    guild = make_guild()
    interaction = make_interaction(
        make_member(1), guild, make_channel(guild=guild), client=SimpleNamespace(get_channel=lambda cid: None)
    )
    modal = gp.PollModal()
    _fill_modal(
        modal, question="Q", option1="a", option2="b", option3="", option4="", option5="", channel="1", duration="10m"
    )

    await modal.on_submit(interaction)

    assert "doesn't exist" in interaction.response.send_message.await_args.args[0]
    assert col.inserted == []


# --- drops ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drop_claim_is_atomic(monkeypatch):
    """Two people clicking Claim at the same instant must not both be paid: only the click whose
    find_one_and_update flips claimed False -> True wins."""
    doc = {"_id": "d1", "message_id": "900", "author_id": "1", "amount": 500, "claimed": False}
    drops = SimpleNamespace(
        find_one=AsyncMock(return_value=dict(doc)), find_one_and_update=AsyncMock(return_value=None)
    )
    monkeypatch.setattr(state, "drop_instances_col", drops)
    add_balance = AsyncMock()
    monkeypatch.setattr(econ, "add_balance", add_balance)
    view = gp.DropClaimView()
    interaction = SimpleNamespace(
        message=SimpleNamespace(id=900, embeds=[]),
        user=make_member(2),
        guild=SimpleNamespace(id=999),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await view.claim.callback(interaction)

    add_balance.assert_not_awaited()
    assert "already been claimed" in interaction.response.send_message.await_args.args[0]

    drops.find_one_and_update = AsyncMock(return_value=dict(doc))
    await view.claim.callback(interaction)
    add_balance.assert_awaited_once_with(2, 999, 500)
    interaction.response.edit_message.assert_awaited_once()
