"""DuckParadise — top-level entry point.

This file used to hold the entire bot in 12K+ lines; it is now a thin
launcher that wires together the modular ``bot`` package. We also re-export
a small set of public names that legacy code paths and the test suite
expect to find on this module.
"""

from __future__ import annotations

# Stub audioop before discord.py imports voice modules — Python 3.13 removed it.
import sys
import types

sys.modules.setdefault("audioop", types.ModuleType("audioop"))

# ---------------------------------------------------------------------------
# Public API — these re-exports preserve the historical surface so external
# consumers (and the test suite) that ``import main`` keep working.
# ---------------------------------------------------------------------------

from discord.ext import commands  # noqa: E402,F401  (re-export)

from bot.config.constants import (  # noqa: E402,F401
    DISCORD_SERVICE_UNAVAILABLE_MESSAGE,
)
from bot.config.secrets import (  # noqa: E402,F401
    DISCORD_TOKEN as TOKEN,
    GEMINI_API_KEYS,
    MONGO_URI,
    OPENROUTER_API_KEY,
    QUOTE_API_KEY,
    TENOR_API_KEY,
)
from bot.database import (  # noqa: E402,F401
    afk_col,
    blacklist_col,
    boost_col,
    config_col,
    db,
    disabled_col,
    drop_instances_col,
    drops_col,
    duck_conversations_col,
    economy_col,
    fines_col,
    giveaway_col,
    guild_config_col,
    guild_shop_col,
    invite_config_col,
    invites_col,
    investments_col,
    logs_col,
    minigameplayerdata_col,
    mod_col,
    mongo,
    mutes_col,
    polls_col,
    quiz_col,
    reaction_col,
    reminders_col,
    roles_col,
    settings_col,
    shop_col,
    staffperms_col,
    sticky_col,
    ticket_panels_col,
    tickets_col,
    tickets_counter_col,
    vanity_col,
    welcome_col,
    xp_col,
)
from bot.utils.channels import (  # noqa: E402,F401
    check_channel,
    check_channel_setting,
)
from bot.utils.economy import (  # noqa: E402,F401
    add_balance,
    get_balance,
    get_user,
    subtract_balance,
)
from bot.utils.errors import (  # noqa: E402,F401
    is_discord_service_unavailable_error,
    is_prefix,
    send_hybrid_error,
    unwrap_command_error,
)
from bot.utils.investments import (  # noqa: E402,F401
    backfill_investment_dates_from_timestamp,
    calculate_investment_value,
    create_investment,
    get_investment_date,
)


def main() -> None:
    """Run the bot."""
    from bot.runner import run

    run()


if __name__ == "__main__":
    main()
