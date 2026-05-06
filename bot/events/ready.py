"""``on_ready`` orchestrator: persistent views, background tasks, slash sync."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands

from bot.database import (
    invites_col,
    mutes_col,
    polls_col,
    roles_col,
    settings_col,
    ticket_panels_col,
)
from bot.database.seeds import ensure_shop_items
from bot.tasks import all_tasks
from bot.utils.invites_cache import invite_cache
from bot.utils.investments import backfill_investment_dates_from_timestamp
from bot.utils.invites_cache import get_guild_invites
from bot.utils.stickies import (
    load_onetime_channels,
    load_sticky_messages,
    load_sticky_notes,
)
from bot.views.drops import DropClaimView
from bot.views.giveaways import resume_giveaways
from bot.views.polls import PollView
from bot.views.roles import RoleButtons
from bot.views.tickets import TicketPanelView


class ReadyCog(commands.Cog, name="Ready"):
    """One-shot startup logic: persistent views, tasks, and command sync."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._http_session: aiohttp.ClientSession | None = None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        bot = self.bot
        # Guard rerun on Discord re-emitting READY.
        if getattr(bot, "views_loaded", False):
            return
        bot.views_loaded = True

        print(f"Logging in as {bot.user}...")

        # Optional one-time data migration.
        if os.getenv(
            "RUN_INVESTMENT_DATE_BACKFILL", "true"
        ).strip().lower() in {"1", "true", "yes", "on"}:
            try:
                stats = await backfill_investment_dates_from_timestamp()
                print(
                    "[Investment Date Backfill] "
                    f"scanned={stats['scanned']} "
                    f"updated={stats['updated']} "
                    f"invalid_timestamp={stats['invalid_timestamp']} "
                    f"skipped_conflict={stats['skipped_conflict']} "
                    f"write_errors={stats['write_errors']}"
                )
            except Exception as exc:
                print(
                    f"[Investment Date Backfill] failed: "
                    f"{type(exc).__name__} - {exc}"
                )

        print("⚠️ Automatic sync disabled - use ?sync manually")

        for task in all_tasks():
            if not task.is_running():
                task.start()

        await load_sticky_messages()

        # Ensure each guild has a Muted role and re-apply persistent mutes.
        mute_role_name = "Muted"
        for guild in bot.guilds:
            mute_role = discord.utils.get(guild.roles, name=mute_role_name)
            if not mute_role:
                mute_role = await guild.create_role(name=mute_role_name)
                for ch in guild.channels:
                    await ch.set_permissions(
                        mute_role, speak=False, send_messages=False
                    )

            async for doc in mutes_col.find({"guild_id": guild.id}):
                member = guild.get_member(doc["user_id"])
                if not member:
                    continue
                mute_end = doc.get("mute_end")
                if mute_end:
                    if isinstance(mute_end, str):
                        try:
                            mute_end = datetime.fromisoformat(mute_end)
                        except ValueError:
                            mute_end = datetime.strptime(
                                mute_end, "%Y-%m-%d %H:%M:%S"
                            )
                    if mute_end.tzinfo is None:
                        mute_end = mute_end.replace(tzinfo=timezone.utc)

                    if datetime.now(timezone.utc) >= mute_end:
                        await mutes_col.delete_one({"_id": doc["_id"]})
                        continue
                    if mute_role and mute_role not in member.roles:
                        await member.add_roles(
                            mute_role, reason="Reapplying mute after restart"
                        )

        # Persistent role-button views.
        async for doc in roles_col.find({}):
            guild_id = doc["_id"]
            guild = bot.get_guild(guild_id)
            if not guild:
                continue
            role_ids = doc.get("roles", [])
            if not role_ids:
                continue
            view = RoleButtons(role_ids, guild_id, guild)
            bot.add_view(view)

        print("✅ Persistent role buttons loaded.")
        bot.add_view(DropClaimView())

        await load_sticky_notes()
        await load_onetime_channels()

        # Resume giveaways and active polls.
        asyncio.create_task(resume_giveaways(bot))
        now = datetime.now(timezone.utc)
        async for poll in polls_col.find({"end_time": {"$gt": now}}):
            try:
                channel = bot.get_channel(int(poll["channel_id"]))
                if not channel:
                    continue
                msg = await channel.fetch_message(int(poll["message_id"]))
                view = PollView(poll["poll_id"], poll["options"])
                await msg.edit(view=view)
                print(f"🔄 Restored poll {poll['poll_id']}")
            except Exception as exc:
                print(f"Failed to restore poll {poll['poll_id']}: {exc}")

        await bot.wait_until_ready()

        for guild in bot.guilds:
            if not isinstance(guild, discord.Guild):
                continue
            try:
                current_invites = await get_guild_invites(guild)
                invite_cache[guild.id] = current_invites
                for invite in current_invites:
                    await invites_col.update_one(
                        {"guild_id": str(guild.id), "code": invite.code},
                        {
                            "$set": {
                                "inviter_id": (
                                    str(invite.inviter.id) if invite.inviter else None
                                ),
                                "uses": invite.uses,
                            }
                        },
                        upsert=True,
                    )
            except Exception as exc:
                invite_cache[guild.id] = []
                print(f"❌ Failed to fetch invites for guild {guild}: {exc}")

        print("✅ Invite cache synced with MongoDB.")

        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening, name="thetruck"
            )
        )

        if self._http_session is None:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

        await self._restore_ticket_panels()
        await ensure_shop_items()
        print("✅ Persistent ticket panel views loaded.")
        print(f"🎉 Bot ready! Logged in as {bot.user}")

    async def _restore_ticket_panels(self) -> None:
        bot = self.bot

        panels = await ticket_panels_col.find({}).to_list(length=None)
        for panel in panels:
            try:
                view = TicketPanelView(panel)
                if panel.get("message_id") and panel.get("channel_id"):
                    try:
                        channel = bot.get_channel(int(panel["channel_id"]))
                        if channel:
                            message = await channel.fetch_message(panel["message_id"])
                            await message.edit(view=view)
                            print(
                                "✅ Reattached view to panel message "
                                f"{panel['message_id']}"
                            )
                            continue
                    except Exception as exc:
                        print(
                            f"Could not reattach view to message "
                            f"{panel.get('message_id')}: {exc}"
                        )
                bot.add_view(view)
                print(
                    f"✅ Registered global view for panel {panel.get('panel_name')}"
                )
            except Exception as exc:
                print(
                    f"Failed to register view for {panel.get('panel_name')}: {exc}"
                )

        # Per-guild fallback (legacy data shape).
        guilds = await settings_col.distinct("guild")
        for guild_id in guilds:
            panels = await ticket_panels_col.find(
                {"guild": str(guild_id)}
            ).to_list(length=50)
            for panel_data in panels:
                try:
                    view = TicketPanelView(panel_data)
                    if (
                        panel_data.get("message_id")
                        and panel_data.get("channel_id")
                    ):
                        try:
                            channel = bot.get_channel(int(panel_data["channel_id"]))
                            if channel:
                                message = await channel.fetch_message(
                                    panel_data["message_id"]
                                )
                                await message.edit(view=view)
                                continue
                        except Exception as exc:
                            print(
                                "Could not reattach guild view to message "
                                f"{panel_data.get('message_id')}: {exc}"
                            )
                    bot.add_view(view)
                except Exception as exc:
                    print(
                        f"Failed to register guild view for "
                        f"{panel_data.get('panel_name')}: {exc}"
                    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReadyCog(bot))
