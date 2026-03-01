# test_bot_commands.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
import main
import asyncio

@pytest.mark.asyncio
async def test_warn_command():
    ctx = MagicMock()
    member = MagicMock()
    ctx.guild.id = 123456789
    ctx.author = MagicMock(name="Moderator", id=111)
    member.id = 222
    member.mention = "@TestUser"
    member.send = AsyncMock()
    main.warnings_data.clear()

    await main.warn(ctx, member, reason="Breaking rules")

    assert str(ctx.guild.id) in main.warnings_data
    assert str(member.id) in main.warnings_data[str(ctx.guild.id)]
    assert main.warnings_data[str(ctx.guild.id)][str(member.id)][0]["reason"] == "Breaking rules"

@pytest.mark.asyncio
async def test_kick_command():
    ctx = MagicMock()
    member = AsyncMock()
    ctx.guild.id = 987654321
    ctx.author = MagicMock(name="Moderator", id=111)
    member.id = 222
    member.mention = "@UserToKick"
    member.kick = AsyncMock()
    main.actions_data.clear()

    await main.kick(ctx, member, reason="Violation")
    member.kick.assert_awaited_with(reason="Violation")
    assert str(ctx.guild.id) in main.actions_data
    assert str(member.id) in main.actions_data[str(ctx.guild.id)]
    assert main.actions_data[str(ctx.guild.id)][str(member.id)][-1]["type"] == "kick"

@pytest.mark.asyncio
async def test_mute_command_with_duration():
    ctx = MagicMock()
    member = MagicMock()
    mute_role = MagicMock()
    ctx.guild.id = 123
    ctx.guild.roles = [mute_role]
    mute_role.name = "Muted"
    ctx.guild.channels = []
    mute_role in member.roles
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    ctx.author = MagicMock(name="Mod", id=999)
    member.id = 888
    member.mention = "@User"
    main.actions_data.clear()

    async def fake_sleep(x): pass
    asyncio.sleep = fake_sleep

    await main.mute(ctx, member, duration="10s", reason="Spamming")
    member.add_roles.assert_awaited()
    member.remove_roles.assert_awaited()
    assert str(ctx.guild.id) in main.actions_data
    assert str(member.id) in main.actions_data[str(ctx.guild.id)]
    assert main.actions_data[str(ctx.guild.id)][str(member.id)][-1]["type"] == "unmute"

if __name__ == '__main__':
    pytest.main([__file__])
