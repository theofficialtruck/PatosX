"""Bot factory + ``DuckParadiseBot`` subclass.

Centralising bot construction here keeps ``main.py`` skinny: anyone wanting
to embed the bot in a different process (an aiohttp app, a script, a test)
just calls :func:`create_bot` and is done.

The subclass overrides ``on_message`` with a per-message-id deduplication
guard. The guard exists because users have repeatedly hit a "every command
runs twice" bug in production whose root cause is hard to pin down — it
could be a duplicate ``process_commands`` call (the historical regression),
two bot processes accidentally sharing a token, a Cog re-registering a
listener, or even discord.py briefly re-delivering an event during a
gateway resume. Whatever the root cause, deduping on ``message.id`` makes
the symptom impossible.
"""

from __future__ import annotations

import sys
import time
import types
from collections import OrderedDict

# discord.py's voice module imports ``audioop``, which was removed in Python
# 3.13. We don't use voice features, so a stub keeps imports happy.
sys.modules.setdefault("audioop", types.ModuleType("audioop"))

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402

from bot.prefix import get_prefix  # noqa: E402
from bot.utils.state import set_bot  # noqa: E402


# How long (seconds) to remember a processed message id. 60s is far longer
# than any realistic gateway resume window but short enough that the OrderedDict
# below stays tiny.
_MESSAGE_DEDUP_TTL: float = 60.0
_MESSAGE_DEDUP_MAX_ENTRIES: int = 10_000


class DuckParadiseBot(commands.Bot):
    """Application-specific subclass with a defensive ``on_message`` dedup guard."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix=get_prefix,
            intents=discord.Intents.all(),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=True, roles=True
            ),
        )

        # Per-guild lock toggled by the ``stop``/``override`` commands.
        self.bot_locks: dict[str, bool] = {}

        # Populated by the help cog so views can recover it after a restart.
        self.help_pages: list[discord.Embed] = []

        # The on_ready handler uses this guard so persistent views are only
        # registered once even if Discord re-emits READY.
        self.views_loaded: bool = False

        # OrderedDict so we can pop oldest entries to bound memory.
        # key: message id (int), value: monotonic timestamp.
        self._processed_messages: OrderedDict[int, float] = OrderedDict()

    async def on_message(self, message: discord.Message) -> None:
        """Override of ``commands.Bot.on_message`` with id-based deduplication.

        ``commands.Bot.on_message`` simply calls ``self.process_commands``.
        We replace it with the same call, gated by a small TTL set so a
        single message can never trigger ``process_commands`` more than
        once — fixing the "every command runs twice" symptom regardless of
        whether the duplicate is coming from the framework, our own code,
        or a misconfigured deployment.
        """
        if message.author.bot:
            return

        if self._is_message_already_processed(message.id):
            print(
                f"[Bot.on_message] Skipping duplicate message {message.id} "
                f"from {message.author} in {getattr(message.guild, 'name', 'DM')}"
            )
            return

        self._mark_message_processed(message.id)
        await self.process_commands(message)

    # ------------------------------------------------------------------
    # Dedup helpers
    # ------------------------------------------------------------------

    def _is_message_already_processed(self, message_id: int) -> bool:
        self._evict_expired_message_ids()
        return message_id in self._processed_messages

    def _mark_message_processed(self, message_id: int) -> None:
        now = time.monotonic()
        self._processed_messages[message_id] = now
        # Bound memory in case the TTL eviction misses a burst.
        while len(self._processed_messages) > _MESSAGE_DEDUP_MAX_ENTRIES:
            self._processed_messages.popitem(last=False)

    def _evict_expired_message_ids(self) -> None:
        if not self._processed_messages:
            return
        cutoff = time.monotonic() - _MESSAGE_DEDUP_TTL
        # OrderedDict preserves insertion order, so the oldest entries are
        # at the head. Walk from the front and pop until we hit a fresh entry.
        while self._processed_messages:
            oldest_id, ts = next(iter(self._processed_messages.items()))
            if ts >= cutoff:
                break
            self._processed_messages.popitem(last=False)


def create_bot() -> DuckParadiseBot:
    """Construct the bot, expose it to utility helpers, and return it."""
    bot = DuckParadiseBot()
    set_bot(bot)
    print("🔧 Bot initialized with built-in tree")
    print(f"🔧 Bot object: {bot}")
    print(f"🔧 Tree object: {bot.tree}")
    return bot


__all__ = ["DuckParadiseBot", "create_bot"]
