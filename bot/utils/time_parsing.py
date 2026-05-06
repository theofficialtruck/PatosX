"""Human-friendly time and amount parsers.

These functions are used wherever the user types something like ``5m`` or
``1.5 mil`` — moderation durations, giveaway lengths, economy amounts, and
so on. The intent is to be permissive (accept abbreviations *and* full
words) without ever silently producing a wrong number.
"""

from __future__ import annotations

import re
from typing import Final

_MULTIPLIERS: Final[dict[str, int]] = {
    "s": 1, "sec": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
    "mo": 2592000, "month": 2592000, "months": 2592000,
    "y": 31536000, "yr": 31536000, "year": 31536000, "years": 31536000,
}

_DURATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(s|sec|second|seconds|m|min|minute|minutes|h|hr|hour|hours|"
    r"d|day|days|w|week|weeks|mo|month|months|y|yr|year|years)\b"
)


def parse_time(duration_str: str) -> int:
    """Convert ``"1d 12h"``-style strings to seconds.

    Raises ``ValueError`` if no recognisable duration is found.
    """
    duration_str = duration_str.lower().replace(",", " ").strip()
    matches = _DURATION_PATTERN.findall(duration_str)
    if not matches:
        raise ValueError(f"Invalid duration format: {duration_str}")

    total_seconds = 0.0
    for amount_str, unit in matches:
        if unit not in _MULTIPLIERS:
            raise ValueError(f"Unknown time unit: {unit}")
        total_seconds += float(amount_str) * _MULTIPLIERS[unit]

    return int(total_seconds)


_WORDS_TO_NUM: Final[dict[str, int]] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1_000,
    "million": 1_000_000, "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}


def words_to_number(text: str) -> int | None:
    """Convert phrases like ``"two hundred thousand"`` to ``200000``.

    Returns ``None`` if any token isn't a known number word — that lets the
    caller fall back to digit parsing instead of guessing.
    """
    words = text.lower().replace("-", " ").split()
    total, current = 0, 0

    for word in words:
        if word not in _WORDS_TO_NUM:
            return None
        value = _WORDS_TO_NUM[word]

        if value == 100:
            current *= value
        elif value >= 1000:
            current *= value
            total += current
            current = 0
        else:
            current += value

    return total + current


def parse_amount(amount_str: str) -> int | None:
    """Parse user-supplied currency amounts like ``"1.5k"`` or ``"two mil"``.

    Returns the integer amount, or ``None`` if the input is unparseable.
    """
    if not amount_str:
        return None

    s = amount_str.lower().replace(",", "").strip()

    if any(word in s for word in _WORDS_TO_NUM):
        result = words_to_number(s)
        if result is not None:
            return result

    multiplier = 1
    if re.search(r"(k|thousand)$", s):
        multiplier = 1_000
        s = re.sub(r"(k|thousand)$", "", s)
    elif re.search(r"(m|mil|mm|million)$", s):
        multiplier = 1_000_000
        s = re.sub(r"(m|mil|mm|million)$", "", s)
    elif re.search(r"(b|bil|bn|billion)$", s):
        multiplier = 1_000_000_000
        s = re.sub(r"(b|bil|bn|billion)$", "", s)
    elif re.search(r"(t|tr|tril|trillion)$", s):
        multiplier = 1_000_000_000_000
        s = re.sub(r"(t|tr|tril|trillion)$", "", s)

    try:
        return int(float(s) * multiplier)
    except ValueError:
        return None


__all__ = ["parse_time", "words_to_number", "parse_amount"]
