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

"""Environment loading, validation and every static game/economy constant.

This is the root of the ``core`` import graph: it depends on nothing else in the
project, so every other module (core and cogs alike) may import it freely.
Access values through the module (``cfg.AUTHORIZED_USER_IDS``) rather than via
``from core.config import X`` so tests can monkeypatch them.
"""

import os
import sys

import discord
from dotenv import load_dotenv

# Load environment variables from the .env file into os.environ. main.py also
# calls this before importing core; load_dotenv never overrides variables that
# are already set, so the double call is harmless and keeps this module usable
# on its own (e.g. under pytest, which imports core without main.py).
load_dotenv()


# ============================================================
# Runtime helpers and test detection
# ============================================================


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable. Accepts 1, true, t, yes, y, on (case insensitive)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "t", "yes", "y", "on")


# Verbose startup logging, disabled by default, set PATOSX_DEBUG_COMMANDS=true to enable
DEBUG_COMMANDS = _env_flag("PATOSX_DEBUG_COMMANDS", default=False)


def _running_under_pytest() -> bool:
    """Return True when the process is being driven by pytest.
    Used to skip real database calls and token validation during tests."""
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def _looks_like_motor_collection(obj) -> bool:
    """Return True if obj is a real Motor MongoDB collection rather than the test stub."""
    return type(obj).__module__.startswith("motor.")


# ============================================================
# Environment variable loading and configuration
# ============================================================

# These five keys must all be present or the bot refuses to start
required_keys = ["DISCORD_TOKEN", "MONGO_URI", "GIPHY_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEYS"]
env_vars = {key: os.getenv(key) for key in required_keys}
missing = [key for key, value in env_vars.items() if not value]
if missing and (not _running_under_pytest()):
    raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
if missing and _running_under_pytest():
    # Fill empty strings so the rest of the module can import without crashing during tests
    for key in missing:
        env_vars[key] = ""
if not missing:
    print(f"All required environment variables loaded: {', '.join(required_keys)}")

# Top level credentials extracted for convenient use throughout the file
TOKEN = env_vars.get("DISCORD_TOKEN", "")
MONGO_URI = env_vars.get("MONGO_URI", "")
GIPHY_API_KEY = env_vars.get("GIPHY_API_KEY", "")
OPENROUTER_API_KEY = env_vars.get("OPENROUTER_API_KEY", "")

# Multiple Gemini keys can be provided as a comma separated list so that the bot
# can round robin between them and stay within per key rate limits
GEMINI_API_KEYS = os.getenv("GEMINI_API_KEYS", "").split(",")
GEMINI_API_KEYS = [k.strip() for k in GEMINI_API_KEYS if k.strip()]
if not missing:
    print(f"[INIT] Loaded {len(GEMINI_API_KEYS)} Gemini API key(s) for rotation")

# Comma separated Discord user IDs that bypass staff role checks and can use
# privileged commands such as addmoney and override
_AUTH_IDS_RAW = os.getenv("AUTHORIZED_USER_IDS", "")
AUTHORIZED_USER_IDS: set = {int(uid.strip()) for uid in _AUTH_IDS_RAW.split(",") if uid.strip().isdigit()}

# Display name shown in error messages, the bot lock notice, bot status, and the AI persona.
# Falls back to a generic phrase when the env var is absent or blank.
_BOT_ADMIN_NAME_RAW = os.getenv("BOT_ADMIN_NAME")
BOT_ADMIN_NAME = (_BOT_ADMIN_NAME_RAW or "").strip() or "the bot administrator"

# Names shown to users who run the beg command, defaults kept as fallback
_BEG_DONORS_RAW = os.getenv("BEG_DONORS", "thetruck,CuteBatak")
BEG_DONORS: list = [d.strip() for d in _BEG_DONORS_RAW.split(",") if d.strip()] or ["thetruck", "CuteBatak"]


# ============================================================
# Economy and game constants
# ============================================================

# Catch tables, each entry is (display name with emoji, coin value)
fishes = [("🦐 Shrimp", 100), ("🐟 Fish", 200), ("🐠 Tropical Fish", 300), ("🦑 Squid", 400), ("🐡 Pufferfish", 500)]
deep_ocean_fishes = [
    ("🐟 Anglerfish", 650),
    ("🐙 Dumbo Octopus", 900),
    ("🦑 Giant Squid", 850),
    ("🦀 King Crab", 800),
    ("🦞 Spiny Lobster", 750),
    ("🐡 Blobfish", 600),
    ("🦈 Goblin Shark", 1100),
    ("🌿 Seaweed", 550),
]
dig_rocks = [("amber shard", 240), ("moonstone fragment", 650), ("fossil core", 1000)]
bugs_to_catch = [
    ("🦋 butterfly", 180),
    ("🐞 ladybug", 140),
    ("🐛 caterpillar", 120),
    ("🪲 beetle", 210),
    ("🦟 mosquito", 90),
    ("🪰 fly", 100),
    ("🕷️ spider", 160),
    ("🦗 cricket", 150),
]

# Riddle bank for the .riddle command, grouped by difficulty.
# "answers" lists every acceptable phrasing; index 0 is shown to the user as the canonical answer.
RIDDLES = {
    "easy": [
        {"question": "What has to be broken before you can use it?", "answers": ["an egg", "egg"]},
        {"question": "What has keys but can't open locks?", "answers": ["a piano", "piano"]},
        {"question": "What gets wetter the more it dries?", "answers": ["a towel", "towel"]},
        {"question": "What has a neck but no head?", "answers": ["a bottle", "bottle"]},
        {"question": "I waddle on land, swim in a pond, and say 'quack'. What am I?", "answers": ["a duck", "duck"]},
        {"question": "What has hands but cannot clap?", "answers": ["a clock", "clock"]},
    ],
    "medium": [
        {"question": "The more you take, the more you leave behind. What am I?", "answers": ["footsteps"]},
        {"question": "What can travel around the world while staying in a corner?", "answers": ["a stamp", "stamp"]},
        {"question": "What has many teeth but cannot bite?", "answers": ["a comb", "comb"]},
        {"question": "What has one eye but cannot see?", "answers": ["a needle", "needle"]},
        {
            "question": "I'm light as a feather, yet even the strongest person can't hold me for more than a few minutes. What am I?",
            "answers": ["breath", "your breath", "breathing"],
        },
        {"question": "What kind of room has no doors or windows?", "answers": ["a mushroom", "mushroom"]},
    ],
    "hard": [
        {"question": "What can fill a room but takes up no space?", "answers": ["light"]},
        {"question": "What word becomes shorter when you add two letters to it?", "answers": ["short"]},
        {"question": "What has a heart that doesn't beat?", "answers": ["an artichoke", "artichoke"]},
        {"question": "What begins with T, ends with T, and has T in it?", "answers": ["a teapot", "teapot"]},
        {"question": "What is so fragile that saying its name breaks it?", "answers": ["silence"]},
        {
            "question": "I am taken from a mine and shut in a wooden case, never released, yet used by almost everyone. What am I?",
            "answers": ["pencil lead", "graphite", "a pencil", "pencil"],
        },
    ],
}

# Difficulty configuration for .riddle: how long the user has to answer and the coin reward range.
RIDDLE_LEVELS = {
    "easy": {"label": "Easy", "emoji": "🟢", "time_limit": 30, "reward_range": (75, 150)},
    "medium": {"label": "Medium", "emoji": "🟡", "time_limit": 25, "reward_range": (150, 300)},
    "hard": {"label": "Hard", "emoji": "🔴", "time_limit": 20, "reward_range": (300, 550)},
}

# How many uses each durable tool has before it breaks.
# Values are total uses, not hours or minutes.
TOOL_DURABILITIES = {
    "shovel": 336,
    "laptop": 28,
    "fishing rod": 112,
    "scuba gear": 112,
    "lockpick": 14,
    "pickaxe": 336,
    "rifle": 336,
    "butterfly net": 336,
}

# Quiz session settings: 10 questions, pass threshold 80 percent
NUM_Q = 10
PASS_PCT = 80.0


# Fraction each Nitro Boost use shaves off a command's cooldown, sized to its 1000 coin price
# (same tier as the Pet Duck's 30% luck buff, but for cooldowns instead of earnings).
NITRO_BOOST_COOLDOWN_REDUCTION_PCT = 0.2

# Discord system message types emitted when a member boosts the server. The guild config
# cog turns these into a thank-you message; the other on_message listeners skip them, just
# as the original single on_message handler returned early after handling a boost.
BOOST_MESSAGE_TYPES = frozenset(
    {
        discord.MessageType.premium_guild_subscription,
        discord.MessageType.premium_guild_tier_1,
        discord.MessageType.premium_guild_tier_2,
        discord.MessageType.premium_guild_tier_3,
    }
)


# ============================================================
# Command name sets used by the help system and XP gating.
# Kept here (not in the help cog) because core/xpHelperFuncs.py needs
# STAFF_HELP_COMMANDS and core/ must never import from cogs/.
# ============================================================

# Commands shown in the staff section of the help embed, also excluded from XP awards
STAFF_HELP_COMMANDS = {
    "kick",
    "ban",
    "unban",
    "mute",
    "unmute",
    "warn",
    "clearwarns",
    "purge",
    "slowmode",
    "blacklist",
    "whitelist",
    "ticketsetup",
    "ticketdeletepanel",
    "ticketlist",
    "ticketforceclose",
    "transcriptsearch",
    "transcriptlist",
    "ticketaddbutton",
    "ticketremovebutton",
    "ticketeditbutton",
    "ticketpanel",
    "transcript",
    "ticketadduser",
    "ticketremoveuser",
    "ticketsync",
    "stickynote",
    "unstickynote",
    "additem",
    "edititem",
    "delitem",
    "addmoney",
    "removemoney",
    "reseteconomy",
    "investmigrate",
    "vanityroles",
    "promoters",
    "resetpromoters",
    "roleadd",
    "roleremove",
    "configure",
    "viewconfig",
    "editconfig",
    "resetconfig",
    "setprefix",
    "invitechannel",
    "invites",
    "removeinvites",
    "resetinvites",
    "giveaway",
    "reroll",
    "draw",
    "disable",
    "enable",
    "listdisabled",
    "modview",
    "say",
    "maintenance",
    "staff",
    "unstaff",
    "viewperms",
    "debug",
    "stop",
    "testwelcome",
    "testboost",
    "reactionrole",
    "onetime",
    "restore",
    "disableonetime",
    "performance",
}
# Commands shown in the economy section of the help embed
ECONOMY_HELP_COMMANDS = {
    "balance",
    "daily",
    "beg",
    "deposit",
    "withdraw",
    "shop",
    "buy",
    "use",
    "inventory",
    "give",
    "drop",
    "leaderboard",
    "badges",
    "coinflip",
    "duckroll",
    "lottery",
    "choosejob",
    "work",
    "jobstatus",
    "fish",
    "swim",
    "attack",
    "crime",
    "passive",
    "sell",
    "invest",
    "investstatus",
    "hunt",
    "mine",
    "dig",
    "bugcatch",
    "doorgame",
    "ducktowers",
    "mines",
    "riddle",
    "monthlyrewards",
}
# Commands that should not appear in any section of the help embed
HELP_EXCLUDED_COMMANDS = {"override"}
# Categories accepted by the disable/enable commands, mapped to the Cog class names whose
# commands they cover. permsHelperFuncs.check_disabled resolves a command's category through
# ctx.command.cog_name, so a disabled category blocks every command in those cogs.
COMMAND_CATEGORIES = {
    "economy": {"Economy", "Shop", "JobsGathering", "GamesGambling"},
    "moderation": {"Moderation"},
    "duckgpt": {"DuckGPT"},
    "general": {"Fun", "Info"},
}
# Commands that can never be disabled: switching these off would lock a guild out of ever
# re-enabling anything (or out of the bot lock override).
UNDISABLEABLE_COMMANDS = {"enable", "disable", "listdisabled", "override", "help"}
# Canonical keys for the legacy mystery box item, all variants purged on startup
REMOVED_SHOP_ITEM_KEYS = {"mystery box", "mystery_box", "mysterybox"}


# ============================================================
# Monthly rewards goal definitions
# 10 goals users can work toward each calendar month (UTC). Progress is stored
# per user per guild in monthly_rewards_col and lazily reset when a new month
# begins (see core.economyHelperFuncs.get_monthly_rewards_doc). Claiming a goal
# pays out a fixed coin reward once; the goal cannot be claimed again until
# next month's reset.
# ============================================================

MONTHLY_REWARD_GOALS = [
    {
        "key": "commands_used",
        "title": "Command Enthusiast",
        "emoji": "🤖",
        "description": "Use 150 commands.",
        "target": 150,
        "reward": 750,
    },
    {
        "key": "coins_collected",
        "title": "Coin Collector",
        "emoji": "🪙",
        "description": "Earn 10,000 coins.",
        "target": 10000,
        "reward": 1000,
    },
    {
        "key": "duck_uses",
        "title": "Duck Fanatic",
        "emoji": "🦆",
        "description": "Use the `.duck` command 50 times.",
        "target": 50,
        "reward": 400,
    },
    {
        "key": "work_uses",
        "title": "Hard Worker",
        "emoji": "💼",
        "description": "Use the `.work` command 20 times.",
        "target": 20,
        "reward": 800,
    },
    {
        "key": "fish_uses",
        "title": "Master Angler",
        "emoji": "🎣",
        "description": "Go fishing 25 times.",
        "target": 25,
        "reward": 600,
    },
    {
        "key": "swim_uses",
        "title": "Deep Diver",
        "emoji": "🤿",
        "description": "Swim in the deep ocean 20 times.",
        "target": 20,
        "reward": 650,
    },
    {
        "key": "daily_uses",
        "title": "Daily Devotee",
        "emoji": "📅",
        "description": "Claim your `.daily` reward 20 times.",
        "target": 20,
        "reward": 700,
    },
    {
        "key": "beg_uses",
        "title": "Persistent Beggar",
        "emoji": "🙏",
        "description": "Use the `.beg` command 30 times.",
        "target": 30,
        "reward": 350,
    },
    {
        "key": "riddles_solved",
        "title": "Riddle Master",
        "emoji": "🧩",
        "description": "Correctly solve 10 riddles.",
        "target": 10,
        "reward": 900,
    },
    {
        "key": "quiz_passes",
        "title": "Quiz Champion",
        "emoji": "🎓",
        "description": "Pass the `.duckquiz` 5 times.",
        "target": 5,
        "reward": 850,
    },
]

# Commands whose successful completion increments a dedicated monthly counter,
# tracked centrally by the on_command_completion listener below.
MONTHLY_COMMAND_COUNTER_MAP = {
    "duck": "duck_uses",
    "work": "work_uses",
    "fish": "fish_uses",
    "swim": "swim_uses",
    "daily": "daily_uses",
    "beg": "beg_uses",
}
