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

"""Core economy commands: balance, daily, beg, bank transfers, giving, selling, investments,
passive mode, leaderboards, badges and monthly rewards display."""

import random
import re
import traceback
from datetime import datetime, timedelta, timezone

import discord
from discord import (
    app_commands,
)
from discord.ext import commands
from discord.ui import Button, Select, View
from pytz import UTC

from core import config as cfg
from core import economyHelperFuncs as econ
from core import permsHelperFuncs as perms
from core import state
from core import xpHelperFuncs as xp


class LeaderboardView(discord.ui.View):
    def __init__(self, ctx, page_size: int = 10, max_entries: int = 1000):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.page_size = max(1, min(int(page_size), 25))
        self.max_entries = max(10, int(max_entries))

        self.mode = "money"  # money|xp
        self.page = 1

        self._money_users = None  # list[(uid, total)] sorted
        self._xp_users = None  # list[(uid, xp)] sorted

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ You can't use these buttons!", ephemeral=True)
            return False
        return True

    def _total_pages(self, top_n: int) -> int:
        if top_n <= 0:
            return 1
        return (top_n - 1) // self.page_size + 1

    def _sync_nav_buttons(self, total_pages: int):
        self.prev_page.disabled = self.page <= 1
        self.next_page.disabled = self.page >= total_pages

    async def _load_money_users(self):
        # Only pull the top N from Mongo so large servers don't lag the bot process.
        # Use the _id prefix ("{guildId}-{userId}") to include older docs that may not have guild/user fields set.
        gid = str(self.ctx.guild.id)
        pipeline = [
            {"$match": {"_id": {"$regex": f"^{gid}-"}}},
            {
                "$project": {
                    "_id": 1,
                    "user": {
                        "$ifNull": [
                            "$user",
                            {"$arrayElemAt": [{"$split": ["$_id", "-"]}, 1]},
                        ]
                    },
                    "total": {
                        "$add": [
                            {"$ifNull": ["$wallet", 0]},
                            {"$ifNull": ["$bank", 0]},
                        ]
                    },
                }
            },
            {"$sort": {"total": -1}},
            {"$limit": int(self.max_entries)},
        ]
        users = []
        cursor = state.economy_col.aggregate(pipeline, allowDiskUse=True)
        async for doc in cursor:
            try:
                uid = int(doc.get("user"))
            except (TypeError, ValueError):
                continue
            users.append((uid, int(doc.get("total", 0))))
        self._money_users = users

    async def _load_xp_users(self):
        gid = str(self.ctx.guild.id)
        users = []
        cursor = (
            state.xp_col.find({"guild": gid, "user": {"$exists": True}}, {"user": 1, "xp": 1})
            .sort("xp", -1)
            .limit(int(self.max_entries))
        )
        async for doc in cursor:
            try:
                uid = int(doc.get("user"))
            except (TypeError, ValueError):
                continue
            users.append((uid, int(doc.get("xp", 0))))
        self._xp_users = users

    async def render(self) -> discord.Embed:
        if self.mode == "xp":
            if self._xp_users is None:
                await self._load_xp_users()
            return await self._render_xp()

        if self._money_users is None:
            await self._load_money_users()
        return await self._render_money()

    async def _render_money(self) -> discord.Embed:
        users = self._money_users or []
        top = users[: self.max_entries]
        total_pages = self._total_pages(len(top))
        self.page = max(1, min(self.page, total_pages))
        self._sync_nav_buttons(total_pages)

        embed = discord.Embed(title="🏆 Leaderboard - Richest Users", color=discord.Color.teal())
        if not top:
            embed.description = "❌ No economy data found yet."
            embed.set_footer(text="Page 1/1")
            return embed

        start = (self.page - 1) * self.page_size
        end = min(start + self.page_size, len(top))

        for idx in range(start, end):
            uid, total = top[idx]
            member = self.ctx.guild.get_member(uid)
            name = member.display_name if member else f"Unknown User ({uid})"
            rank = idx + 1
            embed.add_field(name=f"#{rank} {name}", value=f"🪙 {total} coins", inline=False)

        overall_rank = next((i + 1 for i, (uid, _) in enumerate(users) if uid == self.ctx.author.id), None)
        user_total = next((t for uid, t in users if uid == self.ctx.author.id), 0)
        footer_bits = [
            f"Page {self.page}/{total_pages}",
            f"Showing ranks {start + 1}-{end} of {len(top)}",
        ]
        if overall_rank:
            footer_bits.append(f"Your Rank: #{overall_rank} • 🪙 {user_total} coins")
        embed.set_footer(text=" • ".join(footer_bits))
        return embed

    async def _render_xp(self) -> discord.Embed:
        users = self._xp_users or []
        top = users[: self.max_entries]
        total_pages = self._total_pages(len(top))
        self.page = max(1, min(self.page, total_pages))
        self._sync_nav_buttons(total_pages)

        embed = discord.Embed(title="🏆 Leaderboard - Most XP", color=discord.Color.gold())
        if not top:
            embed.description = "❌ No XP data found yet."
            embed.set_footer(text="Page 1/1")
            return embed

        start = (self.page - 1) * self.page_size
        end = min(start + self.page_size, len(top))

        for idx in range(start, end):
            uid, xp_amount = top[idx]
            member = self.ctx.guild.get_member(uid)
            name = member.display_name if member else f"Unknown User ({uid})"
            rank = idx + 1
            embed.add_field(name=f"#{rank} {name}", value=f"⭐ {xp_amount} XP", inline=False)

        overall_rank = next((i + 1 for i, (uid, _) in enumerate(users) if uid == self.ctx.author.id), None)
        user_xp = next((x for uid, x in users if uid == self.ctx.author.id), 0)
        footer_bits = [
            f"Page {self.page}/{total_pages}",
            f"Showing ranks {start + 1}-{end} of {len(top)}",
        ]
        if overall_rank:
            footer_bits.append(f"Your Rank: #{overall_rank} • ⭐ {user_xp} XP")
        embed.set_footer(text=" • ".join(footer_bits))
        return embed

    @discord.ui.button(label="Money", style=discord.ButtonStyle.primary)
    async def money_lb(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = "money"
        self.page = 1
        embed = await self.render()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="XP", style=discord.ButtonStyle.secondary)
    async def xp_lb(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = "xp"
        self.page = 1
        embed = await self.render()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        embed = await self.render()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        embed = await self.render()
        await interaction.response.edit_message(embed=embed, view=self)


def sell_summary_text(sold_items) -> str:
    """Join the sold-item lines, truncated so the embed description stays within Discord's 4096 limit."""
    desc = "\n".join(sold_items)
    if len(desc) > 4096:
        desc = desc[:4093] + "..."
    return desc


class ConfirmSellAll(View):
    def __init__(self, ctx, prices, inventory, user_id, wallet):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.value = None
        self.prices = prices
        self.inventory = inventory
        self.user_id = user_id
        self.wallet = wallet

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()


class Economy(commands.Cog):
    """Core economy commands: balance, daily, beg, bank transfers, giving, selling, investments,
    passive mode, leaderboards, badges and monthly rewards display."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="balance", description="Check your balance.", aliases=["bal"])
    @app_commands.describe(
        member_name="The member whose balance to check (optional - shows your balance if not provided)"
    )
    @perms.blacklist_barrier()
    @xp.xp_earn(4, 8)
    async def balance(self, ctx, member_name: str | None = None):
        """Display the wallet and bank balance for the invoker or a named member."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            if not member_name:
                member = ctx.author
            else:
                member = None
                if member_name.isdigit():
                    try:
                        member = await ctx.guild.fetch_member(int(member_name))
                    except (discord.NotFound, discord.HTTPException):
                        pass
                if not member:
                    mention_match = re.match("<@!?(\\d+)>", member_name)
                    if mention_match:
                        user_id = int(mention_match.group(1))
                        try:
                            member = await ctx.guild.fetch_member(user_id)
                        except (discord.NotFound, discord.HTTPException):
                            pass
                if not member:
                    search_term = member_name.lower()
                    matches = [
                        m
                        for m in ctx.guild.members
                        if m.display_name.lower().startswith(search_term) or m.name.lower().startswith(search_term)
                    ]
                    if len(matches) == 0:
                        await ctx.send(f"⚠️ No members found matching `{member_name}`.")
                        return
                    elif len(matches) > 1:
                        names = ", ".join([m.display_name for m in matches[:10]])
                        await ctx.send(f"⚠️ Multiple members found: {names}\nPlease be more specific.")
                        return
                    else:
                        member = matches[0]
            data = await econ.get_user(ctx, ctx.guild.id, member.id)
            wallet = data.get("wallet", 0)
            bank = data.get("bank", 0)
            wallet_display = f"🪙 {wallet}" if wallet >= 0 else f"🪙 -{abs(wallet)} ❌ (debt)"
            bank_display = f"🏦 {bank}"
            embed = discord.Embed(title=f"{member.display_name}'s Balance", color=discord.Color.gold())
            embed.add_field(name="Wallet", value=wallet_display, inline=True)
            embed.add_field(name="Bank", value=bank_display, inline=True)
            user_id = f"{ctx.guild.id}-{member.id}"
            user_data = await state.economy_col.find_one({"_id": user_id}) or {}
            passive_until = user_data.get("passive_until")
            if passive_until:
                dt = datetime.fromisoformat(passive_until)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if dt > now:
                    rem = dt - now
                    hours = rem.seconds // 3600
                    mins = rem.seconds % 3600 // 60
                    embed.add_field(
                        name="🛡️ Passive Mode", value=f"Active for {rem.days}d {hours}h {mins}m", inline=False
                    )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send("⚠️ Something went wrong while fetching balance. Please contact " + cfg.BOT_ADMIN_NAME + ".")
            print(f"[ERROR] balance command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @commands.hybrid_command(name="daily", description="Claim your daily reward.", aliases=["collect"])
    @perms.blacklist_barrier()
    @xp.xp_earn(10, 20)
    async def daily(self, ctx):
        """Award the daily coin bonus, maintaining and rewarding a consecutive day streak."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            now = datetime.now(timezone.utc)
            last_daily = data.get("last_daily")
            if last_daily:
                try:
                    last_time = datetime.fromisoformat(last_daily)
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=timezone.utc)
                    time_since_last = now - last_time
                    if time_since_last < timedelta(hours=24):
                        remaining = timedelta(hours=24) - time_since_last
                        hours = remaining.seconds // 3600
                        minutes = remaining.seconds // 60 % 60
                        return await ctx.send(f"🕒 Claim again in {hours}h {minutes}m")
                    current_streak = data.get("daily_streak", 0) + 1
                except Exception as e:
                    print(f"[DAILY] Failed to parse timestamp: {e}")
                    current_streak = 0
            else:
                current_streak = 0
            current_streak = min(current_streak, 30)
            if current_streak == 0:
                reward = 300
            else:
                reward = 50 * current_streak
            await econ.add_balance(ctx.author.id, ctx.guild.id, reward)
            saved_streak = current_streak + 1 if current_streak == 0 else current_streak
            await state.economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"last_daily": now.isoformat(), "daily_streak": saved_streak}},
            )
            embed = discord.Embed(
                title="🎁 Daily Reward Claimed!",
                description=f"💰 You earned **{reward} coins**!",
                color=discord.Color.gold(),
                timestamp=now,
            )
            embed.add_field(
                name="🔥 Current Streak",
                value=f"Day **{current_streak}** of 30" if current_streak > 0 else "First time claiming!",
                inline=True,
            )
            next_streak = current_streak + 1 if current_streak > 0 else 1
            embed.add_field(name="📈 Next Reward", value=f"**{50 * min(next_streak, 30)}** coins", inline=True)
            progress = current_streak / 30
            progress_bar = "🟦" * int(progress * 10) + "⬜" * (10 - int(progress * 10))
            embed.add_field(
                name="📊 Monthly Progress", value=f"{progress_bar} ({current_streak}/30 days)", inline=False
            )
            if current_streak == 0:
                embed.set_footer(text="🌟 Welcome bonus! Keep claiming daily for bigger rewards!")
            elif current_streak == 1:
                embed.set_footer(text="🔥 Streak started! Keep claiming daily for bigger rewards!")
            elif current_streak == 7:
                embed.set_footer(text="🔥 Week streak! You're on fire!")
            elif current_streak == 14:
                embed.set_footer(text="💎 Two weeks! Amazing consistency!")
            elif current_streak == 30:
                embed.set_footer(text="👑 Perfect month! Maximum reward achieved!")
            else:
                embed.set_footer(text=f"💪 Keep it up! {30 - current_streak} days to next reward!")
            await ctx.send(embed=embed)
            fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
        except Exception as e:
            print(f"[ERROR] daily command: {type(e).__name__} - {e}")
            traceback.print_exc()
            await ctx.send(
                "⚠️ Something went wrong while collecting your daily. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(name="beg", description="Beg for coins.")
    @perms.blacklist_barrier()
    @xp.xp_earn(8, 16)
    async def beg(self, ctx):
        """Beg a random donor for coins. Has a 15 minute cooldown and a lucky cookie bonus."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            now = datetime.now(timezone.utc)
            last_beg = data.get("last_beg")
            if last_beg:
                try:
                    last_time = datetime.fromisoformat(last_beg)
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=timezone.utc)
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=timezone.utc)
                    if now - last_time < timedelta(minutes=15):
                        remaining = timedelta(minutes=15) - (now - last_time)
                        minutes = remaining.seconds // 60
                        return await ctx.send(f"🕒 You can beg again in {minutes} minutes.")
                except Exception as e:
                    print(f"[BEG] Failed to parse timestamp: {e}")
            amount = random.randint(50, 200)
            inventory = data.get("inventory", [])
            has_cookie = econ.pop_food_item(inventory, "lucky_cookie")
            earnings_multiplier = 2.0 if has_cookie else 1.0
            duck_used = False
            for i, item in enumerate(inventory):
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    earnings_multiplier *= 1.3
                    item["uses_left"] -= 1
                    await ctx.send("🦆 Your Pet Duck boosted your begging earnings by 30%!")
                    duck_used = True
                    break
            nitro_used, nitro_expired = econ.consume_nitro_boost(inventory)
            nitro_reduction_seconds = 0
            if nitro_used:
                nitro_reduction_seconds = int(900 * cfg.NITRO_BOOST_COOLDOWN_REDUCTION_PCT)
                await ctx.send(
                    f"🚀 Your Nitro Boost cut your next beg cooldown by {nitro_reduction_seconds // 60} minutes!"
                )
                if nitro_expired:
                    await ctx.send("💨 Your Nitro Boost ran out after 3 uses.")
            amount = int(amount * earnings_multiplier)
            if duck_used or has_cookie or nitro_used:
                await state.economy_col.update_one(
                    {"_id": f"{ctx.guild.id}-{ctx.author.id}"}, {"$set": {"inventory": inventory}}, upsert=True
                )
            donor = random.choice(cfg.BEG_DONORS)
            await econ.add_balance(ctx.author.id, ctx.guild.id, amount)
            effective_beg_ts = now - timedelta(seconds=nitro_reduction_seconds)
            await state.economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"last_beg": effective_beg_ts.isoformat(timespec="seconds")}},
            )
            msg = f"🙇 {donor} was kind enough to donate **{amount} coins** to you!"
            if has_cookie:
                msg += "\n🍪 **Lucky Cookie consumed!** Earnings doubled!"
            await ctx.send(msg)
        except Exception as e:
            print(f"[ERROR] beg command: {type(e).__name__} - {e}")
            traceback.print_exc()
            await ctx.send("⚠️ Something went wrong while begging. Please contact " + cfg.BOT_ADMIN_NAME + ".")

    @commands.hybrid_command(name="deposit", description="Deposit to bank.", aliases=["dep"])
    @app_commands.describe(amount="Amount to deposit (supports k, m, b suffixes or 'all')")
    @perms.blacklist_barrier()
    @xp.xp_earn(5, 10)
    async def deposit(self, ctx, amount: str):
        """Move coins from wallet to bank. Applies a 5 percent deposit tax."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data["wallet"]
            if amount.lower() == "all":
                if wallet <= 0:
                    return await ctx.send("❌ You have no coins to deposit.")
                deposit_amount = wallet
            else:
                # Accepts plain numbers plus the k/m/b suffixes the slash description promises
                deposit_amount = econ.parse_amount(amount)
                if deposit_amount is None:
                    return await ctx.send("❌ Please enter a valid number or `all`.")
                if deposit_amount <= 0:
                    return await ctx.send("❌ Invalid deposit amount.")
                if deposit_amount > wallet:
                    return await ctx.send("❌ You can't afford that!")
            taxed_amount = int(deposit_amount * 0.95)
            await state.economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"wallet": wallet - deposit_amount, "bank": data["bank"] + taxed_amount}},
            )
            await ctx.send(
                f"🏦 You deposited {deposit_amount} coins.\n💸 After 5% tax, you received {taxed_amount} coins in your bank."
            )
        except Exception as e:
            await ctx.send(
                "⚠️ Something went wrong while processing your deposit. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )
            print(f"[ERROR] deposit command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @commands.hybrid_command(name="withdraw", description="Withdraw from bank.", aliases=["with"])
    @app_commands.describe(amount="Amount to withdraw (supports k, m, b suffixes or 'all')")
    @perms.blacklist_barrier()
    @xp.xp_earn(5, 10)
    async def withdraw(self, ctx, amount: str):
        """Move coins from bank to wallet. No fee applied on withdrawal."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            bank = data["bank"]
            if amount.lower() == "all":
                if bank <= 0:
                    return await ctx.send("❌ You have no coins to withdraw.")
                withdraw_amount = bank
            else:
                withdraw_amount = econ.parse_amount(amount)
                if withdraw_amount is None:
                    return await ctx.send("❌ Please enter a valid number or `all`.")
                if withdraw_amount <= 0:
                    return await ctx.send("❌ Invalid withdrawal amount.")
                if withdraw_amount > bank:
                    return await ctx.send("❌ You can't afford that")
            await state.economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"wallet": data["wallet"] + withdraw_amount, "bank": bank - withdraw_amount}},
            )
            await ctx.send(f"💰 You withdrew {withdraw_amount} coins.")
        except Exception as e:
            await ctx.send(
                "⚠️ Something went wrong while processing your withdrawal. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )
            print(f"[ERROR] withdraw command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @commands.hybrid_command(name="give", description="Give coins to another user.", aliases=["pay"])
    @app_commands.describe(
        member_name="The user to give coins to (name or mention)", amount="Amount to give (number or 'all')"
    )
    @perms.blacklist_barrier()
    @xp.xp_earn(6, 12)
    async def give(self, ctx, member_name: str, amount: str):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        if member_name.lower() == "patosx":
            return await ctx.send("🦆 I don't need coins, but thanks for the thought! Quack!")
        member = None
        if member_name.startswith("<@") and member_name.endswith(">"):
            try:
                member_id = int(member_name.strip("<@!>"))
                member = ctx.guild.get_member(member_id)
            except (ValueError, AttributeError):
                pass
        if member is None:
            member = discord.utils.get(ctx.guild.members, name=member_name)
        if member is None:
            member = discord.utils.get(ctx.guild.members, display_name=member_name)
        if member is None:
            return await ctx.send(f"❌ Could not find user '{member_name}'. Make sure they're in this server.")
        if member == ctx.author:
            return await ctx.send("❌ You cannot give coins to yourself.")
        sender = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
        if amount.lower() == "all":
            amount = sender["wallet"]
            if amount <= 0:
                return await ctx.send("❌ You don't have any coins to give.")
        else:
            try:
                amount = int(amount)
            except ValueError:
                return await ctx.send("❌ Invalid amount. Use a number or 'all'.")
            if amount <= 0:
                return await ctx.send("❌ Amount must be greater than 0.")
        if sender["wallet"] < amount:
            return await ctx.send("❌ You don't have enough coins.")
        await econ.subtract_balance(ctx.author.id, ctx.guild.id, amount)
        await econ.add_balance(member.id, ctx.guild.id, amount)
        if amount == sender["wallet"] and amount > 0:
            await ctx.send(f"🤝 You gave all **{amount}** coins to {member.mention}!")
        else:
            await ctx.send(f"🤝 You gave **{amount}** coins to {member.mention}!")

    @commands.hybrid_command(name="leaderboard", description="View the top users.", aliases=["lb"])
    @perms.blacklist_barrier()
    @xp.xp_earn(3, 7)
    async def leaderboard(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        # Pull a bounded top N from Mongo (default 1000) and paginate it in discord
        view = LeaderboardView(ctx, page_size=10, max_entries=1000)
        embed = await view.render()
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="badges", description="View your (or someone else's) earned badges.")
    @app_commands.describe(member="User to view badges for (optional)")
    @perms.blacklist_barrier()
    async def badges(self, ctx, member: discord.Member = None):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        target = member or ctx.author
        guild_id = str(ctx.guild.id)
        user_id = str(target.id)
        key = f"{guild_id}-{user_id}"
        badge_doc = await state.badges_col.find_one({"_id": key}) or {}
        earned_ids = set(badge_doc.get("earned", []))
        embed = discord.Embed(title=f"🏅 {target.display_name}'s Badges", color=discord.Color.gold())
        earned_lines = []
        locked_lines = []
        for badge_id, badge in xp.BADGES.items():
            line = f"{badge['emoji']} **{badge['name']}** - {badge['description']}"
            if badge_id in earned_ids:
                earned_lines.append(f"✅ {line}")
            else:
                locked_lines.append(f"🔒 {line}")
        if earned_lines:
            embed.add_field(
                name=f"Earned ({len(earned_lines)}/{len(xp.BADGES)})", value="\n".join(earned_lines), inline=False
            )
        else:
            embed.add_field(name="Earned (0)", value="No badges earned yet. Get out there and play!", inline=False)
        if locked_lines:
            embed.add_field(name="Locked", value="\n".join(locked_lines[:15]), inline=False)
        await ctx.send(embed=embed)

    @commands.command()
    @perms.staffperm("economy")
    @perms.staff_only()
    @xp.xp_earn(3, 6)
    async def reseteconomy(self, ctx):
        """Wipe all economy records for the guild. Requires economy staff permission. Irreversible."""
        await ctx.defer()
        try:
            result = await state.economy_col.delete_many({"_id": {"$regex": f"^{ctx.guild.id}-"}})
            await state.settings_col.update_one(
                {"guild": str(ctx.guild.id)}, {"$set": {"season_reset_time": datetime.now(UTC)}}, upsert=True
            )
            await ctx.send(
                f"🧹 Economy has been reset for this server!\nDeleted **{result.deleted_count}** player records."
            )
            print(f"[RESET ECONOMY] {ctx.guild.name} ({ctx.guild.id}) - Deleted {result.deleted_count} entries.")
        except Exception as e:
            await ctx.send("⚠️ Something went wrong while resetting the economy.")
            print(f"[ERROR] reseteconomy command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @commands.hybrid_command(
        name="monthlyrewards",
        description="Check your monthly reward goal progress.",
        aliases=["monthlygoals"],
    )
    @perms.blacklist_barrier()
    async def monthlyrewards(self, ctx):
        """Show the invoker's progress on every monthly reward goal, along with how many coins
        they've already claimed this month. No xp.xp_earn decorator - checking progress isn't itself
        an activity that should earn XP."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            doc = await econ.get_monthly_rewards_doc(ctx.guild.id, ctx.author.id)
            counters = doc.get("counters", {})
            claimed = doc.get("claimed", {})
            month_label = datetime.now(timezone.utc).strftime("%B %Y")
            embed = discord.Embed(
                title=f"🗓️ Monthly Rewards - {month_label}",
                description="Complete goals before the month ends to earn bonus coins. Progress resets on the 1st.",
                color=discord.Color.gold(),
            )
            total_claimed_coins = 0
            for goal in cfg.MONTHLY_REWARD_GOALS:
                key = goal["key"]
                target = goal["target"]
                progress = min(counters.get(key, 0), target)
                if claimed.get(key):
                    total_claimed_coins += goal["reward"]
                    status = f"✅ Claimed - **{goal['reward']} coins**"
                else:
                    filled = int((progress / target) * 10) if target else 0
                    bar = "🟩" * filled + "⬜" * (10 - filled)
                    status = f"{bar} {progress}/{target} - **{goal['reward']} coins**"
                embed.add_field(
                    name=f"{goal['emoji']} {goal['title']}", value=f"{goal['description']}\n{status}", inline=False
                )
            embed.set_footer(text=f"Earned this month: {total_claimed_coins} coins")
            await ctx.send(embed=embed)
        except Exception as e:
            print(f"[ERROR] monthlyrewards command: {type(e).__name__} - {e}")
            traceback.print_exc()
            await ctx.send(
                "⚠️ Something went wrong while checking your monthly rewards. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(name="passive", description="Toggle passive mode. Staff can manage others.")
    @perms.blacklist_barrier()
    @xp.xp_earn(4, 8)
    async def passive(self, ctx, member: discord.Member = None):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        if member and member != ctx.author:
            if not await perms.staff_only().predicate(ctx):
                return await ctx.send("❌ You don't have permission to toggle passive mode for others.")
            target = member
        else:
            target = ctx.author
        user_id = f"{ctx.guild.id}-{target.id}"
        now = datetime.now(timezone.utc)
        user_data = await state.economy_col.find_one({"_id": user_id}) or {}
        passive_until = user_data.get("passive_until")
        last_toggle = user_data.get("last_passive_toggle")
        if last_toggle:
            last_toggle_dt = datetime.fromisoformat(last_toggle)
            if last_toggle_dt.tzinfo is None:
                last_toggle_dt = last_toggle_dt.replace(tzinfo=timezone.utc)
            time_since = (now - last_toggle_dt).total_seconds()
            if time_since < 180:
                remaining = int(180 - time_since)
                return await ctx.send(f"⏳ You must wait **{remaining} seconds** before toggling passive mode again.")
        if passive_until:
            dt = datetime.fromisoformat(passive_until)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > now:
                await state.economy_col.update_one(
                    {"_id": user_id},
                    {"$unset": {"passive_until": ""}, "$set": {"last_passive_toggle": now.isoformat()}},
                )
                if target == ctx.author:
                    return await ctx.send("🛡️ Passive mode disabled. You can now attack and be attacked.")
                else:
                    return await ctx.send(f"🛡️ Disabled passive mode for {target.display_name}.")
        until_time = now + timedelta(hours=24)
        await state.economy_col.update_one(
            {"_id": user_id},
            {"$set": {"passive_until": until_time.isoformat(), "last_passive_toggle": now.isoformat()}},
            upsert=True,
        )
        if target == ctx.author:
            await ctx.send("🛡️ Passive mode enabled for 24 hours - you can't attack or be attacked.")
        else:
            await ctx.send(f"🛡️ Enabled passive mode for {target.display_name} for 24 hours.")

    @commands.hybrid_command(name="sell", description="Sell items, investments, or everything at once.")
    @app_commands.describe(
        item="What to sell: item name (e.g., 'rabbit', 'fish'), 'all' to sell everything, or 'inv' to sell inventory"
    )
    @perms.blacklist_barrier()
    @xp.xp_earn(9, 18)
    async def sell(self, ctx, *, item: str | None = None):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            inventory = data.get("inventory", [])
            wallet = data.get("wallet", 0)
            if not item:
                return await ctx.send("❌ Please specify what to sell (example: `sell rabbit 2` or `sell all`).")
            item_parts = item.lower().strip().split()
            amount = 1
            if item_parts[-1].isdigit():
                amount = int(item_parts[-1])
                item_name = " ".join(item_parts[:-1])
            else:
                item_name = " ".join(item_parts)
            total_gain = 0
            sold_items = []
            prices = {
                "rabbit": 200,
                "deer": 450,
                "bear": 600,
                "fish": 150,
                "iron ore": 200,
                "gold ore": 500,
                "diamond": 1200,
                "amber shard": 240,
                "moonstone fragment": 650,
                "fossil core": 1000,
            }
            if item_name == "all":
                confirm_view = ConfirmSellAll(ctx, prices, inventory, user_id, wallet)
                confirm_embed = discord.Embed(
                    title="⚠️ Confirm Sell All",
                    description="You are about to sell **ALL ores, hunted animals, and investments.**\n\nThis includes:\n• Rabbits, deer, bears, fish, ores, diamonds, dig rocks\n• All company investments\n\nAre you sure you want to continue?",
                    color=discord.Color.red(),
                )
                confirm_msg = await ctx.send(embed=confirm_embed, view=confirm_view)
                await confirm_view.wait()
                if confirm_view.value is None:
                    ctx._skip_xp_award = True
                    return await confirm_msg.edit(content="⌛ Timed out. No items were sold.", embed=None, view=None)
                elif confirm_view.value is False:
                    ctx._skip_xp_award = True
                    return await confirm_msg.edit(content="❌ Cancelled. No items were sold.", embed=None, view=None)
                total_gain = 0
                sold_items = []
                for inv_item in inventory:
                    if isinstance(inv_item, dict):
                        continue
                    if inv_item in prices:
                        price = prices[inv_item]
                        total_gain += price
                        sold_items.append(f"1x {inv_item} ({price} each)")
                inventory = [i for i in inventory if not (isinstance(i, str) and i in prices)]
                investments = await state.investments_col.find({"user_id": user_id}).to_list(length=None)
                investments = await econ.refresh_user_investments_for_today(investments)
                for inv in investments:
                    current_value = await econ.calculate_investment_value(inv)
                    total_gain += current_value
                    sold_items.append(
                        f"Investment in {inv['company']} (ID: {inv['_id']}, {inv['amount']} -> {current_value})"
                    )
                await state.investments_col.delete_many({"user_id": user_id})
                if total_gain == 0:
                    ctx._skip_xp_award = True
                    return await confirm_msg.edit(content="❌ You had nothing to sell.", embed=None, view=None)
                await state.economy_col.update_one(
                    {"_id": user_id}, {"$set": {"wallet": wallet + total_gain, "inventory": inventory}}
                )
                embed = discord.Embed(
                    title="💸 Sell Summary", description=sell_summary_text(sold_items), color=discord.Color.gold()
                )
                embed.add_field(name="Total Earned", value=f"🪙 {total_gain}", inline=False)
                await confirm_msg.edit(content=None, embed=embed, view=None)
                return
            elif item_name in ["inventory", "inv"]:
                for inv_item, price in prices.items():
                    count = inventory.count(inv_item)
                    if count > 0:
                        total_gain += price * count
                        sold_items.append(f"{count}x {inv_item} ({price} each)")
                        inventory = [i for i in inventory if i != inv_item]
            elif item_name in ["investments", "all investments"]:
                investments = await state.investments_col.find({"user_id": user_id}).to_list(length=None)
                investments = await econ.refresh_user_investments_for_today(investments)
                for inv in investments:
                    current_value = await econ.calculate_investment_value(inv)
                    total_gain += current_value
                    sold_items.append(
                        f"Investment in {inv['company']} (ID: {inv['_id']}, {inv['amount']} -> {current_value})"
                    )
                await state.investments_col.delete_many({"user_id": user_id})
            else:
                investments = await state.investments_col.find({"user_id": user_id}).to_list(length=None)
                investments = await econ.refresh_user_investments_for_today(investments)
                found_investment = False
                for inv in investments:
                    if inv["company"].lower() == item_name or str(inv["_id"]) == item_name:
                        current_value = await econ.calculate_investment_value(inv)
                        total_gain += current_value
                        sold_items.append(
                            f"Investment in {inv['company']} (ID: {inv['_id']}, {inv['amount']} -> {current_value})"
                        )
                        await state.investments_col.delete_one({"_id": inv["_id"]})
                        found_investment = True
                        break
                if not found_investment:
                    normalized_target = econ.normalize_shop_item_name(item_name)
                    normalized_prices = {econ.normalize_shop_item_name(k): v for k, v in prices.items()}
                    if normalized_target not in normalized_prices:
                        return await ctx.send("❌ That item or investment cannot be sold.")
                    match_count = 0
                    for inv_item in list(inventory):
                        if econ.normalize_item_key(inv_item) == normalized_target:
                            match_count += 1
                    if match_count < amount:
                        return await ctx.send(f"❌ You don't have {amount}x `{item_name}` in your inventory.")
                    to_remove = amount
                    new_inventory = []
                    for inv_item in inventory:
                        if to_remove > 0 and econ.normalize_item_key(inv_item) == normalized_target:
                            to_remove -= 1
                            continue
                        new_inventory.append(inv_item)
                    inventory = new_inventory
                    price_each = normalized_prices[normalized_target]
                    gain = price_each * amount
                    total_gain += gain
                    sold_items.append(f"{amount}x {item_name} ({price_each} each)")
            if total_gain == 0:
                return await ctx.send("❌ You have nothing to sell.")
            await state.economy_col.update_one(
                {"_id": user_id}, {"$set": {"wallet": wallet + total_gain, "inventory": inventory}}
            )
            embed = discord.Embed(
                title="💸 Sell Summary", description=sell_summary_text(sold_items), color=discord.Color.gold()
            )
            embed.add_field(name="Total Earned", value=f"🪙 {total_gain}", inline=False)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send("⚠️ Something went wrong while selling.")
            print(f"[ERROR] sell command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @commands.hybrid_command(name="invest", description="Invest in fake companies for profit.")
    @app_commands.describe(
        company="Company to invest in (e.g., 'Techify', 'MineCorp', 'Oceanic')",
        amount="Amount to invest (number or 'all')",
    )
    @perms.blacklist_barrier()
    @xp.xp_earn(16, 30)
    async def invest(self, ctx, company: str | None = None, amount: str | None = None):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        companies = {
            "Techify": {"min": 500, "max": 5000},
            "MineCorp": {"min": 300, "max": 3000},
            "Oceanic": {"min": 200, "max": 2500},
        }
        if company and amount:
            company = company.title()
            if company not in companies:
                return await ctx.send(f"❌ Invalid company! Available: {', '.join(companies.keys())}")
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data.get("wallet", 0)
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            user_investments = await state.investments_col.count_documents({"user_id": user_id})
            if user_investments >= 5:
                return await ctx.send(
                    "❌ You can only have up to **5 active investments** at a time. Sell some before investing again."
                )
            if amount.lower() == "all":
                invest_amount = wallet
            else:
                try:
                    invest_amount = int(amount)
                except ValueError:
                    return await ctx.send("❌ Invalid amount! Use a number or 'all'.")
            stats = companies[company]
            if invest_amount < stats["min"]:
                return await ctx.send(f"❌ Minimum investment for {company} is {stats['min']} coins!")
            if invest_amount > stats["max"]:
                return await ctx.send(f"❌ Maximum investment for {company} is {stats['max']} coins!")
            if invest_amount > wallet:
                return await ctx.send("❌ You don't have enough coins!")
            await econ.create_investment(user_id, company, invest_amount)
            await econ.subtract_balance(ctx.author.id, ctx.guild.id, invest_amount)
            await ctx.send(f"📈 Invested {invest_amount} coins in {company}!")
            return
        embed = discord.Embed(
            title="📈 Investment Opportunities",
            description="Choose a company to invest in!",
            color=discord.Color.green(),
        )
        for name, stats in companies.items():
            embed.add_field(name=name, value=f"Investment Range: {stats['min']} - {stats['max']} coins", inline=False)
        embed.set_footer(text="Unofficial Analyst Ranking: Techify ⭐⭐⭐ > MineCorp ⭐⭐ > Oceanic ⭐")
        view = View()
        for company, stats in companies.items():

            async def button_callback(interaction, company=company, stats=stats):
                if interaction.user != ctx.author:
                    return await interaction.response.send_message("❌ Not your investment.", ephemeral=True)
                user_id = f"{ctx.guild.id}-{ctx.author.id}"
                data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
                wallet = int(data.get("wallet", 0) or 0)
                user_investments = await state.investments_col.count_documents({"user_id": user_id})
                if user_investments >= 5:
                    return await interaction.response.send_message(
                        "❌ You can only have up to **5 active investments** at a time. Sell some before investing again.",
                        ephemeral=True,
                    )
                if wallet < stats["min"]:
                    return await interaction.response.send_message(
                        f"❌ You need at least {stats['min']} coins to invest in {company}.", ephemeral=True
                    )
                step = 500
                max_affordable = min(stats["max"], wallet)
                amounts = [x for x in range(stats["min"], max_affordable + step, step)]
                options = [discord.SelectOption(label=f"{amt} coins", value=str(amt)) for amt in amounts]
                select = Select(placeholder=f"Choose amount to invest in {company}", options=options)

                async def select_callback(inter: discord.Interaction):
                    if inter.user != ctx.author:
                        return await inter.response.send_message("❌ Not your selection.", ephemeral=True)
                    invest_amount = int(select.values[0])
                    latest_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
                    latest_wallet = int(latest_data.get("wallet", 0) or 0)
                    if latest_wallet < invest_amount:
                        return await inter.response.send_message(
                            f"❌ You only have {latest_wallet} coins but tried to invest {invest_amount}.",
                            ephemeral=True,
                        )
                    update_result = await state.economy_col.update_one(
                        {"_id": user_id, "wallet": {"$gte": invest_amount}},
                        {"$inc": {"wallet": -invest_amount}},
                        upsert=False,
                    )
                    if getattr(update_result, "modified_count", 0) == 0:
                        fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
                        fresh_wallet = int(fresh_data.get("wallet", 0) or 0)
                        return await inter.response.send_message(
                            f"❌ You only have {fresh_wallet} coins but tried to invest {invest_amount}.",
                            ephemeral=True,
                        )
                    await econ.create_investment(user_id, company, invest_amount)
                    await inter.response.send_message(f"✅ You invested **{invest_amount} coins** in **{company}**.")

                select.callback = select_callback
                await interaction.response.send_message(
                    f"💰 Choose how much to invest in **{company}**:", view=View().add_item(select), ephemeral=True
                )

            view.add_item(Button(label=company, style=discord.ButtonStyle.green, custom_id=f"invest_{company}"))
            view.children[-1].callback = button_callback
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="investstatus", description="Check your investments.")
    @perms.blacklist_barrier()
    @xp.xp_earn(4, 8)
    async def investstatus(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        investments = await state.investments_col.find({"user_id": user_id}).to_list(length=None)
        if not investments:
            return await ctx.send("❌ You don't have any active investments.")
        investments = await econ.refresh_user_investments_for_today(investments)
        embed = discord.Embed(title=f"📊 {ctx.author.display_name}'s Investments", color=discord.Color.blue())
        for inv in investments:
            company = inv["company"]
            amount = inv["amount"]
            current_value = await econ.calculate_investment_value(inv)
            inv_id = inv["_id"]
            date_obj = econ.get_investment_date(inv)
            unix_timestamp = int(date_obj.timestamp())
            embed.add_field(
                name=f"{company} (ID: {inv_id})",
                value=f"Invested: 🪙 {amount}\nCurrent Value: 🪙 {current_value}\nDate: <t:{unix_timestamp}:F>",
                inline=False,
            )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
