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

from cogs import stickynotes
from core import state


class FakeStickyCol:
    def __init__(self, docs):
        self.docs = docs
        self.update_one = AsyncMock()

    def find(self, query):
        docs = self.docs

        class _Cursor:
            def __aiter__(self):
                async def gen():
                    for doc in docs:
                        yield doc

                return gen()

        return _Cursor()


def make_channel(channel_id, new_message_id, *, send_error=None):
    channel = MagicMock()
    channel.id = channel_id
    channel.guild.id = 1
    channel.send = AsyncMock(return_value=SimpleNamespace(id=new_message_id), side_effect=send_error)
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.partial = partial
    return channel


def make_bot(channels):
    guild = SimpleNamespace(get_channel=lambda cid: channels.get(cid))
    return SimpleNamespace(get_guild=lambda gid: guild if gid == 1 else None)


def test_sticky_repost_interval_is_about_five_minutes():
    assert stickynotes.STICKY_REPOST_MINUTES == 5


def test_cog_has_no_on_message_listener(stickynotes_cog):
    """Reposting is timer driven only; nothing reacts to channel messages any more."""
    assert not hasattr(stickynotes_cog, "on_message")
    assert not hasattr(stickynotes, "last_sticky_msg")


@pytest.mark.asyncio
async def test_repost_posts_new_saves_id_and_deletes_old(monkeypatch):
    col = FakeStickyCol([])
    monkeypatch.setattr(state, "sticky_col", col)
    channel = make_channel(10, 222)
    await stickynotes.repost_sticky_note(channel, {"_id": "a", "text": "rules", "message": 111})
    channel.send.assert_awaited_once_with("rules")
    col.update_one.assert_awaited_once_with({"_id": "a"}, {"$set": {"guild": "1", "channel": "10", "message": 222}})
    channel.get_partial_message.assert_called_once_with(111)
    channel.partial.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_repost_keeps_going_when_old_message_is_already_gone_or_undeletable(monkeypatch):
    col = FakeStickyCol([])
    monkeypatch.setattr(state, "sticky_col", col)
    channel = make_channel(10, 222)
    channel.partial.delete.side_effect = discord.NotFound(MagicMock(status=404), "gone")
    await stickynotes.repost_sticky_note(channel, {"_id": "a", "text": "rules", "message": 111})
    channel.partial.delete.side_effect = discord.Forbidden(MagicMock(status=403), "nope")
    await stickynotes.repost_sticky_note(channel, {"_id": "a", "text": "rules", "message": 222})
    assert col.update_one.await_count == 2
    col.update_one.assert_awaited_with({"_id": "a"}, {"$set": {"guild": "1", "channel": "10", "message": 222}})


@pytest.mark.asyncio
async def test_repost_without_stored_message_only_posts(monkeypatch):
    monkeypatch.setattr(state, "sticky_col", FakeStickyCol([]))
    channel = make_channel(10, 222)
    await stickynotes.repost_sticky_note(channel, {"_id": "a", "text": "rules"})
    channel.send.assert_awaited_once_with("rules")
    channel.get_partial_message.assert_not_called()


@pytest.mark.asyncio
async def test_loop_reposts_every_sticky_and_one_failure_does_not_stop_the_rest(monkeypatch, stickynotes_cog):
    broken = make_channel(10, 0, send_error=discord.Forbidden(MagicMock(status=403), "no perms"))
    fine = make_channel(20, 333)
    docs = [
        {"_id": "a", "guild": "1", "channel": "10", "text": "one", "message": 1},
        {"_id": "gone", "guild": "1", "channel": "999", "text": "deleted channel", "message": 2},
        {"_id": "other", "guild": "77", "channel": "20", "text": "other guild", "message": 3},
        {"_id": "b", "guild": "1", "channel": "20", "text": "two", "message": 4},
    ]
    col = FakeStickyCol(docs)
    monkeypatch.setattr(state, "sticky_col", col)
    stickynotes_cog.bot = make_bot({10: broken, 20: fine})

    await stickynotes_cog.repost_stickies.coro(stickynotes_cog)

    broken.send.assert_awaited_once_with("one")
    fine.send.assert_awaited_once_with("two")
    fine.get_partial_message.assert_called_once_with(4)


@pytest.mark.asyncio
async def test_loop_reposts_even_when_nobody_has_talked_in_the_channel(monkeypatch, stickynotes_cog):
    """Every run re-sends the note; there is no 'is it still the last message' detection to go stale."""
    fine = make_channel(20, 333)
    monkeypatch.setattr(
        state, "sticky_col", FakeStickyCol([{"_id": "b", "guild": "1", "channel": "20", "text": "two", "message": 4}])
    )
    stickynotes_cog.bot = make_bot({20: fine})
    await stickynotes_cog.repost_stickies.coro(stickynotes_cog)
    await stickynotes_cog.repost_stickies.coro(stickynotes_cog)
    assert fine.send.await_count == 2


@pytest.mark.asyncio
async def test_loop_survives_database_errors(monkeypatch, stickynotes_cog):
    def boom(query):
        raise RuntimeError("db down")

    monkeypatch.setattr(state, "sticky_col", SimpleNamespace(find=boom))
    await stickynotes_cog.repost_stickies.coro(stickynotes_cog)  # must not raise
