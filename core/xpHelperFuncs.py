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

"""XP and badges: the xp_earn decorator, the on_command_completion listener body that drives
monthly-goal progress, badge definitions, badge role management and badge awarding.
"""

import inspect
import random

import discord

from core import config as cfg
from core import economyHelperFuncs as econ
from core import state

BADGES = {
    "first_cast": {
        "name": "First Cast",
        "emoji": "🎣",
        "description": "Went fishing for the first time.",
        "check": lambda data, xp, extra: extra.get("fish_count", 0) >= 1,
    },
    "fish_fanatic": {
        "name": "Fish Fanatic",
        "emoji": "🐟",
        "description": "Caught 50 fish total.",
        "check": lambda data, xp, extra: extra.get("fish_count", 0) >= 50,
    },
    "first_strike": {
        "name": "First Strike",
        "emoji": "⛏️",
        "description": "Went mining for the first time.",
        "check": lambda data, xp, extra: extra.get("mine_count", 0) >= 1,
    },
    "deep_miner": {
        "name": "Deep Miner",
        "emoji": "💎",
        "description": "Completed 50 mining trips.",
        "check": lambda data, xp, extra: extra.get("mine_count", 0) >= 50,
    },
    "first_hunt": {
        "name": "First Hunt",
        "emoji": "🔫",
        "description": "Went hunting for the first time.",
        "check": lambda data, xp, extra: extra.get("hunt_count", 0) >= 1,
    },
    "big_game_hunter": {
        "name": "Big Game Hunter",
        "emoji": "🦌",
        "description": "Completed 50 hunts.",
        "check": lambda data, xp, extra: extra.get("hunt_count", 0) >= 50,
    },
    "coin_tosser": {
        "name": "Coin Tosser",
        "emoji": "🪙",
        "description": "Won a coinflip.",
        "check": lambda data, xp, extra: extra.get("coinflip_wins", 0) >= 1,
    },
    "hot_streak": {
        "name": "Hot Streak",
        "emoji": "🔥",
        "description": "Won 10 coinflips in a row.",
        "check": lambda data, xp, extra: extra.get("coinflip_win_streak", 0) >= 10,
    },
    "pocket_change": {
        "name": "Pocket Change",
        "emoji": "💵",
        "description": "Accumulated 1,000 coins.",
        "check": lambda data, xp, extra: data.get("wallet", 0) + data.get("bank", 0) >= 1000,
    },
    "millionaire": {
        "name": "Millionaire",
        "emoji": "🤑",
        "description": "Accumulated 1,000,000 coins.",
        "check": lambda data, xp, extra: data.get("wallet", 0) + data.get("bank", 0) >= 1000000,
    },
    "daily_devotee": {
        "name": "Daily Devotee",
        "emoji": "📅",
        "description": "Reached a 7 day daily streak.",
        "check": lambda data, xp, extra: data.get("daily_streak", 0) >= 7,
    },
    "streak_master": {
        "name": "Streak Master",
        "emoji": "🏆",
        "description": "Reached a 30 day daily streak.",
        "check": lambda data, xp, extra: data.get("daily_streak", 0) >= 30,
    },
    "apprentice": {
        "name": "Apprentice",
        "emoji": "⭐",
        "description": "Earned 500 XP.",
        "check": lambda data, xp, extra: xp >= 500,
    },
    "scholar": {
        "name": "Scholar",
        "emoji": "🎓",
        "description": "Earned 5,000 XP.",
        "check": lambda data, xp, extra: xp >= 5000,
    },
    "legend": {
        "name": "Legend",
        "emoji": "👑",
        "description": "Earned 25,000 XP.",
        "check": lambda data, xp, extra: xp >= 25000,
    },
    "shopaholic": {
        "name": "Shopaholic",
        "emoji": "🛍️",
        "description": "Purchased 10 items from the shop.",
        "check": lambda data, xp, extra: extra.get("shop_purchases", 0) >= 10,
    },
    "bug_hunter": {
        "name": "Bug Hunter",
        "emoji": "🦋",
        "description": "Caught 20 bugs.",
        "check": lambda data, xp, extra: extra.get("bug_count", 0) >= 20,
    },
    "treasure_hunter": {
        "name": "Treasure Hunter",
        "emoji": "🗺️",
        "description": "Dug up 20 rocks.",
        "check": lambda data, xp, extra: extra.get("dig_count", 0) >= 20,
    },
    "banker": {
        "name": "Banker",
        "emoji": "🏦",
        "description": "Deposited at least 10,000 coins into the bank.",
        "check": lambda data, xp, extra: data.get("bank", 0) >= 10000,
    },
    "duck_whisperer": {
        "name": "Duck Whisperer",
        "emoji": "🦆",
        "description": "Owned a Pet Duck.",
        "check": lambda data, xp, extra: any(
            isinstance(i, dict) and i.get("_id") == "pet_duck" or i == "pet_duck" for i in data.get("inventory", [])
        ),
    },
}


def get_badge_role_name(badge: dict) -> str:
    """Return the Discord role name for a badge, combining its emoji and display name."""
    return f"{badge['emoji']} {badge['name']}"


async def ensure_badge_role_for_guild(guild: discord.Guild, badge: dict):
    """Return the existing badge role in guild, creating it if it does not exist."""
    badge_name = get_badge_role_name(badge)
    role = discord.utils.get(guild.roles, name=badge_name)
    if role:
        return role
    return await guild.create_role(name=badge_name, reason="Badge role auto created")


async def ensure_badge_roles_for_guild(guild: discord.Guild) -> None:
    """Create every badge role in guild that does not already exist. Errors are printed but not raised."""
    for badge in BADGES.values():
        try:
            await ensure_badge_role_for_guild(guild, badge)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[Badge Roles] Could not ensure role {get_badge_role_name(badge)} in {guild.id}: {e}")


async def ensure_all_badge_roles(bot) -> None:
    """Create all badge roles across every guild the bot is currently in."""
    for guild in bot.guilds:
        if isinstance(guild, discord.Guild):
            await ensure_badge_roles_for_guild(guild)


async def get_badge_extra_data(guild_id: str, user_id: str) -> dict:
    """Return the badge counter dict for a user, or an empty dict if no record exists."""
    key = f"{guild_id}-{user_id}"
    doc = await state.badges_col.find_one({"_id": key}) or {}
    return doc.get("counters", {})


async def check_and_award_badges(
    ctx_or_channel, guild: discord.Guild, member: discord.Member, economy_data: dict
) -> None:
    """Check all badge conditions for member, award any newly earned badges, and post an announcement.
    Skipped entirely when running under pytest to avoid database side effects."""
    if cfg._running_under_pytest() and (
        cfg._looks_like_motor_collection(state.badges_col) or cfg._looks_like_motor_collection(state.xp_col)
    ):
        return
    try:
        guild_id = str(guild.id)
        user_id = str(member.id)
        key = f"{guild_id}-{user_id}"
        badge_doc = await state.badges_col.find_one({"_id": key}) or {"_id": key, "earned": [], "counters": {}}
        earned_ids = set(badge_doc.get("earned", []))
        extra = badge_doc.get("counters", {})
        xp_doc = await state.xp_col.find_one({"_id": key}) or {}
        xp = xp_doc.get("xp", 0)
        newly_earned = []
        for badge_id, badge in BADGES.items():
            if badge_id in earned_ids:
                continue
            try:
                if badge["check"](economy_data, xp, extra):
                    newly_earned.append(badge_id)
            except Exception as e:
                print(f"[badge check] {e}")
        if not newly_earned:
            return
        earned_ids.update(newly_earned)
        await state.badges_col.update_one(
            {"_id": key},
            {"$set": {"earned": list(earned_ids), "guild": guild_id, "user": user_id, "counters": extra}},
            upsert=True,
        )
        for badge_id in newly_earned:
            badge = BADGES[badge_id]
            badge_name = get_badge_role_name(badge)
            try:
                role = await ensure_badge_role_for_guild(guild, badge)
                if role not in member.roles:
                    await member.add_roles(role, reason=f"Earned badge: {badge_name}")
            except (discord.Forbidden, discord.HTTPException) as role_err:
                print(f"[Badge] Could not assign role for {badge_name}: {role_err}")
            announcement = f"🏅 {member.mention} just earned the **{badge_name}** badge! *{badge['description']}*"
            try:
                if hasattr(ctx_or_channel, "send"):
                    await ctx_or_channel.send(announcement)
            except (discord.Forbidden, discord.HTTPException):
                print(f"[Badge] Could not announce badge {badge_name} in guild {guild_id}.")
    except Exception as e:
        print(f"[Badge check error] {type(e).__name__}: {e}")


async def increment_badge_counter(guild_id: str, user_id: str, counter: str, amount: int = 1) -> None:
    """Atomically add amount to a named badge counter for a user.
    Counters drive badge checks such as fish_count, mine_count, and shop_purchases."""
    if cfg._running_under_pytest() and cfg._looks_like_motor_collection(state.badges_col):
        return
    key = f"{guild_id}-{user_id}"
    try:
        await state.badges_col.update_one(
            {"_id": key},
            {"$inc": {f"counters.{counter}": amount}, "$set": {"guild": guild_id, "user": user_id}},
            upsert=True,
        )
    except Exception:
        return


def xp_earn(min_xp: int, max_xp: int):
    """Decorator factory.  Wrap a command with XP logic: intercept ctx.send to detect failure
    responses, skip XP for staff commands, then award a random amount between min_xp and max_xp.

    Works on both plain coroutine functions ``(ctx, ...)`` and Cog methods ``(self, ctx, ...)``:
    when the wrapped callable's first parameter is named ``self`` the wrapper accepts and forwards
    it, so the decorator can sit directly under ``@commands.hybrid_command`` inside a Cog.
    functools.wraps keeps the original signature visible to discord.py's argument parser."""

    def decorator(func):
        import functools

        params = list(inspect.signature(func).parameters)
        is_method = bool(params) and params[0] == "self"

        async def run_with_xp(call, ctx):
            original_send = getattr(ctx, "send", None)
            sent_error_response = False

            def looks_like_failure_message(content: str) -> bool:
                text = content.strip().lower()
                if text.startswith(("❌", "⚠️", "⏰", "🕒", "🚫")):
                    # 🚫 covers wrong channel rejections ("can only be used in #…")
                    # and blacklist blocks, neither should award XP.
                    return True
                failure_markers = (
                    " on cooldown",
                    "try again in",
                    "you need ",
                    "don't need",
                    "you don't have",
                    "you cannot",
                    "you can't",
                    "invalid",
                    "not found",
                )
                return any(marker in text for marker in failure_markers)

            async def tracked_send(*send_args, **send_kwargs):
                nonlocal sent_error_response
                content = send_kwargs.get("content")
                if content is None and send_args:
                    content = send_args[0]
                if isinstance(content, str) and looks_like_failure_message(content):
                    sent_error_response = True
                    ctx._skip_xp_award = True
                return await original_send(*send_args, **send_kwargs)

            if callable(original_send):
                ctx.send = tracked_send
            try:
                result = await call()
            finally:
                if callable(original_send):
                    ctx.send = original_send
            guild = getattr(ctx, "guild", None)
            if guild:
                cmd_obj = getattr(ctx, "command", None)
                raw_name = getattr(cmd_obj, "name", None) or func.__name__
                command_name = str(raw_name).lower()
                if command_name in cfg.STAFF_HELP_COMMANDS:
                    return result
                if sent_error_response or getattr(ctx, "_skip_xp_award", False):
                    # Leave the flag set (rather than resetting to False) so the
                    # on_command_completion listener, which runs after this wrapper
                    # returns, can also see that this invocation didn't actually do
                    # anything and skip monthly-goal progress for it too. ctx is a
                    # fresh object per invocation, so there's no cross-command leakage.
                    ctx._skip_xp_award = True
                    return result
                if cfg._running_under_pytest() and cfg._looks_like_motor_collection(state.xp_col):
                    return result
                xp_gained = random.randint(min_xp, max_xp)
                user_id = str(getattr(ctx.author, "id", ""))
                guild_id = str(getattr(guild, "id", ""))
                key = f"{guild_id}-{user_id}"
                await state.xp_col.update_one(
                    {"_id": key}, {"$inc": {"xp": xp_gained}, "$set": {"guild": guild_id, "user": user_id}}, upsert=True
                )
                try:
                    xp_msg = f"{ctx.author.display_name}, you earned **{xp_gained} xp** by using `/{command_name}`"
                    if hasattr(ctx, "interaction") and ctx.interaction and ctx.interaction.response.is_done():
                        await ctx.interaction.followup.send(xp_msg)
                    else:
                        await ctx.send(xp_msg)
                except Exception as e:
                    print(f"[XP Decorator Error] Could not send XP message: {e}")
            return result

        if is_method:

            @functools.wraps(func)
            async def wrapper(self, ctx, *args, **kwargs):
                return await run_with_xp(lambda: func(self, ctx, *args, **kwargs), ctx)

        else:

            @functools.wraps(func)
            async def wrapper(ctx, *args, **kwargs):
                return await run_with_xp(lambda: func(ctx, *args, **kwargs), ctx)

        return wrapper

    return decorator


async def on_command_completion(ctx):
    """Fired by discord.py after any command (prefix or hybrid/slash) finishes without raising
    (main.py registers this via ``bot.add_listener(on_command_completion, "on_command_completion")``).
    Drives the generic side of the monthly rewards system: every completed command bumps the
    commands_used goal, and a handful of specific commands also bump their own dedicated
    counter (see cfg.MONTHLY_COMMAND_COUNTER_MAP). Goal completion is then checked immediately so
    rewards pay out the moment a threshold is crossed instead of waiting for the next command."""
    guild = getattr(ctx, "guild", None)
    if guild is None:
        return
    if econ._monthly_rewards_col_live_in_tests():
        return
    if getattr(ctx, "_skip_xp_award", False):
        # Commands with manual (non-decorator) cooldown/validation checks send a
        # failure message and return normally instead of raising, so discord.py still
        # fires command_completion. xp_earn already flags those invocations via
        # _skip_xp_award; reuse that here so spamming an on-cooldown command (e.g.
        # .work, .beg, .daily) can't farm monthly goal progress.
        return
    cmd_obj = getattr(ctx, "command", None)
    command_name = str(getattr(cmd_obj, "name", "") or "").lower()
    if not command_name or command_name in cfg.STAFF_HELP_COMMANDS:
        return
    try:
        guild_id, user_id = (str(guild.id), str(ctx.author.id))
        await econ.increment_monthly_goal(guild_id, user_id, "commands_used", 1)
        mapped_counter = cfg.MONTHLY_COMMAND_COUNTER_MAP.get(command_name)
        if mapped_counter:
            await econ.increment_monthly_goal(guild_id, user_id, mapped_counter, 1)
        await econ.check_and_award_monthly_rewards(ctx, guild, ctx.author)
    except Exception as e:
        print(f"[Monthly Rewards completion hook error] {type(e).__name__}: {e}")
