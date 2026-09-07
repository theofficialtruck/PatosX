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

"""Vanity (status keyword) roles and invite tracking: invite cache + rate-limited fetch queue,
invite stats/leaderboard commands, join/leave attribution, and the vanity status polling loop."""

import asyncio
import time
from datetime import datetime, timezone

import discord
from discord import (
    app_commands,
)
from discord.ext import commands, tasks
from discord.ui import View

from core import permsHelperFuncs as perms
from core import state


async def get_invites_count(guild_id: int, user_id: int):
    """Return the total number of invite uses attributed to user_id in guild_id."""
    total_uses = 0
    async for code_doc in state.invites_col.find({"guild_id": str(guild_id), "inviter_id": str(user_id)}):
        try:
            total_uses += int(code_doc.get("uses", 0))
        except (TypeError, ValueError):
            pass
    return total_uses


class PromotersView(View):
    def __init__(self, ctx, mentions, per_page=10):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.mentions = mentions
        self.per_page = per_page
        self.page = 0
        self.update_buttons()

    def get_page_data(self):
        start = self.page * self.per_page
        end = start + self.per_page
        return self.mentions[start:end]

    def make_embed(self):
        total_pages = max(1, (len(self.mentions) + self.per_page - 1) // self.per_page)
        desc = "\n".join(self.get_page_data()) or "None"
        embed = discord.Embed(title="📢 Current Promoters", description=desc, color=discord.Color.blue())
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages}")
        return embed

    def update_buttons(self):
        total_pages = max(1, (len(self.mentions) + self.per_page - 1) // self.per_page)
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= total_pages - 1

    async def disable_all(self, interaction=None, message=None):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if interaction:
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
        elif message:
            await message.edit(embed=self.make_embed(), view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ You can't control this menu.", ephemeral=True)
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ You can't control this menu.", ephemeral=True)
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    async def on_timeout(self):
        await self.disable_all(message=self.message)


class InviteLeaderboardView(discord.ui.View):
    def __init__(self, ctx, entries, page_size: int = 10):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.entries = entries
        self.page_size = max(1, min(int(page_size), 25))
        self.page = 1
        self._name_cache = {}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ You can't use these buttons!", ephemeral=True)
            return False
        return True

    def _total_pages(self) -> int:
        if not self.entries:
            return 1
        return (len(self.entries) - 1) // self.page_size + 1

    def _sync_nav_buttons(self):
        total_pages = self._total_pages()
        self.prev_page.disabled = self.page <= 1
        self.next_page.disabled = self.page >= total_pages

    async def _resolve_name(self, uid_int: int) -> str:
        member = self.ctx.guild.get_member(uid_int)
        if member:
            return member.display_name
        cached = self._name_cache.get(uid_int)
        if cached:
            return cached
        try:
            fetched = await self.ctx.bot.fetch_user(uid_int)
            name = getattr(fetched, "name", None) or f"Unknown ({uid_int})"
        except Exception:
            name = f"Unknown ({uid_int})"
        self._name_cache[uid_int] = name
        return name

    async def render(self) -> discord.Embed:
        total_pages = self._total_pages()
        self.page = max(1, min(self.page, total_pages))
        self._sync_nav_buttons()

        embed = discord.Embed(title=f"🏆 Invite Leaderboard - {self.ctx.guild.name}", color=discord.Color.gold())
        if not self.entries:
            embed.description = "❌ No invite data found yet."
            embed.set_footer(text="Page 1/1")
            return embed

        start = (self.page - 1) * self.page_size
        end = min(start + self.page_size, len(self.entries))

        for idx in range(start, end):
            inviter_id, joins, leaves, net = self.entries[idx]
            username = await self._resolve_name(int(inviter_id))
            rank = idx + 1
            embed.add_field(
                name=f"#{rank} {username}",
                value=f"✅ {joins} joins | ❌ {leaves} leaves -> **{net} net**",
                inline=False,
            )

        embed.set_footer(
            text=f"Page {self.page}/{total_pages} • Showing ranks {start + 1}-{end} of {len(self.entries)}"
        )
        return embed

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        embed = await self.render()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self._total_pages(), self.page + 1)
        embed = await self.render()
        await interaction.response.edit_message(embed=embed, view=self)


class VanityInvites(commands.Cog):
    """Vanity (status keyword) roles and invite tracking: invite cache + rate-limited fetch queue,
    invite stats/leaderboard commands, join/leave attribution, and the vanity status polling loop."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False
        # Consumer task for state.invite_queue; created lazily on first need (see
        # _ensure_invite_queue_processor) and cancelled in cog_unload.
        self._invite_queue_task: asyncio.Task | None = None

    async def process_invite_queue(self):
        """Consume invite fetch requests one at a time, respecting per guild and global rate limits.
        Runs as a persistent background task started on first need (see _ensure_invite_queue_processor)."""
        while True:
            try:
                guild, future = await state.invite_queue.get()
                current_time = time.time()
                time_since_global = current_time - state.last_global_invite_fetch
                if time_since_global < state.GLOBAL_RATE_LIMIT:
                    await asyncio.sleep(state.GLOBAL_RATE_LIMIT - time_since_global)
                guild_id = guild.id
                if guild_id in state.last_invite_fetch:
                    time_since_last = current_time - state.last_invite_fetch[guild_id]
                    if time_since_last < 60:
                        await asyncio.sleep(60 - time_since_last)
                try:
                    state.last_global_invite_fetch = time.time()
                    invites = await guild.invites()
                    state.invite_cache[guild_id] = (current_time, invites)
                    state.last_invite_fetch[guild_id] = current_time
                    future.set_result(invites)
                except discord.HTTPException as e:
                    if e.status == 429:
                        print(f"⚠️ Rate limited for guild {guild.name}, waiting {e.retry_after or 10}s...")
                        await asyncio.sleep(e.retry_after if hasattr(e, "retry_after") else 10)
                        try:
                            invites = await guild.invites()
                            state.invite_cache[guild_id] = (current_time, invites)
                            state.last_invite_fetch[guild_id] = current_time
                            future.set_result(invites)
                        except Exception as retry_e:
                            print(f"❌ Retry failed for {guild.name}: {retry_e}")
                            future.set_exception(retry_e)
                    else:
                        future.set_exception(e)
                state.invite_queue.task_done()
                await asyncio.sleep(5)
            except Exception as e:
                print(f"❌ Error in invite queue processor: {e}")
                await asyncio.sleep(10)

    def _ensure_invite_queue_processor(self) -> None:
        """Start the invite queue consumer if it is not already running. The old module-level code
        did the same through a processing_invite flag; tracking the task lets cog_unload cancel it."""
        if self._invite_queue_task is None or self._invite_queue_task.done():
            self._invite_queue_task = asyncio.create_task(self.process_invite_queue())

    async def get_guild_invites(self, guild):
        """Return the cached invite list for a guild, refreshing via the queue if the cache is stale.
        Falls back to the last known list when a rate limit error is encountered."""
        guild_id = guild.id
        current_time = time.time()

        if guild_id in state.invite_cache:
            cached_data = state.invite_cache[guild_id]
            if isinstance(cached_data, tuple) and len(cached_data) == 2:
                cached_time, cached_invites = cached_data
                if current_time - cached_time < state.INVITE_CACHE_DURATION:
                    return cached_invites
            elif isinstance(cached_data, list):
                state.invite_cache[guild_id] = (current_time, cached_data)
                return cached_data
            else:
                state.invite_cache.pop(guild_id, None)

        self._ensure_invite_queue_processor()
        future = asyncio.Future()
        await state.invite_queue.put((guild, future))
        try:
            return await future
        except discord.HTTPException as e:
            if e.status == 429:
                print(f"⚠️ Rate limited for guild {guild.name}, using cached data...")
            else:
                print(f"❌ Error fetching invites for {guild.name}: {e}")

            cached_data = state.invite_cache.get(guild_id)
            if isinstance(cached_data, tuple) and len(cached_data) == 2:
                return cached_data[1]
            if isinstance(cached_data, list):
                return cached_data
            return []

    @tasks.loop(hours=1)
    async def cleanup_invite_cache(self):
        """Remove invite cache entries that have not been refreshed within twice the cache duration."""
        current_time = time.time()
        expired_keys = []
        for guild_id, cached_data in state.invite_cache.items():
            if isinstance(cached_data, tuple) and len(cached_data) == 2:
                cached_time, _ = cached_data
                if current_time - cached_time > state.INVITE_CACHE_DURATION * 2:
                    expired_keys.append(guild_id)
            elif isinstance(cached_data, list):
                expired_keys.append(guild_id)
        for key in expired_keys:
            del state.invite_cache[key]
        if expired_keys:
            print(f"🧹 Cleaned up {len(expired_keys)} expired invite cache entries")

    @tasks.loop(hours=1)
    async def update_invite_cache(self):
        """Proactively refresh the invite list for every guild to keep tracking data accurate."""
        for guild in self.bot.guilds:
            try:
                await self.get_guild_invites(guild)
                await asyncio.sleep(10)
            except Exception as e:
                print(f"⚠️ Error updating invite cache for {guild.name}: {e}")

    @commands.hybrid_command(name="vanityroles", description="Track users with keyword in status. Staff only.")
    @app_commands.describe(
        role="Role to assign", log_channel="Channel to log changes", keyword="Keyword to track in status"
    )
    @perms.staffperm("vanity")
    @perms.staff_only()
    async def vanityroles(self, ctx, role: discord.Role, log_channel: discord.TextChannel, keyword: str):
        guild = str(ctx.guild.id)
        await state.vanity_col.update_one(
            {"guild": guild},
            {"$set": {"role": role.id, "log": log_channel.id, "keyword": keyword, "users": []}},
            upsert=True,
        )
        await ctx.send(f"✅ Vanity role set for '{keyword}' -> {role.mention}")

    @commands.hybrid_command(name="promoters", description="View users with the vanity role. Staff only.")
    @perms.staffperm("vanity")
    @perms.staff_only()
    async def promoters(self, ctx):
        data = await state.vanity_col.find_one({"guild": str(ctx.guild.id)})
        users = data.get("users", []) if data else []
        mentions = []
        for uid in users:
            member = ctx.guild.get_member(uid)
            if member:
                mentions.append(member.mention)
        view = PromotersView(ctx, mentions)
        msg = await ctx.send(embed=view.make_embed(), view=view)
        view.message = msg

    @commands.hybrid_command(name="resetpromoters", description="Clear all users from the vanity role. Staff only.")
    @perms.staffperm("vanity")
    @perms.staff_only()
    async def resetpromoters(self, ctx):
        guild = str(ctx.guild.id)
        data = await state.vanity_col.find_one({"guild": guild})
        if not data:
            return await ctx.send("❌ No vanity config set.")
        await ctx.send("⚠️ Type exactly:\n`I confirm I want to reset all the promoters.`")
        try:
            msg = await self.bot.wait_for(
                "message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30
            )
        except asyncio.TimeoutError:
            return await ctx.send("❌ Timeout - cancelled.")
        if msg.content.strip() != "I confirm I want to reset all the promoters.":
            return await ctx.send("❌ Confirmation failed - cancelled.")
        r = ctx.guild.get_role(data["role"])
        removed = 0
        for uid in data["users"]:
            m = ctx.guild.get_member(uid)
            if m and r in m.roles:
                await m.remove_roles(r, reason="reset promoters")
                removed += 1
        await state.vanity_col.update_one({"guild": guild}, {"$set": {"users": []}})
        await ctx.send(
            embed=discord.Embed(
                title="🔁 Promoters Reset",
                description=f"{removed} users removed. List cleared.",
                color=discord.Color.red(),
            )
        )

    async def _repost_sticky(self, channel_id, guild_id):
        """Ask the StickyNotes cog to re-post the sticky in a vanity log channel. Looked up lazily
        through the bot (never imported) so cogs stay independent of each other."""
        sticky_cog = self.bot.get_cog("StickyNotes")
        if sticky_cog is not None:
            await sticky_cog.repost_sticky_note(channel_id, guild_id)

    async def _sync_vanity_role(self, guild, member, data, *, repost_sticky=False):
        """Award/revoke the vanity (Pond Promoters) role for a single member.

        Both the on_presence_update event and the check_all_statuses polling loop
        call this for the same member/guild, so the DB update below has to be the
        single source of truth for "did this call actually cause the change" -
        otherwise both callers can race past the in-memory role check before
        either one's add_roles/remove_roles call resolves, and both send a log
        message for the same award. find_one_and_update's filter only matches
        once (Mongo serializes writes to a document), so only the winner of the
        race proceeds to touch the role and send a message.
        """
        if member.bot:
            return
        role = guild.get_role(data["role"])
        if not role:
            return
        log_ch = guild.get_channel(data["log"])
        keyword = data["keyword"].lower()
        is_online = member.status != discord.Status.offline
        activity_text = member.activity.name.lower() if member.activity and member.activity.name else ""
        has_keyword = keyword in activity_text
        has_role = role in member.roles

        if is_online and has_keyword and not has_role:
            won = await state.vanity_col.find_one_and_update(
                {"guild": str(guild.id), "users": {"$ne": member.id}},
                {"$addToSet": {"users": member.id}},
            )
            if won is None:
                return
            await member.add_roles(role, reason="vanity match")
            if log_ch:
                await log_ch.send(
                    embed=discord.Embed(
                        title="Vanity Added ✨",
                        description=f"{member.mention} has been awarded **{role.name}** for proudly displaying our vanity `{keyword}` in their status!",
                        color=discord.Color.magenta(),
                        timestamp=datetime.now(timezone.utc),
                    ).set_thumbnail(url=member.display_avatar.url)
                )
                if repost_sticky:
                    await self._repost_sticky(log_ch.id, guild.id)
        elif is_online and not has_keyword and has_role:
            # Only revoke when the member is online without the keyword - never
            # because they went offline (their activity briefly disappearing on
            # disconnect should not be treated as "lost the vanity").
            won = await state.vanity_col.find_one_and_update(
                {"guild": str(guild.id), "users": member.id},
                {"$pull": {"users": member.id}},
            )
            if won is None:
                return
            await member.remove_roles(role, reason="vanity lost")
            if log_ch:
                await log_ch.send(
                    embed=discord.Embed(
                        title="Vanity Removed",
                        description=f"{member.mention} has lost **{role.name}** for no longer displaying our vanity `{keyword}`.",
                        color=discord.Color.light_gray(),
                        timestamp=datetime.now(timezone.utc),
                    ).set_thumbnail(url=member.display_avatar.url)
                )
                if repost_sticky:
                    await self._repost_sticky(log_ch.id, guild.id)

    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        if not self.check_all_statuses.is_running():
            self.check_all_statuses.start()
        if after.bot or not after.guild:
            return
        data = await state.vanity_col.find_one({"guild": str(after.guild.id)})
        if not data:
            return
        await self._sync_vanity_role(after.guild, after, data)

    @tasks.loop(seconds=30)
    async def check_all_statuses(self):
        for guild in self.bot.guilds:
            data = await state.vanity_col.find_one({"guild": str(guild.id)})
            if not data:
                continue
            for member in guild.members:
                if member.bot:
                    continue
                await self._sync_vanity_role(guild, member, data, repost_sticky=True)

    @commands.hybrid_command(name="invitechannel", description="Set the channel where invite joins are announced.")
    @perms.staffperm("invites")
    @perms.staff_only()
    async def invitechannel(self, ctx, channel: discord.TextChannel):
        await state.invite_config_col.update_one(
            {"guild_id": str(ctx.guild.id)}, {"$set": {"channel_id": str(channel.id)}}, upsert=True
        )
        await ctx.send(f"✅ Invite announcements will now be sent in {channel.mention}.")

    @commands.hybrid_command(name="invites", description="Check how many invites a user has.")
    @app_commands.describe(member="The user to check (optional - shows your invites if not provided)")
    @perms.staffperm("invites")
    @perms.blacklist_barrier()
    async def invites(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        stats = await state.invites_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)}) or {}
        regular = stats.get("regular", 0)
        fake = stats.get("fake", 0)
        leaves = stats.get("leaves", stats.get("left", 0))
        total_display = regular + leaves
        embed = discord.Embed(title=f"📨 Invite Stats for {member.display_name}", color=discord.Color.blurple())
        embed.add_field(name="✨ Total Invites", value=total_display, inline=False)
        embed.add_field(name="✅ Regular", value=regular, inline=True)
        embed.add_field(name="❌ Leaves", value=leaves, inline=True)
        embed.add_field(name="⚠️ Fake", value=fake, inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="removeinvites", aliases=["delinvites"], description="Remove a certain number of invites from a user."
    )
    @app_commands.describe(member="The user to remove invites from", amount="Number of invites to remove")
    @perms.staffperm("invites")
    @perms.staff_only()
    async def removeinvites(self, ctx, member: discord.Member, amount: int):
        if amount <= 0:
            return await ctx.send("❌ Please provide a **positive number** of invites to remove.")
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        stats = await state.invites_col.find_one({"guild_id": guild_id, "user_id": user_id})
        if not stats:
            return await ctx.send(f"❌ {member.mention} has no invite records.")
        total = stats.get("total", 0)
        regular = stats.get("regular", 0)
        fake = stats.get("fake", 0)
        leaves = stats.get("leaves", stats.get("left", 0))
        if total <= 0:
            return await ctx.send(f"❌ {member.mention} already has **0 invites**.")
        to_remove = amount
        if regular > 0:
            removed = min(regular, to_remove)
            regular -= removed
            to_remove -= removed
        if to_remove > 0 and fake > 0:
            removed = min(fake, to_remove)
            fake -= removed
            to_remove -= removed
        if to_remove > 0 and leaves > 0:
            removed = min(leaves, to_remove)
            leaves -= removed
            to_remove -= removed
        new_total = max(regular - leaves, 0)
        new_total = max(new_total, 0)
        await state.invites_col.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$set": {"regular": regular, "fake": fake, "leaves": leaves, "total": new_total}},
        )
        await ctx.send(f"✅ Removed **{amount} invites** from {member.mention}. New total: **{new_total}**")

    @commands.hybrid_command(
        name="inviteleaderboard",
        aliases=["invitelb"],
        description="Show the server invite leaderboard (paginated, up to top 300).",
    )
    @perms.blacklist_barrier()
    async def inviteleaderboard(self, ctx, limit: int = 300):
        guild_id = str(ctx.guild.id)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 300
        limit = max(1, min(limit, 300))

        totals = {}
        async for code_doc in state.invites_col.find({"guild_id": guild_id, "inviter_id": {"$ne": None}}):
            inviter_id = code_doc.get("inviter_id")
            if not inviter_id:
                continue
            try:
                totals[inviter_id] = totals.get(inviter_id, 0) + int(code_doc.get("uses", 0))
            except (TypeError, ValueError):
                pass

        if not totals:
            return await ctx.send("❌ No invite data found yet.")

        leaves_map = {}
        async for stats_doc in state.invites_col.find({"guild_id": guild_id, "user_id": {"$in": list(totals.keys())}}):
            inviter = stats_doc.get("user_id")
            leaves_map[inviter] = stats_doc.get("leaves", stats_doc.get("left", 0))

        sorted_inv = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        entries = []
        for inviter_id, joins in sorted_inv:
            leaves = leaves_map.get(inviter_id, 0)
            net = max(joins - leaves, 0)
            entries.append((inviter_id, joins, leaves, net))

        view = InviteLeaderboardView(ctx, entries, page_size=10)
        embed = await view.render()
        await ctx.send(embed=embed, view=view)

    @commands.command()
    @perms.staffperm("invites")
    @perms.staff_only()
    async def resetinvites(self, ctx):
        guild_id = str(ctx.guild.id)
        stats_res = await state.invites_col.delete_many({"guild_id": guild_id, "user_id": {"$exists": True}})
        upd_res = await state.invites_col.update_many(
            {"guild_id": guild_id, "code": {"$exists": True}}, {"$set": {"joined_users": []}}
        )
        try:
            current_invites = await self.get_guild_invites(ctx.guild)
            for invite in current_invites:
                await state.invites_col.update_one(
                    {"guild_id": guild_id, "code": invite.code},
                    {"$set": {"inviter_id": str(invite.inviter.id) if invite.inviter else None, "uses": invite.uses}},
                    upsert=True,
                )
        except discord.HTTPException:
            pass
        await ctx.send(
            f"✅ Reset invites for this server.\nCleared {stats_res.deleted_count} inviter records and refreshed {upd_res.modified_count} invite codes."
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Fired when a member leaves or is removed. Updates invite leave counts to keep stats accurate."""
        guild = member.guild
        code_doc = await state.invites_col.find_one({"guild_id": str(guild.id), "joined_users": str(member.id)})
        if code_doc:
            inviter_id = code_doc.get("inviter_id")
            await state.invites_col.update_one(
                {"guild_id": str(guild.id), "code": code_doc.get("code")}, {"$pull": {"joined_users": str(member.id)}}
            )
            if inviter_id:
                stats = await state.invites_col.find_one({"guild_id": str(guild.id), "user_id": str(inviter_id)})
                joins = stats.get("joins", stats.get("regular", 0)) if stats else 0
                leaves = stats.get("leaves", stats.get("left", 0)) if stats else 0
                await state.invites_col.update_one(
                    {"guild_id": str(guild.id), "user_id": str(inviter_id)},
                    {"$inc": {"leaves": 1}, "$set": {"total": max(joins - (leaves + 1), 0)}},
                    upsert=True,
                )

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the invite cache loops and sync every guild's invite list into MongoDB (runs once)."""
        if self._ready_done:
            return
        self._ready_done = True
        self.cleanup_invite_cache.start()
        self.update_invite_cache.start()
        print("🔄 Started invite cache management tasks")
        for guild in self.bot.guilds:
            if not isinstance(guild, discord.Guild):
                continue
            try:
                current_invites = await self.get_guild_invites(guild)
                state.invite_cache[guild.id] = (time.time(), current_invites)
                for invite in current_invites:
                    await state.invites_col.update_one(
                        {"guild_id": str(guild.id), "code": invite.code},
                        {
                            "$set": {
                                "inviter_id": str(invite.inviter.id) if invite.inviter else None,
                                "uses": invite.uses,
                            }
                        },
                        upsert=True,
                    )
            except Exception as e:
                state.invite_cache[guild.id] = (0, [])
                print(f"❌ Failed to fetch invites for guild {guild}: {e}")
        print("✅ Invite cache synced with MongoDB.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Work out which invite a new member used and credit the inviter."""
        guild = member.guild
        try:
            new_invites = await self.get_guild_invites(guild)
            old_invites_data = state.invite_cache.get(guild.id, (time.time(), []))
            if isinstance(old_invites_data, tuple) and len(old_invites_data) == 2:
                _, old_invites = old_invites_data
            else:
                old_invites = old_invites_data if isinstance(old_invites_data, list) else []
            used_invite = None
            for new_inv in new_invites:
                old = discord.utils.get(old_invites, code=new_inv.code)
                if old and new_inv.uses > old.uses:
                    used_invite = new_inv
                    break
            state.invite_cache[guild.id] = (time.time(), new_invites)
            if used_invite:
                inviter = used_invite.inviter
                await state.invites_col.update_one(
                    {"guild_id": str(guild.id), "user_id": str(inviter.id)},
                    {"$inc": {"total": 1, "regular": 1, "joins": 1}},
                    upsert=True,
                )
                await state.invites_col.update_one(
                    {"guild_id": str(guild.id), "code": used_invite.code},
                    {
                        "$set": {"inviter_id": str(inviter.id), "uses": used_invite.uses},
                        "$addToSet": {"joined_users": str(member.id)},
                    },
                    upsert=True,
                )
                config = await state.invite_config_col.find_one({"guild_id": str(guild.id)})
                if config:
                    channel = guild.get_channel(int(config["channel_id"]))
                    if channel:
                        await channel.send(
                            f"👋 Welcome {member.mention}! Invited by {inviter.mention} (now **{used_invite.uses}** uses)"
                        )
        except Exception as e:
            print("on_member_join ERROR:", e)

    def cog_unload(self):
        """Cancel every loop/task this cog owns; nothing else does it for us."""
        for loop in (self.cleanup_invite_cache, self.update_invite_cache, self.check_all_statuses):
            if loop.is_running():
                loop.cancel()
        if self._invite_queue_task is not None and not self._invite_queue_task.done():
            self._invite_queue_task.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VanityInvites(bot))
