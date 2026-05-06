"""Configuration package: secrets, environment values, and tunable constants.

The package separates *what is sensitive* (``secrets.py``) from *what is just
a tunable* (``constants.py``) so that a developer can review or change one
without touching the other.
"""

from bot.config import constants
from bot.config.secrets import (
    DISCORD_TOKEN,
    GEMINI_API_KEYS,
    MONGO_URI,
    OPENROUTER_API_KEY,
    QUOTE_API_KEY,
    TENOR_API_KEY,
)

__all__ = [
    "DISCORD_TOKEN",
    "GEMINI_API_KEYS",
    "MONGO_URI",
    "OPENROUTER_API_KEY",
    "QUOTE_API_KEY",
    "TENOR_API_KEY",
    "constants",
]
