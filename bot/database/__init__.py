"""Single source of truth for the MongoDB client and collection handles.

Every collection in the bot is created here so other modules can import
them by name and there is exactly one ``AsyncIOMotorClient`` instance.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from bot.config.secrets import MONGO_URI

mongo: AsyncIOMotorClient = AsyncIOMotorClient(MONGO_URI)
db = mongo["discord_bot"]


def _col(name: str) -> AsyncIOMotorCollection:
    return db[name]


# --- Server / settings ----------------------------------------------------
settings_col = _col("guild_settings")
config_col = _col("configuration")
guild_config_col = _col("guild_config")
disabled_col = _col("disabled")
staffperms_col = _col("staffperms")

# --- Logging --------------------------------------------------------------
logs_col = _col("logs")

# --- Economy --------------------------------------------------------------
economy_col = _col("economy")
shop_col = _col("shop")
guild_shop_col = _col("guild_shop")
investments_col = _col("investments")
fines_col = _col("fines")
xp_col = _col("xp")
minigameplayerdata_col = _col("minigameplayerdata")
drops_col = _col("drops")
drop_instances_col = _col("drop_instances")
quiz_col = _col("quiz")

# --- Moderation -----------------------------------------------------------
mod_col = _col("moderation")
mutes_col = _col("mutes")
blacklist_col = _col("blacklist")

# --- Server features ------------------------------------------------------
afk_col = _col("afk")
vanity_col = _col("vanityroles")
sticky_col = _col("stickynotes")
reaction_col = _col("reactionroles")
welcome_col = _col("welcome")
boost_col = _col("boost")
roles_col = _col("roles")
reminders_col = _col("reminders")
polls_col = _col("polls")
giveaway_col = _col("giveaway_col")

# --- Tickets --------------------------------------------------------------
tickets_col = _col("tickets")
ticket_panels_col = _col("ticket_panels")
tickets_counter_col = _col("tickets_counter")

# --- Invites --------------------------------------------------------------
invites_col = _col("invites")
invite_config_col = _col("invite_config")

# --- DuckGPT --------------------------------------------------------------
duck_conversations_col = _col("duck_conversations")

__all__ = [
    "mongo",
    "db",
    # Settings
    "settings_col",
    "config_col",
    "guild_config_col",
    "disabled_col",
    "staffperms_col",
    # Logging
    "logs_col",
    # Economy
    "economy_col",
    "shop_col",
    "guild_shop_col",
    "investments_col",
    "fines_col",
    "xp_col",
    "minigameplayerdata_col",
    "drops_col",
    "drop_instances_col",
    "quiz_col",
    # Moderation
    "mod_col",
    "mutes_col",
    "blacklist_col",
    # Features
    "afk_col",
    "vanity_col",
    "sticky_col",
    "reaction_col",
    "welcome_col",
    "boost_col",
    "roles_col",
    "reminders_col",
    "polls_col",
    "giveaway_col",
    # Tickets
    "tickets_col",
    "ticket_panels_col",
    "tickets_counter_col",
    # Invites
    "invites_col",
    "invite_config_col",
    # DuckGPT
    "duck_conversations_col",
]
