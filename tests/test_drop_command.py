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

"""Tests for the drop command: no xp, always-public embed."""

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock
import main


# === decorator checks (static) ================================================================


def test_drop_command_has_no_xp_earn_decorator():
    """@xp_earn must not appear in the drop command source."""
    src = inspect.getsource(main.drop.callback)
    assert "xp_earn" not in src, "drop command must not use @xp_earn"


def test_drop_command_uses_channel_send():
    """The drop embed must be sent via ctx.channel.send, not ctx.send."""
    src = inspect.getsource(main.drop.callback)
    assert "ctx.channel.send" in src, "drop embed must use ctx.channel.send for public visibility"


def test_drop_xp_earn_not_in_decorators():
    """Confirm @xp_earn is not in the command's decorator chain."""
    for wrapper in getattr(main.drop, "__wrapped__", []):
        assert "xp_earn" not in str(wrapper)


# === prefix path: drop embed is sent publicly ==================================


@pytest.mark.asyncio
async def test_drop_prefix_sends_embed_to_channel(monkeypatch):
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.guild.id = 1111
    ctx.author.id = 2222
    ctx.author.__str__ = lambda self: "thetruck"
    ctx.author.display_avatar.url = "https://example.com/avatar.png"
    ctx.channel.send = AsyncMock(return_value=MagicMock(id=9999))
    ctx.send = AsyncMock()
    ctx.interaction = None  # prefix command
    ctx.message.delete = AsyncMock()

    monkeypatch.setattr(main, "staffperm", lambda perm: MagicMock(predicate=AsyncMock(return_value=True)))
    monkeypatch.setattr(main, "drops_col", MagicMock(find_one=AsyncMock(return_value=None)))
    monkeypatch.setattr(main, "drop_instances_col", MagicMock(update_one=AsyncMock()))

    await main.drop.callback(ctx, "100")

    ctx.channel.send.assert_awaited_once()
    ctx.send.assert_not_awaited()  # no fallback to ctx.send for main embed


@pytest.mark.asyncio
async def test_drop_slash_sends_embed_to_channel_not_interaction(monkeypatch):
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.guild.id = 3333
    ctx.author.id = 4444
    ctx.author.__str__ = lambda self: "thetruck"
    ctx.author.display_avatar.url = "https://example.com/avatar.png"
    ctx.channel.send = AsyncMock(return_value=MagicMock(id=8888))
    ctx.send = AsyncMock()
    ctx.message.delete = AsyncMock()

    # Slash context
    ctx.interaction = MagicMock()
    ctx.interaction.response.is_done.return_value = False
    ctx.interaction.response.send_message = AsyncMock()

    monkeypatch.setattr(main, "staffperm", lambda perm: MagicMock(predicate=AsyncMock(return_value=True)))
    monkeypatch.setattr(main, "drops_col", MagicMock(find_one=AsyncMock(return_value=None)))
    monkeypatch.setattr(main, "drop_instances_col", MagicMock(update_one=AsyncMock()))

    await main.drop.callback(ctx, "100")

    # Embed must go to channel (public)
    ctx.channel.send.assert_awaited_once()
    # Slash interaction acknowledged ephemerally
    ctx.interaction.response.send_message.assert_awaited_once()
    kwargs = ctx.interaction.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_drop_slash_acknowledgment_is_ephemeral(monkeypatch):
    """The slash acknowledgment must be ephemeral so it only shows to the invoker."""
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.guild.id = 5555
    ctx.author.id = 6666
    ctx.author.__str__ = lambda self: "thetruck"
    ctx.author.display_avatar.url = "https://example.com/avatar.png"
    ctx.channel.send = AsyncMock(return_value=MagicMock(id=7777))
    ctx.send = AsyncMock()
    ctx.message.delete = AsyncMock()

    ctx.interaction = MagicMock()
    ctx.interaction.response.is_done.return_value = False
    ctx.interaction.response.send_message = AsyncMock()

    monkeypatch.setattr(main, "staffperm", lambda perm: MagicMock(predicate=AsyncMock(return_value=True)))
    monkeypatch.setattr(main, "drops_col", MagicMock(find_one=AsyncMock(return_value=None)))
    monkeypatch.setattr(main, "drop_instances_col", MagicMock(update_one=AsyncMock()))

    await main.drop.callback(ctx, "500")

    ack_kwargs = ctx.interaction.response.send_message.call_args[1]
    assert ack_kwargs.get("ephemeral") is True, "Slash ack must be ephemeral"


@pytest.mark.asyncio
async def test_drop_no_xp_message_sent(monkeypatch):
    """Running drop (success) must not trigger any XP-related followup message."""
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.guild.id = 1234
    ctx.author.id = 5678
    ctx.author.__str__ = lambda self: "thetruck"
    ctx.author.display_avatar.url = "https://example.com/avatar.png"
    ctx.channel.send = AsyncMock(return_value=MagicMock(id=1000))
    ctx.send = AsyncMock()
    ctx.message.delete = AsyncMock()
    ctx.interaction = None

    monkeypatch.setattr(main, "staffperm", lambda perm: MagicMock(predicate=AsyncMock(return_value=True)))
    monkeypatch.setattr(main, "drops_col", MagicMock(find_one=AsyncMock(return_value=None)))
    monkeypatch.setattr(main, "drop_instances_col", MagicMock(update_one=AsyncMock()))

    await main.drop.callback(ctx, "200")

    # ctx.send should not have been called at all (no XP message, no error)
    ctx.send.assert_not_awaited()

# === member drop: balance deducted and refunded on failure  ========================


@pytest.mark.asyncio
async def test_drop_member_refunds_on_channel_send_failure(monkeypatch):
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.guild.id = 111
    ctx.author.id = 222
    ctx.author.__str__ = lambda self: "user"
    ctx.author.display_avatar.url = "https://example.com/avatar.png"
    ctx.channel.send = AsyncMock(side_effect=Exception("network error"))
    ctx.send = AsyncMock()
    ctx.interaction = None
    ctx.message.delete = AsyncMock()

    monkeypatch.setattr(main, "staffperm", lambda perm: MagicMock(predicate=AsyncMock(side_effect=Exception)))
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    mock_economy = MagicMock()
    mock_economy.find_one = AsyncMock(return_value={"_id": "111-222", "wallet": 500, "bank": 0})
    mock_economy.update_one = AsyncMock()
    monkeypatch.setattr(main, "economy_col", mock_economy)

    await main.drop.callback(ctx, "300")

    # Should have attempted a refund
    assert mock_economy.update_one.await_count >= 2  # deduct + refund
    # Error message sent to user
    ctx.send.assert_awaited()
    assert "❌" in ctx.send.call_args[0][0]
