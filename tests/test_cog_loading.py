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

"""Structural guarantees of the Cog layout: every extension loads, every command survived the
split, main.py is the only place that dispatches commands, and the core/cogs import graph stays
a DAG (core never imports cogs, cogs never import each other, core modules only import earlier
core modules)."""

import ast
from pathlib import Path

import pytest

import main

PROJECT_ROOT = Path(__file__).parent.parent

# The one permitted import direction inside core/, from root to leaf.
CORE_ORDER = ["config", "state", "permsHelperFuncs", "economyHelperFuncs", "xpHelperFuncs", "errors"]

# Every command the bot exposed before the split (prefix name only, aliases excluded).
EXPECTED_COMMANDS = {
    "additem",
    "addmoney",
    "afk",
    "attack",
    "badges",
    "balance",
    "ban",
    "beg",
    "blacklist",
    "bugcatch",
    "buy",
    "choosejob",
    "clearwarns",
    "coinflip",
    "configure",
    "crime",
    "daily",
    "debug",
    "delitem",
    "deposit",
    "dig",
    "disable",
    "disableonetime",
    "doorgame",
    "draw",
    "drop",
    "duck",
    "duckfact",
    "duckquiz",
    "duckroll",
    "ducktowers",
    "editconfig",
    "edititem",
    "enable",
    "fish",
    "give",
    "giveaway",
    "help",
    "hunt",
    "inventory",
    "invest",
    "investstatus",
    "invitechannel",
    "inviteleaderboard",
    "invites",
    "jobstatus",
    "kick",
    "leaderboard",
    "listdisabled",
    "lottery",
    "maintenance",
    "mine",
    "mines",
    "modview",
    "monthlyrewards",
    "mute",
    "onetime",
    "override",
    "passive",
    "performance",
    "poll",
    "promoters",
    "purge",
    "quackcount",
    "quacktop",
    "quote",
    "reactionrole",
    "removeinvites",
    "removemoney",
    "reroll",
    "resetconfig",
    "reseteconomy",
    "resetinvites",
    "resetpromoters",
    "restore",
    "riddle",
    "roleadd",
    "roleremove",
    "roles",
    "say",
    "sell",
    "serverinfo",
    "setprefix",
    "shop",
    "slap",
    "slowmode",
    "staff",
    "stickynote",
    "stop",
    "swim",
    "testboost",
    "testwelcome",
    "ticketaddbutton",
    "ticketadduser",
    "ticketclose",
    "ticketdeletepanel",
    "ticketeditbutton",
    "ticketforceclose",
    "ticketlist",
    "ticketpanel",
    "ticketremovebutton",
    "ticketremoveuser",
    "ticketsetup",
    "ticketsync",
    "transcript",
    "transcriptlist",
    "transcriptsearch",
    "tutorial",
    "unban",
    "unmute",
    "unstaff",
    "unstickynote",
    "use",
    "userinfo",
    "vanityroles",
    "viewconfig",
    "viewperms",
    "warn",
    "whitelist",
    "withdraw",
    "work",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_discover_cogs_skips_templates():
    names = main.discover_cogs()
    assert "cogs._base_cog" not in names
    assert len(names) == 15


@pytest.mark.asyncio
async def test_every_extension_loads_and_every_command_survived(bot):
    loaded = await main.load_all_cogs(bot)
    assert loaded == main.discover_cogs()
    assert {cmd.name for cmd in bot.commands} == EXPECTED_COMMANDS
    # unloading runs each cog's cog_unload; nothing may be left registered afterwards
    for extension in list(bot.extensions):
        await bot.unload_extension(extension)
    assert not bot.cogs
    assert not bot.commands


def test_only_main_dispatches_commands():
    """Section 8/9 of the refactor blueprint: exactly one process_commands/invoke call site."""
    dispatchers = []
    for path in [
        PROJECT_ROOT / "main.py",
        *(PROJECT_ROOT / "cogs").glob("*.py"),
        *(PROJECT_ROOT / "core").glob("*.py"),
    ]:
        source = path.read_text(encoding="utf-8")
        if "process_commands(" in source or ".invoke(" in source:
            dispatchers.append(path.name)
    assert dispatchers == ["main.py"]


def test_import_graph_is_a_dag():
    problems = []
    for path in (PROJECT_ROOT / "core").glob("*.py"):
        if path.stem == "__init__":
            continue
        for imp in _imports(path):
            if imp == "main" or imp.startswith("cogs"):
                problems.append(f"{path.name} imports {imp}")
            if imp.startswith("core."):
                dep = imp.split(".")[1]
                if dep in CORE_ORDER and CORE_ORDER.index(dep) >= CORE_ORDER.index(path.stem):
                    problems.append(f"{path.name} imports {dep}, which is not earlier in the core order")
    for path in (PROJECT_ROOT / "cogs").glob("*.py"):
        if path.stem == "__init__":
            continue
        for imp in _imports(path):
            if imp == "main" or imp.startswith("cogs"):
                problems.append(f"{path.name} imports {imp}")
    assert problems == []
