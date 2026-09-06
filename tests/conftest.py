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

"""Shared fixtures for the test-suite.

Every command now lives on a Cog, so a command's ``.callback`` is the *unbound* method and
needs the cog instance as its first argument::

    await shop_cog.buy.callback(shop_cog, ctx, item="fishing rod 10")

Calling the Command object directly also works, because the fixtures add the cog to a real
``commands.Bot`` and discord.py then injects the cog itself::

    await moderation_cog.warn(ctx, member, reason="Breaking rules")

Each test gets a fresh ``bot`` (function scoped), and every cog fixture attaches its cog to that
same bot, so a test can request several cogs at once. No background loop starts here: cogs only
start their loops from ``on_ready``, which never fires in tests.
"""

import discord
import pytest
from discord.ext import commands

from cogs.admin import Admin
from cogs.duckgpt import DuckGPT
from cogs.economy import Economy
from cogs.fun import Fun
from cogs.games_gambling import GamesGambling
from cogs.giveaways_polls import GiveawaysPolls
from cogs.guildcfg import GuildConfig
from cogs.info import Info
from cogs.jobs_gathering import JobsGathering
from cogs.moderation import Moderation
from cogs.shop import Shop
from cogs.staff_management import StaffManagement
from cogs.stickynotes import StickyNotes
from cogs.tickets import Tickets
from cogs.vanity_invites import VanityInvites


@pytest.fixture
def bot():
    """A real (never connected) Bot, mirroring main.py's construction closely enough for cogs to
    register their commands and listeners against it."""
    return commands.Bot(
        command_prefix="?",
        intents=discord.Intents.default(),
        allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True),
        help_command=None,
    )


async def _attach(bot, cog_cls):
    cog = cog_cls(bot)
    await bot.add_cog(cog)
    return cog


@pytest.fixture
async def admin_cog(bot):
    return await _attach(bot, Admin)


@pytest.fixture
async def duckgpt_cog(bot):
    return await _attach(bot, DuckGPT)


@pytest.fixture
async def economy_cog(bot):
    return await _attach(bot, Economy)


@pytest.fixture
async def fun_cog(bot):
    return await _attach(bot, Fun)


@pytest.fixture
async def games_cog(bot):
    return await _attach(bot, GamesGambling)


@pytest.fixture
async def giveaways_cog(bot):
    return await _attach(bot, GiveawaysPolls)


@pytest.fixture
async def guildcfg_cog(bot):
    return await _attach(bot, GuildConfig)


@pytest.fixture
async def info_cog(bot):
    return await _attach(bot, Info)


@pytest.fixture
async def jobs_cog(bot):
    return await _attach(bot, JobsGathering)


@pytest.fixture
async def moderation_cog(bot):
    return await _attach(bot, Moderation)


@pytest.fixture
async def shop_cog(bot):
    return await _attach(bot, Shop)


@pytest.fixture
async def staff_cog(bot):
    return await _attach(bot, StaffManagement)


@pytest.fixture
async def stickynotes_cog(bot):
    return await _attach(bot, StickyNotes)


@pytest.fixture
async def tickets_cog(bot):
    return await _attach(bot, Tickets)


@pytest.fixture
async def vanity_cog(bot):
    return await _attach(bot, VanityInvites)
