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

"""Single source of truth for all mutable shared state.

Everything here is looked up *through the module* at call time
(``state.xp_col.find_one(...)``, ``state.session``), never via
``from core.state import xp_col``:

* ``session`` starts as ``None`` and is only assigned once the bot connects;
  a bare import would capture ``None`` forever.
* The test-suite swaps collections by reassigning attributes on this module
  (``monkeypatch.setattr(state, "xp_col", fake)``), which only works if callers
  resolve the attribute at call time.

No cog creates its own database connection or aiohttp session.
"""

import asyncio
import types
from concurrent.futures import ThreadPoolExecutor

import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient

from core import config as cfg

# ============================================================
# Test stubs for MongoDB, used when running under pytest
# so no live database connection is required
# ============================================================


class _DummyAsyncCursor:
    """Async iterator that immediately signals end of results, standing in for a real Motor cursor."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        return []


class _DummyAsyncCollection:
    """No op MongoDB collection used during test runs.
    Every write returns a success shaped namespace, every read returns None or zero."""

    def __init__(self, name: str = ""):
        self._name = name

    def find(self, *args, **kwargs):
        return _DummyAsyncCursor()

    async def find_one(self, *args, **kwargs):
        return None

    async def insert_one(self, *args, **kwargs):
        return types.SimpleNamespace(inserted_id=None)

    async def update_one(self, *args, **kwargs):
        return types.SimpleNamespace(modified_count=0)

    async def delete_one(self, *args, **kwargs):
        return types.SimpleNamespace(deleted_count=0)

    async def count_documents(self, *args, **kwargs):
        return 0

    async def create_index(self, *args, **kwargs):
        return None

    async def find_one_and_update(self, *args, **kwargs):
        return None

    async def replace_one(self, *args, **kwargs):
        return types.SimpleNamespace(modified_count=0, upserted_id=None)

    async def update_many(self, *args, **kwargs):
        return types.SimpleNamespace(modified_count=0)

    async def delete_many(self, *args, **kwargs):
        return types.SimpleNamespace(deleted_count=0)

    async def insert_many(self, *args, **kwargs):
        return types.SimpleNamespace(inserted_ids=[])

    async def distinct(self, *args, **kwargs):
        return []

    def aggregate(self, *args, **kwargs):
        return _DummyAsyncCursor()


class _DummyDB:
    """Minimal database stand in that returns a _DummyAsyncCollection for every attribute access."""

    def __getitem__(self, name: str):
        return _DummyAsyncCollection(name)


# ============================================================
# MongoDB client, database and collection references
# ============================================================

# Real Motor client when credentials are present, otherwise the test stub
mongo = AsyncIOMotorClient(cfg.MONGO_URI) if cfg.MONGO_URI else None
# Use the real Motor client when credentials are present, otherwise use the test stub
db = mongo["discord_bot"] if mongo is not None else _DummyDB()

# Each collection maps to a specific domain of bot data
settings_col = db["guild_settings"]  # per guild prefix, staff role, channel config
config_col = db["configuration"]  # extended feature configuration per guild
logs_col = db["logs"]  # moderation action audit log
economy_col = db["economy"]  # wallet, bank, inventory, job data per user
mod_col = db["moderation"]  # warnings, bans, mute records
afk_col = db["afk"]  # AFK status and messages
vanity_col = db["vanityroles"]  # vanity role assignments
sticky_col = db["stickynotes"]  # sticky message channel config
reaction_col = db["reactionroles"]  # reaction to role mappings
shop_col = db["shop"]  # global default shop items
fines_col = db["fines"]  # outstanding fines per user
welcome_col = db["welcome"]  # welcome message templates per guild
boost_col = db["boost"]  # boost reward config
guild_shop_col = db["guild_shop"]  # per guild custom shop items
quiz_col = db["quiz"]  # active quiz session state
disabled_col = db["disabled"]  # disabled commands and categories per guild
tickets_col = db["tickets"]  # open and closed ticket records
ticket_panels_col = db["ticket_panels"]  # ticket panel button configuration
tickets_counter_col = db["tickets_counter"]  # incrementing ticket number per guild
giveaway_col = db["giveaway_col"]  # active giveaway records
guild_config_col = db["guild_config"]  # miscellaneous guild level toggles
invites_col = db["invites"]  # invite link usage tracking
invite_config_col = db["invite_config"]  # per guild invite tracking settings
blacklist_col = db["blacklist"]  # users blocked from bot commands
reminders_col = db["reminders"]  # scheduled user reminders
polls_col = db["polls"]  # active poll records
investments_col = db["investments"]  # user investment positions
drops_col = db["drops"]  # active money drop definitions
drop_instances_col = db["drop_instances"]  # in flight drop claim state
roles_col = db["roles"]  # claimable role listings
mutes_col = db["mutes"]  # active timed mute records
duck_conversations_col = db["duck_conversations"]  # DuckGPT per user conversation history
staffperms_col = db["staffperms"]  # granular staff permission grants per user
minigameplayerdata_col = db["minigameplayerdata"]  # persistent minigame state
xp_col = db["xp"]  # experience point totals per user per guild
badges_col = db["badges"]  # earned badge IDs and activity counters
monthly_rewards_col = db["monthly_rewards"]  # monthly reward goal progress and claims per user


# ============================================================
# Shared runtime objects
# ============================================================

# Shared aiohttp session. Starts as None and is created by main.py's on_ready once the bot has
# connected (or lazily by http_session() on first use); closed again during shutdown. Cogs never
# create their own ClientSession - they call http_session() and never close what it returns.
session: aiohttp.ClientSession | None = None


def http_session() -> aiohttp.ClientSession:
    """Return the shared aiohttp session, creating it if the bot has not opened one yet.

    The lazy path covers the window before on_ready and test runs, where main.py never gets to
    create the session. Callers use ``async with state.http_session().get(url) as resp`` - never
    ``async with state.http_session()``, which would close the shared session for everyone."""
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return session


# Thread pool for blocking Gemini SDK calls so they do not block the asyncio event loop
executor = ThreadPoolExecutor()

# Per guild lock flags set by the stop command, cleared by override
bot_locks = {}
# Rolling buffer of the last five unhandled runtime errors, surfaced by the debug command
recent_errors = []

# In memory invite cache: guild_id -> (timestamp, list of Invite objects).
# A dedicated async queue serializes API calls to avoid hitting Discord rate limits.
invite_cache = {}
last_invite_fetch = {}
INVITE_CACHE_DURATION = 300  # seconds before a cached invite list is considered stale
GLOBAL_RATE_LIMIT = 30  # minimum seconds between global invite API calls
last_global_invite_fetch = 0
invite_queue = asyncio.Queue()
