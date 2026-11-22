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

"""One time message channels: using your one message is enforced quietly, with no public announcement
in the channel; staff see it in the log channel instead."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs import admin
from core import permsHelperFuncs as perms
from core import state


@pytest.fixture
def onetime(monkeypatch):
    """Channel 5 in guild 1 is a one time channel; the poster is not staff."""
    monkeypatch.setattr(admin, "onetime_channels", {"1": {"5": {}}})
    monkeypatch.setattr(state, "settings_col", SimpleNamespace(update_one=AsyncMock()))
    staff = AsyncMock(return_value=False)
    log = AsyncMock()
    monkeypatch.setattr(perms, "has_staff_role", staff)
    monkeypatch.setattr(perms, "log_action", log)
    guild = SimpleNamespace(id=1)
    channel = SimpleNamespace(id=5, name="introductions", mention="<#5>", set_permissions=AsyncMock(), send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=7, mention="<@7>"),
        guild=guild,
        channel=channel,
        type=discord.MessageType.default,
    )
    return SimpleNamespace(message=message, channel=channel, guild=guild, staff=staff, log=log)


@pytest.mark.asyncio
async def test_using_the_one_time_message_is_not_announced(admin_cog, onetime):
    await admin_cog.on_message(onetime.message)

    onetime.channel.set_permissions.assert_awaited_once()
    assert onetime.channel.set_permissions.await_args.kwargs["send_messages"] is False
    onetime.channel.send.assert_not_awaited()  # nothing posted in the channel
    assert "7" in admin.onetime_channels["1"]["5"]


@pytest.mark.asyncio
async def test_it_is_logged_for_staff_instead(admin_cog, onetime):
    await admin_cog.on_message(onetime.message)

    onetime.log.assert_awaited_once()
    args, kwargs = onetime.log.await_args
    assert args[0] is None  # an automated action, not a command
    assert "<@7> used their one time message in <#5>" in args[1]
    assert ".restore 7 5" in args[1]
    assert kwargs == {"user_id": 7, "action_type": "onetime_used", "guild": onetime.guild}


@pytest.mark.asyncio
async def test_staff_are_not_restricted_or_logged(admin_cog, onetime):
    onetime.staff.return_value = True
    await admin_cog.on_message(onetime.message)
    onetime.channel.set_permissions.assert_not_awaited()
    onetime.log.assert_not_awaited()


@pytest.mark.asyncio
async def test_nothing_is_logged_when_the_permission_change_fails(admin_cog, onetime):
    onetime.channel.set_permissions.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason=""), "no")
    await admin_cog.on_message(onetime.message)
    onetime.log.assert_not_awaited()
    onetime.channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_message_does_nothing_more(admin_cog, onetime):
    await admin_cog.on_message(onetime.message)
    await admin_cog.on_message(onetime.message)
    onetime.channel.set_permissions.assert_awaited_once()
    onetime.log.assert_awaited_once()
