"""Staff performance analytics — pulled out of the moderation cog.

These functions read from ``mod_col`` and aggregate per-staff statistics for
the ``performance`` command. Kept dependency-light so the moderation cog can
import them without pulling in any UI code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser

from bot.database import mod_col


async def generate_performance_analytics(guild_id, staff_id, days: int = 30) -> dict:
    """Aggregate punishments + notes for ``staff_id`` over the last ``days``."""
    try:
        analytics = {
            "total_actions": 0,
            "total_messages": 0,
            "commands_used": 0,
            "staff_since": "Unknown",
            "punishments": {"warn": 0, "mute": 0, "kick": 0, "ban": 0, "total": 0},
            "avg_actions_per_day": 0,
            "most_active_day": "Monday",
            "peak_hour": 14,
            "active_this_week": False,
            "recent_activity": [],
        }

        days_ago = datetime.now(timezone.utc) - timedelta(days=days)
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

        mod_data = await mod_col.find({"guild": str(guild_id)}).to_list(length=None)

        action_counters = {
            "warnings": "warn",
            "mutes": "mute",
            "kicks": "kick",
            "bans": "ban",
        }

        for doc in mod_data:
            for key, label in action_counters.items():
                for action in doc.get(key, []):
                    if action.get("by") != str(staff_id):
                        continue
                    try:
                        action_time = date_parser.isoparse(action["time"])
                    except Exception:
                        continue
                    if action_time >= days_ago:
                        analytics["punishments"][label] += 1
                        analytics["total_actions"] += 1

        analytics["punishments"]["total"] = sum(
            analytics["punishments"][p] for p in ("warn", "mute", "kick", "ban")
        )
        analytics["commands_used"] = analytics["punishments"]["total"]

        for doc in mod_data:
            for note in doc.get("notes", []):
                if note.get("by") != str(staff_id):
                    continue
                try:
                    note_time = date_parser.isoparse(note["time"])
                except Exception:
                    continue
                if note_time >= days_ago:
                    analytics["commands_used"] += 1

        earliest_action = None
        for doc in mod_data:
            for key in ("warnings", "mutes", "kicks", "bans", "notes"):
                for action in doc.get(key, []):
                    if action.get("by") != str(staff_id):
                        continue
                    try:
                        action_time = date_parser.isoparse(action["time"])
                    except Exception:
                        continue
                    if not earliest_action or action_time < earliest_action:
                        earliest_action = action_time

        if earliest_action:
            analytics["staff_since"] = earliest_action.strftime("%b %d, %Y")

        analytics["total_messages"] = await get_user_message_count(
            guild_id, staff_id, days_ago
        )

        analytics["avg_actions_per_day"] = (
            analytics["total_actions"] / days if analytics["total_actions"] > 0 else 0
        )

        analytics["active_this_week"] = analytics["total_actions"] > 0 and any(
            (item.get("time") and date_parser.isoparse(item["time"]) >= seven_days_ago)
            for doc in mod_data
            for key in ("warnings", "mutes", "kicks", "bans")
            for item in doc.get(key, [])
            if isinstance(item, dict) and item.get("by") == str(staff_id)
        )

        expected_daily = 2
        analytics["efficiency"] = min(
            100, (analytics["avg_actions_per_day"] / expected_daily) * 100
        )

        analytics["recent_activity"] = [
            f"Used {ptype} command"
            for ptype, count in analytics["punishments"].items()
            if ptype != "total" and count > 0
        ][:3]

        if not analytics["recent_activity"]:
            analytics["recent_activity"] = ["No recent activity"]

        return analytics

    except Exception as exc:
        print(f"[Performance Analytics Error] {exc}")
        return {
            "total_actions": 0,
            "total_messages": 0,
            "commands_used": 0,
            "staff_since": "Unknown",
            "punishments": {"warn": 0, "mute": 0, "kick": 0, "ban": 0, "total": 0},
            "avg_actions_per_day": 0,
            "most_active_day": "Monday",
            "peak_hour": 14,
            "active_this_week": False,
            "efficiency": 0,
            "recent_activity": ["No data available"],
        }


async def get_user_message_count(guild_id, user_id, since_date) -> int:
    """Approximate message count by summing this staff member's actions."""
    try:
        message_count = 0
        mod_data = await mod_col.find({"guild": str(guild_id)}).to_list(length=None)
        for doc in mod_data:
            for key in ("warnings", "mutes", "kicks", "bans", "notes"):
                for action in doc.get(key, []):
                    if action.get("by") != str(user_id):
                        continue
                    try:
                        action_time = date_parser.isoparse(action["time"])
                    except Exception:
                        continue
                    if action_time >= since_date:
                        message_count += 1
        return message_count
    except Exception as exc:
        print(f"[Message Count Error] {exc}")
        return 0


__all__ = ["generate_performance_analytics", "get_user_message_count"]
