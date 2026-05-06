"""Non-sensitive configuration: tunables, identifiers, and built-in catalogues.

This module is intentionally free of any third-party dependencies so that
tests and tooling can import it cheaply.
"""

from __future__ import annotations

from typing import Final

DEFAULT_PREFIX: Final[str] = "?"

# DuckQuiz tuning -----------------------------------------------------------
NUM_Q: Final[int] = 10
PASS_PCT: Final[float] = 80.0

# DuckGPT system prompt -----------------------------------------------------
DUCKGPT_SYSTEM_PROMPT: Final[str] = (
    "You are DuckGPT a knowledgeable talking duck created by 'thetruck'. "
    "You can answer real questions in a SHORT, clear, and funny way while "
    "staying in duck character."
    "If the user is named 'thetruck', NEVER EVER EVER EVER say 'my creator "
    "is thetruck' or repeat that fact, just talk LIKE A NORMAL HUMAN EVEN "
    "THOUGH YOU ARENT. "
    "Always keep your reply to one sentence, humorous if possible, ending "
    "with one quack sound like 'Quack!' YOU CAN DO OTHERS PLEASE PLEASE "
    "PLEASE DONT STICK TO JUST QUACK. "
    "Never add blank lines or paragraphs. Never say things like 'you told "
    "me your name' or 'you didn’t tell me your name'. "
    "If asked any kind of questions, give a short and accurate summary as "
    "a talking duck. "
    "If greeted, you can greet back naturally, but DONT YOU DARE repeat the "
    "full intro every time. "
    "Your name is DuckGPT when requested for your name MAKE SURE TO RESPOND "
    "WITH DuckGPT."
)

# Discord error fallback ----------------------------------------------------
DISCORD_SERVICE_UNAVAILABLE_MESSAGE: Final[str] = (
    "⚠️ Discord is having trouble right now. Please try again in a moment."
)

# Invite caching ------------------------------------------------------------
INVITE_CACHE_DURATION: Final[int] = 300
GLOBAL_RATE_LIMIT: Final[int] = 30

# Bot owners / debug overrides ---------------------------------------------
THETRUCK_ID: Final[int] = 1059882387590365314
CUTEBATAK_ID: Final[int] = 903123014420406302
STAFF_OVERRIDE_IDS: Final[tuple[int, ...]] = (THETRUCK_ID, CUTEBATAK_ID)

# Authorized economy admins (these IDs can run /addmoney etc.)
ECONOMY_ADMIN_IDS: Final[tuple[int, ...]] = (
    THETRUCK_ID,
    CUTEBATAK_ID,
    447235867485143057,
    723609072297050193,
)

# Commands that are considered "staff helpers" and therefore should NOT grant
# XP — keeping the list central makes it easy to audit.
STAFF_HELP_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "kick", "ban", "unban", "mute", "unmute", "warn", "clearwarns",
        "purge", "slowmode", "blacklist", "whitelist", "ticketsetup",
        "ticketdeletepanel", "ticketlist", "ticketforceclose",
        "transcriptsearch", "transcriptlist", "ticketaddbutton",
        "ticketeditbutton", "ticketpanel", "ticketclose", "transcript",
        "ticketadduser", "ticketremoveuser", "stickynote", "unstickynote",
        "additem", "edititem", "delitem", "drop", "addmoney", "removemoney",
        "vanityroles", "promoters", "resetpromoters", "roleadd", "roleremove",
        "configure", "viewconfig", "editconfig", "resetconfig", "setprefix",
        "invitechannel", "invites", "removeinvites", "giveaway", "reroll",
        "disable", "enable", "listdisabled", "stop", "testwelcome",
        "testboost", "reactionrole", "onetime", "restore", "disableonetime",
        "performance",
    }
)

# Static catalogues used across cogs ---------------------------------------
FISHES: Final[tuple[tuple[str, int], ...]] = (
    ("🦐 Shrimp", 100),
    ("🐟 Fish", 200),
    ("🐠 Tropical Fish", 300),
    ("🦑 Squid", 400),
    ("🐡 Pufferfish", 500),
)

HUNT_ANIMALS: Final[tuple[tuple[str, int], ...]] = (
    ("rabbit", 200),
    ("deer", 450),
    ("bear", 600),
)

MINE_ORES: Final[tuple[tuple[str, int], ...]] = (
    ("iron ore", 200),
    ("gold ore", 500),
    ("diamond", 1200),
)

INVESTMENT_COMPANIES: Final[dict[str, dict[str, int]]] = {
    "Techify": {"min": 500, "max": 5000},
    "MineCorp": {"min": 300, "max": 3000},
    "Oceanic": {"min": 200, "max": 2500},
}

SELL_PRICES: Final[dict[str, int]] = {
    "rabbit": 200,
    "deer": 450,
    "bear": 600,
    "fish": 150,
    "iron ore": 200,
    "gold ore": 500,
    "diamond": 1200,
}

# Initial shop catalogue (seeded on startup if collection is empty).
INITIAL_SHOP_ITEMS: Final[tuple[dict, ...]] = (
    {
        "_id": "fishing rod",
        "name": "Fishing Rod",
        "name_lower": "fishing rod",
        "price": 150,
        "description": "🎣 Needed to catch fish to earn coins.",
    },
    {
        "_id": "laptop",
        "name": "Laptop",
        "name_lower": "laptop",
        "price": 500,
        "description": "💻 Needed to work the developer job.",
    },
    {
        "_id": "pickaxe",
        "name": "Pickaxe",
        "name_lower": "pickaxe",
        "price": 500,
        "description": "⛏️ Needed to go mining.",
    },
    {
        "_id": "rifle",
        "name": "Rifle",
        "name_lower": "rifle",
        "price": 500,
        "description": "🔫 Needed to go hunting.",
    },
    {
        "_id": "shovel",
        "name": "Shovel",
        "name_lower": "shovel",
        "price": 450,
        "description": "🪏 Needed to dig for buried finds.",
    },
    {
        "_id": "bug_net",
        "name": "Bug Net",
        "name_lower": "bug net",
        "price": 400,
        "description": "🪲 Needed to catch bugs.",
    },
    {
        "_id": "pet_duck",
        "name": "Pet Duck",
        "name_lower": "pet duck",
        "price": 1000,
        "description": "🦆 Cool pet duck! Gives 30% luck for 3 uses on certain activities.",
        "uses_left": 3,
    },
    {
        "_id": "energy_drink",
        "name": "Energy Drink",
        "name_lower": "energy drink",
        "price": 200,
        "description": "⚡ Reduces work cooldown by 50% for your next work session. One-time use.",
        "uses_left": 1,
    },
    {
        "_id": "lucky_cookie",
        "name": "Lucky Cookie",
        "name_lower": "lucky cookie",
        "price": 150,
        "description": "🍪 Doubles your next work/beg earnings. One-time use.",
        "uses_left": 1,
    },
    {
        "_id": "coffee_cup",
        "name": "Coffee Cup",
        "name_lower": "coffee cup",
        "price": 100,
        "description": "☕ Gives 25% bonus on your next crime success chance. One-time use.",
        "uses_left": 1,
    },
)

# Maps a logical permission key to the commands it gates. Used by the staff
# permissions admin embed so users can see which commands they're granting.
PERMISSION_COMMAND_MAP: Final[dict[str, list[str]]] = {
    "kick": ["kick"],
    "ban": ["ban"],
    "mute": ["mute", "unmute"],
    "money_drop": ["drop"],
    "other_moderation": ["warn", "purge", "slowmode", "fine"],
    "stickynotes": ["stickynote", "unstickynote"],
    "economy": ["shop", "addmoney", "drop"],
    "vanity": ["vanityroles", "promoters"],
    "roles": ["roleadd", "claimableroles"],
    "config": ["configure", "editconfig", "viewconfig"],
    "invites": ["invitechannel", "invites", "removeinvite"],
    "toggle_commands": ["enable", "disable", "listdisabled"],
    "reactionroles": ["reactionrole"],
    "giveaways": ["giveaway", "reroll"],
    "tickets:admin": [
        "ticketsetup",
        "ticketpanel",
        "ticketaddbutton",
        "ticketeditbutton",
        "ticketdeletepanel",
        "ticketlist",
        "transcript",
        "transcriptsearch",
        "transcriptlist",
        "ticketadduser",
        "ticketremoveuser",
    ],
    "all": ["ALL COMMANDS"],
}

__all__ = [
    "DEFAULT_PREFIX",
    "NUM_Q",
    "PASS_PCT",
    "DUCKGPT_SYSTEM_PROMPT",
    "DISCORD_SERVICE_UNAVAILABLE_MESSAGE",
    "INVITE_CACHE_DURATION",
    "GLOBAL_RATE_LIMIT",
    "THETRUCK_ID",
    "CUTEBATAK_ID",
    "STAFF_OVERRIDE_IDS",
    "ECONOMY_ADMIN_IDS",
    "STAFF_HELP_COMMANDS",
    "FISHES",
    "HUNT_ANIMALS",
    "MINE_ORES",
    "INVESTMENT_COMPANIES",
    "SELL_PRICES",
    "INITIAL_SHOP_ITEMS",
    "PERMISSION_COMMAND_MAP",
]
