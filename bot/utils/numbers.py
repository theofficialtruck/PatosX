"""Display formatters for currency-like integers.

Two complementary helpers live here:

* ``add_suffix`` produces 2-decimal pretty strings (``"1.25K"``).
* ``format_with_suffix`` produces 1-decimal compact strings (``"1.3K"``).
* ``suffix_to_int`` is the inverse and accepts user-typed suffixes (``"1k"``).

The two formatters use different precision on purpose: the economy displays
need cents-of-coin precision, while the casino games prefer compact labels
to fit inside a button or a small embed.
"""

from __future__ import annotations


def add_suffix(value: int) -> str:
    """Format ``value`` with K/M/B suffix and 2-decimal precision."""
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return str(value)


def suffix_to_int(s: str) -> int:
    """Inverse of ``add_suffix``: parse ``"1k"`` etc. to an integer."""
    s = s.upper().replace(",", "")
    if s.endswith("B"):
        return int(float(s[:-1]) * 1_000_000_000)
    if s.endswith("M"):
        return int(float(s[:-1]) * 1_000_000)
    if s.endswith("K"):
        return int(float(s[:-1]) * 1_000)
    return int(float(s))


def format_with_suffix(amount: float) -> str:
    """Compact 1-decimal formatter used by the mini-games."""
    if amount >= 1_000_000_000:
        return f"{round(amount / 1_000_000_000, 1)}B"
    if amount >= 1_000_000:
        return f"{round(amount / 1_000_000, 1)}M"
    if amount >= 1_000:
        return f"{round(amount / 1_000, 1)}K"
    return str(round(amount, 1))


__all__ = ["add_suffix", "suffix_to_int", "format_with_suffix"]
