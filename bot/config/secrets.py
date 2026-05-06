"""Loads sensitive credentials from the environment.

Keeping secrets isolated in their own module makes it obvious which values
need to be present in ``.env`` and which are merely runtime tunables. The
module intentionally fails fast at import time if a required key is missing
so the bot does not start in an unusable state.
"""

from __future__ import annotations

import os
from typing import Final, List

from dotenv import load_dotenv

load_dotenv()

_REQUIRED_KEYS: Final[tuple[str, ...]] = (
    "DISCORD_TOKEN",
    "MONGO_URI",
    "TENOR_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEYS",
)

_env_values = {key: os.getenv(key) for key in _REQUIRED_KEYS}
_missing = [key for key, value in _env_values.items() if not value]
if _missing:
    raise ValueError(
        f"❌ Missing required environment variables: {', '.join(_missing)}"
    )

print(f"All required environment variables loaded: {', '.join(_REQUIRED_KEYS)}")

DISCORD_TOKEN: Final[str] = _env_values["DISCORD_TOKEN"]  # type: ignore[assignment]
MONGO_URI: Final[str] = _env_values["MONGO_URI"]  # type: ignore[assignment]
TENOR_API_KEY: Final[str] = _env_values["TENOR_API_KEY"]  # type: ignore[assignment]
OPENROUTER_API_KEY: Final[str] = _env_values["OPENROUTER_API_KEY"]  # type: ignore[assignment]
QUOTE_API_KEY: Final[str | None] = os.getenv("QUOTE_API_KEY")


def _split_keys(raw: str | None) -> List[str]:
    if not raw:
        return []
    return [key.strip() for key in raw.split(",") if key.strip()]


GEMINI_API_KEYS: Final[List[str]] = _split_keys(_env_values["GEMINI_API_KEYS"])

__all__ = [
    "DISCORD_TOKEN",
    "MONGO_URI",
    "TENOR_API_KEY",
    "OPENROUTER_API_KEY",
    "QUOTE_API_KEY",
    "GEMINI_API_KEYS",
]
