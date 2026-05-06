"""Economy commands: balance, daily, beg, deposit/withdraw, work, sell, etc."""

from __future__ import annotations

import random
import re
import traceback
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dateutil import parser as date_parser
from pytz import UTC

from bot.database import economy_col, investments_col, settings_col
from bot.utils.channels import check_channel_setting as check_channel
from bot.utils.checks import blacklist_barrier, staff_only, xp_earn
from bot.utils.economy import (
    add_balance,
    get_earnings_multiplier,
    get_user,
    get_work_cooldown_reduction,
    subtract_balance,
)
from bot.utils.errors import send_hybrid_error
from bot.utils.investments import calculate_investment_value
from bot.views.confirmations import ConfirmSellAll
from bot.views.jobs import JobPicker
from bot.views.leaderboard import LeaderboardView


SELL_PRICES: dict[str, int] = {
    "rabbit": 200,
    "deer": 450,
    "bear": 600,
    "fish": 150,
    "iron ore": 200,
    "gold ore": 500,
    "diamond": 1200,
}


class EconomyCog(commands.Cog, name="Economy"):
    """User-facing economy commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Balance, daily, beg
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="balance",
        description="Check your balance.",
        aliases=["bal"],
    )
    @app_commands.describe(
        member_name="The member whose balance to check (optional - shows your balance if not provided)"
    )
    @blacklist_barrier()
    @xp_earn(4, 8)
    async def balance(
        self, ctx: commands.Context, member_name: str | None = None
    ) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
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
                    mention_match = re.match(r"<@!?(\d+)>", member_name)
                    if mention_match:
                        try:
                            member = await ctx.guild.fetch_member(
                                int(mention_match.group(1))
                            )
                        except (discord.NotFound, discord.HTTPException):
                            pass
                if not member:
                    search_term = member_name.lower()
                    matches = [
                        m
                        for m in ctx.guild.members
                        if m.display_name.lower().startswith(search_term)
                        or m.name.lower().startswith(search_term)
                    ]
                    if not matches:
                        await ctx.send(
                            f"⚠️ No members found matching `{member_name}`."
                        )
                        return
                    if len(matches) > 1:
                        names = ", ".join([m.display_name for m in matches[:10]])
                        await ctx.send(
                            f"⚠️ Multiple members found: {names}\nPlease be more specific."
                        )
                        return
                    member = matches[0]

            data = await get_user(ctx, ctx.guild.id, member.id)
            wallet = data.get("wallet", 0)
            bank = data.get("bank", 0)
            wallet_display = (
                f"🪙 {wallet}" if wallet >= 0 else f"🪙 -{abs(wallet)} ❌ (debt)"
            )
            bank_display = f"🏦 {bank}"

            embed = discord.Embed(
                title=f"{member.display_name}'s Balance",
                color=discord.Color.gold(),
            )
            embed.add_field(name="Wallet", value=wallet_display, inline=True)
            embed.add_field(name="Bank", value=bank_display, inline=True)

            user_id = f"{ctx.guild.id}-{member.id}"
            user_data = await economy_col.find_one({"_id": user_id}) or {}
            passive_until = user_data.get("passive_until")

            if passive_until:
                dt = datetime.fromisoformat(passive_until)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if dt > now:
                    rem = dt - now
                    hours = rem.seconds // 3600
                    mins = (rem.seconds % 3600) // 60
                    embed.add_field(
                        name="🛡️ Passive Mode",
                        value=f"Active for {rem.days}d {hours}h {mins}m",
                        inline=False,
                    )

            await ctx.send(embed=embed)
        except Exception as exc:
            await ctx.send(
                "⚠️ Something went wrong while fetching balance. Contact thetruck."
            )
            print(f"[ERROR] balance command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @commands.hybrid_command(
        name="daily",
        description="Claim your daily reward.",
        aliases=["collect"],
    )
    @blacklist_barrier()
    @xp_earn(10, 20)
    async def daily(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            now = datetime.now(timezone.utc)

            last_daily = data.get("last_daily")
            current_streak = 0
            if last_daily:
                try:
                    last_time = datetime.fromisoformat(last_daily)
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=timezone.utc)

                    time_since_last = now - last_time
                    if time_since_last < timedelta(hours=24):
                        remaining = timedelta(hours=24) - time_since_last
                        hours = remaining.seconds // 3600
                        minutes = (remaining.seconds // 60) % 60
                        return await ctx.send(
                            f"🕒 Claim again in {hours}h {minutes}m"
                        )
                    current_streak = data.get("daily_streak", 0) + 1
                except Exception as exc:
                    print(f"[DAILY] Failed to parse timestamp: {exc}")

            current_streak = min(current_streak, 30)
            reward = 300 if current_streak == 0 else 50 * current_streak

            await add_balance(ctx.author.id, ctx.guild.id, reward)

            saved_streak = current_streak + 1 if current_streak == 0 else current_streak
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {
                    "$set": {
                        "last_daily": now.isoformat(),
                        "daily_streak": saved_streak,
                    }
                },
            )

            embed = discord.Embed(
                title="🎁 Daily Reward Claimed!",
                description=f"💰 You earned **{reward} coins**!",
                color=discord.Color.gold(),
                timestamp=now,
            )
            embed.add_field(
                name="🔥 Current Streak",
                value=(
                    f"Day **{current_streak}** of 30"
                    if current_streak > 0
                    else "First time claiming!"
                ),
                inline=True,
            )

            next_streak = current_streak + 1 if current_streak > 0 else 1
            embed.add_field(
                name="📈 Next Reward",
                value=f"**{50 * min(next_streak, 30)}** coins",
                inline=True,
            )

            progress = current_streak / 30
            progress_bar = "🟦" * int(progress * 10) + "⬜" * (
                10 - int(progress * 10)
            )
            embed.add_field(
                name="📊 Monthly Progress",
                value=f"{progress_bar} ({current_streak}/30 days)",
                inline=False,
            )

            footers = {
                0: "🌟 Welcome bonus! Keep claiming daily for bigger rewards!",
                1: "🔥 Streak started! Keep claiming daily for bigger rewards!",
                7: "🔥 Week streak! You're on fire!",
                14: "💎 Two weeks! Amazing consistency!",
                30: "👑 Perfect month! Maximum reward achieved!",
            }
            footer = footers.get(
                current_streak,
                f"💪 Keep it up! {(30 - current_streak)} days to next reward!",
            )
            embed.set_footer(text=footer)
            await ctx.send(embed=embed)

        except Exception as exc:
            print(f"[ERROR] daily command: {type(exc).__name__} - {exc}")
            traceback.print_exc()
            await ctx.send(
                "⚠️ Something went wrong while collecting your daily. Contact thetruck."
            )

    @commands.hybrid_command(name="beg", description="Beg for coins.")
    @blacklist_barrier()
    @xp_earn(8, 16)
    async def beg(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            now = datetime.now(timezone.utc)

            last_beg = data.get("last_beg")
            if last_beg:
                try:
                    last_time = datetime.fromisoformat(last_beg)
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=timezone.utc)
                    if now - last_time < timedelta(minutes=15):
                        remaining = timedelta(minutes=15) - (now - last_time)
                        return await ctx.send(
                            f"🕒 You can beg again in {remaining.seconds // 60} minutes."
                        )
                except Exception as exc:
                    print(f"[BEG] Failed to parse timestamp: {exc}")

            amount = random.randint(50, 200)
            earnings_multiplier = await get_earnings_multiplier(
                ctx.author.id, ctx.guild.id
            )

            inventory = data.get("inventory", [])
            duck_used = False
            for item in inventory:
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    earnings_multiplier *= 1.3
                    item["uses_left"] -= 1
                    await ctx.send(
                        "🦆 Your Pet Duck boosted your begging earnings by 30%!"
                    )
                    duck_used = True
                    break

            amount = int(amount * earnings_multiplier)

            if duck_used:
                await economy_col.update_one(
                    {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                    {"$set": {"inventory": inventory}},
                    upsert=True,
                )

            donor = random.choice(["thetruck", "CuteBatak"])
            await add_balance(ctx.author.id, ctx.guild.id, amount)
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"last_beg": now.isoformat(timespec="seconds")}},
            )

            msg = (
                f"🙇 {donor} was kind enough to donate **{amount} coins** to you!"
            )
            if earnings_multiplier > 1.0:
                msg += "\n🍪 **Lucky Cookie consumed!** Earnings doubled!"
            await ctx.send(msg)

        except Exception as exc:
            print(f"[ERROR] beg command: {type(exc).__name__} - {exc}")
            traceback.print_exc()
            await ctx.send(
                "⚠️ Something went wrong while begging. Contact thetruck."
            )

    # ------------------------------------------------------------------
    # Bank commands
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="deposit",
        description="Deposit to bank.",
        aliases=["dep"],
    )
    @app_commands.describe(amount="Amount to deposit (supports k, m, b suffixes or 'all')")
    @blacklist_barrier()
    @xp_earn(5, 10)
    async def deposit(self, ctx: commands.Context, amount: str) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data["wallet"]

            if amount.lower() == "all":
                if wallet <= 0:
                    return await ctx.send("❌ You have no coins to deposit.")
                deposit_amount = wallet
            elif amount.isdigit():
                deposit_amount = int(amount)
                if deposit_amount <= 0:
                    return await ctx.send("❌ Invalid deposit amount.")
                if deposit_amount > wallet:
                    return await ctx.send("❌ You can't afford that!")
            else:
                return await ctx.send("❌ Please enter a valid number or `all`.")

            taxed_amount = int(deposit_amount * 0.95)
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {
                    "$set": {
                        "wallet": wallet - deposit_amount,
                        "bank": data["bank"] + taxed_amount,
                    }
                },
            )
            await ctx.send(
                f"🏦 You deposited {deposit_amount} coins.\n"
                f"💸 After 5% tax, you received {taxed_amount} coins in your bank."
            )
        except Exception as exc:
            await ctx.send(
                "⚠️ Something went wrong while processing your deposit. Contact thetruck."
            )
            print(f"[ERROR] deposit command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @commands.hybrid_command(
        name="withdraw",
        description="Withdraw from bank.",
        aliases=["with"],
    )
    @app_commands.describe(amount="Amount to withdraw (supports k, m, b suffixes or 'all')")
    @blacklist_barrier()
    @xp_earn(5, 10)
    async def withdraw(self, ctx: commands.Context, amount: str) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            bank = data["bank"]

            if amount.lower() == "all":
                if bank <= 0:
                    return await ctx.send("❌ You have no coins to withdraw.")
                withdraw_amount = bank
            elif amount.isdigit():
                withdraw_amount = int(amount)
                if withdraw_amount <= 0:
                    return await ctx.send("❌ Invalid withdrawal amount.")
                if withdraw_amount > bank:
                    return await ctx.send("❌ You can't afford that")
            else:
                return await ctx.send("❌ Please enter a valid number or `all`.")

            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {
                    "$set": {
                        "wallet": data["wallet"] + withdraw_amount,
                        "bank": bank - withdraw_amount,
                    }
                },
            )
            await ctx.send(f"💰 You withdrew {withdraw_amount} coins.")
        except Exception as exc:
            await ctx.send(
                "⚠️ Something went wrong while processing your withdrawal. Contact thetruck."
            )
            print(f"[ERROR] withdraw command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Give / Leaderboard
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="give",
        description="Give coins to another user.",
        aliases=["pay"],
    )
    @app_commands.describe(
        member_name="The user to give coins to (name or mention)",
        amount="Amount to give (number or 'all')",
    )
    @blacklist_barrier()
    @xp_earn(6, 12)
    async def give(
        self, ctx: commands.Context, member_name: str, amount: str
    ) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        if member_name.lower() == "duckparadise":
            return await ctx.send(
                "🦆 I don't need coins, but thanks for the thought! Quack!"
            )

        member: discord.Member | None = None
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
            return await ctx.send(
                f"❌ Could not find user '{member_name}'. Make sure they're in this server."
            )
        if member == ctx.author:
            return await ctx.send("❌ You cannot give coins to yourself.")

        sender = await get_user(ctx, ctx.guild.id, ctx.author.id)
        await get_user(ctx, ctx.guild.id, member.id)

        if amount.lower() == "all":
            transfer_amount = sender["wallet"]
            if transfer_amount <= 0:
                return await ctx.send("❌ You don't have any coins to give.")
        else:
            try:
                transfer_amount = int(amount)
            except ValueError:
                return await ctx.send("❌ Invalid amount. Use a number or 'all'.")
            if transfer_amount <= 0:
                return await ctx.send("❌ Amount must be greater than 0.")

        if sender["wallet"] < transfer_amount:
            return await ctx.send("❌ You don't have enough coins.")

        await subtract_balance(ctx.author.id, ctx.guild.id, transfer_amount)
        await add_balance(member.id, ctx.guild.id, transfer_amount)

        if transfer_amount == sender["wallet"] and transfer_amount > 0:
            await ctx.send(
                f"🤝 You gave all **{transfer_amount}** coins to {member.mention}!"
            )
        else:
            await ctx.send(
                f"🤝 You gave **{transfer_amount}** coins to {member.mention}!"
            )

    @commands.hybrid_command(
        name="leaderboard",
        description="View the top users.",
        aliases=["lb"],
    )
    @blacklist_barrier()
    @xp_earn(3, 7)
    async def leaderboard(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        view = LeaderboardView(ctx)
        embed = await view.get_money_embed()
        await ctx.send(embed=embed, view=view)

    # ------------------------------------------------------------------
    # Job and work commands
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="choosejob", description="Choose your dream job"
    )
    @blacklist_barrier()
    @xp_earn(5, 10)
    async def choosejob(self, ctx: commands.Context) -> None:
        view = JobPicker(ctx)
        await ctx.send(
            "💼 Choose your job by clicking one of the buttons below:",
            view=view,
        )

    @commands.hybrid_command(name="work", description="Work to earn coins.")
    @blacklist_barrier()
    @xp_earn(20, 35)
    async def work(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            job = data.get("job")
            if not job:
                doc = await settings_col.find_one({"guild": str(ctx.guild.id)})
                prefix = doc.get("prefix", "?") if doc else "?"
                return await ctx.send(
                    f"❌ You don't have a job yet! Use `{prefix}choosejob` to get one."
                )

            inventory = data.get("inventory", [])
            has_laptop = any(
                (isinstance(item, str) and item == "laptop")
                or (isinstance(item, dict) and item.get("_id") == "laptop")
                for item in inventory
            )

            if job == "developer" and not has_laptop:
                return await ctx.send(
                    "💻 You need a **laptop** to work as a developer!"
                )
            if job not in ["developer", "duck"]:
                return await ctx.send(
                    "⚠️ You have an invalid job. Please use `?choosejob` to pick a valid one."
                )

            cooldown_key = f"work_cooldown_{ctx.guild.id}-{ctx.author.id}"
            cooldown_data = await economy_col.find_one({"_id": cooldown_key})
            if cooldown_data:
                last_work = cooldown_data.get("timestamp")
                if last_work:
                    time_since = datetime.now(timezone.utc) - date_parser.isoparse(
                        last_work
                    )
                    cooldown_duration = 43200
                    cooldown_reduction = await get_work_cooldown_reduction(
                        ctx.author.id, ctx.guild.id
                    )
                    if cooldown_reduction < 1.0:
                        cooldown_duration = int(cooldown_duration * cooldown_reduction)
                    if time_since.total_seconds() < cooldown_duration:
                        remaining = int(cooldown_duration - time_since.total_seconds())
                        hours, remainder = divmod(remaining, 3600)
                        minutes, _ = divmod(remainder, 60)
                        if hours > 0:
                            return await ctx.send(
                                f"⏰ You're on cooldown! Try again in {hours}h {minutes}m."
                            )
                        return await ctx.send(
                            f"⏰ You're on cooldown! Try again in "
                            f"{minutes}m {int((remainder % 60))}s."
                        )

            promo_level = data.get("promotion_level", 0)
            cooldown_reduction = await get_work_cooldown_reduction(
                ctx.author.id, ctx.guild.id
            )
            earnings_multiplier = await get_earnings_multiplier(
                ctx.author.id, ctx.guild.id
            )

            inventory = data.get("inventory", [])
            duck_used = False
            for item in inventory:
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    earnings_multiplier *= 1.3
                    item["uses_left"] -= 1
                    await ctx.send(
                        "🦆 Your Pet Duck boosted your work earnings by 30%!"
                    )
                    duck_used = True
                    break
            if duck_used:
                await economy_col.update_one(
                    {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                    {"$set": {"inventory": inventory}},
                    upsert=True,
                )

            if cooldown_reduction < 1.0:
                ctx.command.reset_cooldown(ctx)
                await ctx.send(
                    "⚡ **Energy Drink consumed!** Work cooldown reduced by 50%!"
                )

            base_payouts = {
                "developer": (300, 600),
                "duck": (200, 500),
            }
            descriptions = {
                "developer": "You wrote some killer code 💻",
                "duck": "You danced and quacked around the duck pond 🦆",
            }

            low, high = base_payouts[job]
            multiplier = 1 + (0.2 * promo_level)
            multiplier *= earnings_multiplier
            low = int(low * multiplier)
            high = int(high * multiplier)

            earned = random.randint(low, high)
            await add_balance(ctx.author.id, ctx.guild.id, earned)
            await economy_col.update_one(
                {"_id": cooldown_key},
                {"$set": {"timestamp": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )

            msg = (
                f"🧾 {descriptions.get(job, 'You worked hard!')}\n"
                f"💰 You earned **{earned} coins** as a level `{promo_level}` {job}!"
            )
            if earnings_multiplier > 1.0:
                msg += "\n🍪 **Lucky Cookie consumed!** Earnings doubled!"
            await ctx.send(msg)

        except Exception as exc:
            await ctx.send(
                "⚠️ Something went wrong while processing your work. Contact thetruck."
            )
            print(f"[ERROR] work command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @work.error
    async def work_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                return await send_hybrid_error(
                    ctx,
                    content=f"⏰ You're on cooldown! Try again in {hours}h {minutes}m {seconds}s.",
                )
            if minutes > 0:
                return await send_hybrid_error(
                    ctx,
                    content=f"⏰ You're on cooldown! Try again in {minutes}m {seconds}s.",
                )
            return await send_hybrid_error(
                ctx, content=f"⏰ You're on cooldown! Try again in {seconds}s."
            )
        if isinstance(error, commands.CommandError):
            await send_hybrid_error(
                ctx,
                content="⚠️ Something went wrong while processing your work. Contact thetruck.",
            )
            print(f"[ERROR] work command: {type(error).__name__} - {error}")

    @commands.command()
    @staff_only()
    @xp_earn(3, 6)
    async def reseteconomy(self, ctx: commands.Context) -> None:
        await ctx.defer()
        try:
            result = await economy_col.delete_many(
                {"_id": {"$regex": f"^{ctx.guild.id}-"}}
            )
            await settings_col.update_one(
                {"guild": str(ctx.guild.id)},
                {"$set": {"season_reset_time": datetime.now(UTC)}},
                upsert=True,
            )
            await ctx.send(
                f"🧹 Economy has been reset for this server!\n"
                f"Deleted **{result.deleted_count}** player records."
            )
            print(
                f"[RESET ECONOMY] {ctx.guild.name} ({ctx.guild.id}) — "
                f"Deleted {result.deleted_count} entries."
            )
        except Exception as exc:
            await ctx.send("⚠️ Something went wrong while resetting the economy.")
            print(f"[ERROR] reseteconomy command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @commands.hybrid_command(
        name="jobstatus", description="Check your next promotion."
    )
    @blacklist_barrier()
    @xp_earn(4, 8)
    async def jobstatus(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            user_data = await economy_col.find_one({"_id": user_id}) or {}

            job = user_data.get("job")
            job_start_str = user_data.get("job_start")
            promo_level = user_data.get("promotion_level", 0)
            promo_chance = user_data.get("promotion_chance", 20.0)
            last_check_str = user_data.get("last_promo_check")
            last_roll_str = user_data.get("last_promo_roll")

            if not job or not job_start_str:
                return await ctx.send(
                    "💼 You don't currently have a job. Choose one with your "
                    "server's prefix like `?choosejob`."
                )

            try:
                job_start = datetime.fromisoformat(job_start_str)
                if job_start.tzinfo is None:
                    job_start = job_start.replace(tzinfo=timezone.utc)
            except Exception as exc:
                return await ctx.send(
                    "⚠️ An unexpected error occurred, please contact thetruck: "
                    f"`{type(exc).__name__} - {exc}`"
                )

            now = datetime.now(timezone.utc)
            delta = now - job_start
            days = delta.days
            hours = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60

            promoted = False
            allow_roll = False
            last_roll = None

            if days >= 7:
                if last_check_str:
                    try:
                        last_check = datetime.fromisoformat(last_check_str).replace(
                            tzinfo=timezone.utc
                        )
                    except Exception:
                        last_check = now
                else:
                    last_check = now

                elapsed_days = (now - last_check).days
                allow_roll = True
                if last_roll_str:
                    try:
                        last_roll = datetime.fromisoformat(last_roll_str)
                        if last_roll.tzinfo is None:
                            last_roll = last_roll.replace(tzinfo=timezone.utc)
                    except Exception:
                        last_roll = None
                    if last_roll:
                        allow_roll = (now - last_roll) >= timedelta(days=1)

                if allow_roll:
                    if elapsed_days > 0:
                        promo_chance += elapsed_days * 0.5
                        if promo_chance > 100:
                            promo_chance = 100
                    if random.random() <= (promo_chance / 100):
                        promo_level += 1
                        promo_chance = 20.0
                        promoted = True

                        update_fields = {
                            "promotion_level": promo_level,
                            "promotion_chance": promo_chance,
                            "last_promo_check": now.isoformat(),
                            "last_promo_roll": now.isoformat(),
                        }
                        await economy_col.update_one(
                            {"_id": user_id}, {"$set": update_fields}, upsert=True
                        )

                        embed = discord.Embed(
                            title="🎉 Promotion Achieved!",
                            description=(
                                f"Congratulations {ctx.author.mention}, you’ve "
                                f"been **promoted** to level `{promo_level}` in "
                                f"your job as a **{job.capitalize()}**!\n\n"
                                "💰 You will now earn **even more coins** when "
                                "you work!"
                            ),
                            color=discord.Color.gold(),
                        )
                        embed.set_thumbnail(
                            url="https://media.tenor.com/I5qPz6wS1jAAAAAC/congratulations-clapping.gif"
                        )
                        await ctx.send(embed=embed)

                if not promoted:
                    embed = discord.Embed(
                        title=f"📋 Job Status for {ctx.author.display_name}",
                        color=discord.Color.blue(),
                    )
                    embed.add_field(name="Job", value=job.capitalize(), inline=False)
                    embed.add_field(name="Promotion Level", value=str(promo_level), inline=False)
                    embed.add_field(
                        name="Time on Job",
                        value=f"{days}d {hours}h {minutes}m",
                        inline=False,
                    )
                    if allow_roll:
                        embed.add_field(
                            name="Promotion Chance",
                            value=f"✅ Eligible ({promo_chance:.2f}%)",
                            inline=False,
                        )
                    else:
                        next_time = (
                            (last_roll + timedelta(days=1))
                            if last_roll_str and last_roll
                            else (now + timedelta(days=1))
                        )
                        embed.add_field(
                            name="Promotion Chance",
                            value=(
                                f"⏳ On cooldown ({promo_chance:.2f}%) — next roll "
                                f"<t:{int(next_time.timestamp())}:f>"
                            ),
                            inline=False,
                        )
                    await ctx.send(embed=embed)

            else:
                embed = discord.Embed(
                    title=f"📋 Job Status for {ctx.author.display_name}",
                    color=discord.Color.blue(),
                )
                embed.add_field(name="Job", value=job.capitalize(), inline=False)
                embed.add_field(name="Promotion Level", value=str(promo_level), inline=False)
                embed.add_field(
                    name="Time on Job",
                    value=f"{days}d {hours}h {minutes}m",
                    inline=False,
                )
                embed.add_field(
                    name="Promotion Chance",
                    value=f"❌ Not eligible yet (need {7 - days} more day(s))",
                    inline=False,
                )
                await ctx.send(embed=embed)

            if not promoted:
                update_fields = {
                    "promotion_level": promo_level,
                    "promotion_chance": promo_chance,
                }
                if days >= 7 and allow_roll:
                    update_fields["last_promo_check"] = now.isoformat()
                    update_fields["last_promo_roll"] = now.isoformat()
                await economy_col.update_one(
                    {"_id": user_id}, {"$set": update_fields}, upsert=True
                )

        except Exception as exc:
            print(f"[jobstatus command error] {type(exc).__name__}: {exc}")
            traceback.print_exc()
            await ctx.send(
                f"⚠️ An unexpected error occurred: `{type(exc).__name__} - {exc}`\n"
                "Please contact thetruck."
            )

    # ------------------------------------------------------------------
    # Rob / crime / passive / sell
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="rob",
        description="Attempt to rob another user.",
        aliases=["steal"],
    )
    @app_commands.describe(member="The user to rob (mention or name)")
    @blacklist_barrier()
    @xp_earn(14, 28)
    async def rob(self, ctx: commands.Context, member: discord.Member) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        if member == ctx.author:
            return await ctx.send("❌ You can't rob yourself!")

        now = datetime.now(timezone.utc)
        robber_id = f"{ctx.guild.id}-{ctx.author.id}"
        victim_id = f"{ctx.guild.id}-{member.id}"

        r_doc = await economy_col.find_one({"_id": robber_id}) or {}
        v_doc = await economy_col.find_one({"_id": victim_id}) or {}

        cooldown = r_doc.get("rob_cooldown")
        if cooldown:
            cooldown_dt = datetime.fromisoformat(cooldown)
            if cooldown_dt.tzinfo is None:
                cooldown_dt = cooldown_dt.replace(tzinfo=timezone.utc)
            if now < cooldown_dt:
                remaining = cooldown_dt - now
                mins = int(remaining.total_seconds() // 60)
                return await ctx.send(f"🕒 You can rob again in {mins} minute(s).")

        if r_doc.get("passive_until"):
            until = datetime.fromisoformat(r_doc["passive_until"])
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > now:
                return await ctx.send(
                    "🔒 You have passive mode enabled, disable it to rob others."
                )
        if v_doc.get("passive_until"):
            until = datetime.fromisoformat(v_doc["passive_until"])
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > now:
                return await ctx.send(
                    "🔒 That user has passive mode enabled, you can't rob them."
                )

        last_robbed = v_doc.get("last_robbed")
        if last_robbed:
            if isinstance(last_robbed, str):
                last_robbed = datetime.fromisoformat(last_robbed)
                if last_robbed.tzinfo is None:
                    last_robbed = last_robbed.replace(tzinfo=timezone.utc)
            if now - last_robbed < timedelta(hours=1):
                rem = timedelta(hours=1) - (now - last_robbed)
                minutes = round(rem.total_seconds() / 60)
                return await ctx.send(
                    f"🛡️ {member.display_name} is under protection. Try again "
                    f"in {minutes} minutes."
                )

        if r_doc.get("wallet", 0) < 500:
            return await ctx.send("❌ You need at least 500 coins to rob.")
        if v_doc.get("wallet", 0) < 300:
            return await ctx.send("❌ They don’t have enough coins to rob.")

        amount = random.randint(
            100, min(500, v_doc["wallet"], r_doc["wallet"])
        )
        await add_balance(ctx.author.id, ctx.guild.id, amount)
        await subtract_balance(member.id, ctx.guild.id, amount)
        await economy_col.update_one(
            {"_id": robber_id},
            {"$set": {"rob_cooldown": (now + timedelta(hours=3)).isoformat()}},
        )
        await economy_col.update_one(
            {"_id": victim_id}, {"$set": {"last_robbed": now.isoformat()}}
        )
        await ctx.send(
            f"💰 You robbed {member.display_name} and stole {amount} coins!"
        )

    @rob.error
    async def rob_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await send_hybrid_error(
                ctx,
                content="❌ You must mention someone to rob. Example: `.rob @User`",
            )
        elif isinstance(error, commands.BadArgument):
            await send_hybrid_error(ctx, content="❌ That’s not a valid user.")
        else:
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ An unexpected error occurred: "
                    f"`{type(error).__name__} - {error}`"
                ),
            )

    @commands.hybrid_command(
        name="crime",
        description="Attempt a risky crime to earn coins.",
    )
    @blacklist_barrier()
    @xp_earn(14, 28)
    async def crime(self, ctx: commands.Context, *, choice: str) -> None:
        from bot.utils.economy import get_crime_bonus

        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data.get("wallet", 0)
            inventory = data.get("inventory", [])
            now = datetime.now(timezone.utc)

            last_crime = data.get("last_crime")
            if last_crime:
                last_dt = datetime.fromisoformat(last_crime)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if now - last_dt < timedelta(days=1):
                    remaining = timedelta(days=1) - (now - last_dt)
                    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                    minutes = remainder // 60
                    return await ctx.send(
                        f"🕒 You can commit a crime again in {hours}h {minutes}m."
                    )

            choice = choice.lower().strip()
            if choice not in ("bank", "shoplift", "payroll"):
                return await ctx.send(
                    "❌ Choose a valid crime: `bank`, `shoplift`, or `payroll`."
                )

            if choice == "bank":
                if "lockpick" not in inventory:
                    return await ctx.send(
                        "🔐 You need to buy a **🗝️ Lockpick** to rob the bank!"
                    )
                inventory.remove("lockpick")

            config = {
                "bank": {"chance": 0.4, "gain": (1200, 3000), "fine": (600, 1500)},
                "shoplift": {"chance": 0.5, "gain": (300, 600), "fine": (150, 400)},
                "payroll": {"chance": 0.4, "gain": (800, 1500), "fine": (400, 800)},
            }
            conf = config[choice]

            luck_buff = 0.0
            for index, item in enumerate(inventory):
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    luck_buff = 0.3
                    item["uses_left"] -= 1
                    await ctx.send(
                        "🦆 Your Pet Duck increased your crime success chance!"
                    )
                    if item["uses_left"] <= 0:
                        inventory.pop(index)
                        await ctx.send(
                            "💔 One of your Pet Ducks has left after 3 uses."
                        )
                    break

            coffee_bonus = await get_crime_bonus(ctx.author.id, ctx.guild.id)
            if coffee_bonus > 0:
                await ctx.send(
                    "☕ **Coffee consumed!** Crime success chance increased by 25%!"
                )

            adjusted_chance = min(conf["chance"] + luck_buff + coffee_bonus, 1.0)
            success = random.random() < adjusted_chance

            if success:
                amount = random.randint(*conf["gain"])
                await add_balance(ctx.author.id, ctx.guild.id, amount)
                await economy_col.update_one(
                    {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                    {
                        "$set": {
                            "inventory": inventory,
                            "last_crime": now.isoformat(),
                        }
                    },
                )
                await ctx.send(
                    f"💥 Crime successful! You earned **{amount} coins** "
                    f"via `{choice}` crime."
                )
            else:
                fine = random.randint(*conf["fine"])
                new_wallet = max(0, wallet - fine)
                await economy_col.update_one(
                    {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                    {"$set": {"wallet": new_wallet, "inventory": inventory}},
                )
                await ctx.send(
                    f"🚓 You were caught during the `{choice}` attempt. "
                    f"Fined **{fine} coins**."
                )
        except Exception as exc:
            await ctx.send(
                "⚠️ An unexpected error occurred, please contact thetruck: "
                f"`{type(exc).__name__} - {exc}`"
            )

    @crime.error
    async def crime_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            seconds = int(error.retry_after)
            hours, remainder = divmod(seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            await send_hybrid_error(
                ctx,
                content=f"🕒 You can commit a crime again in {hours}h {minutes}m.",
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await send_hybrid_error(
                ctx,
                content="❌ You must specify a crime type. Example: `?crime bank`",
            )
        else:
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ An unexpected error occurred: "
                    f"`{type(error).__name__} - {error}`\n"
                    "Please contact thetruck."
                ),
            )

    @commands.hybrid_command(
        name="passive",
        description="Toggle passive mode. Staff can manage others.",
    )
    @blacklist_barrier()
    @xp_earn(4, 8)
    async def passive(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        if member and member != ctx.author:
            if not await staff_only().predicate(ctx):
                return await ctx.send(
                    "❌ You don’t have permission to toggle passive mode for others."
                )
            target = member
        else:
            target = ctx.author

        user_id = f"{ctx.guild.id}-{target.id}"
        now = datetime.now(timezone.utc)

        user_data = await economy_col.find_one({"_id": user_id}) or {}
        passive_until = user_data.get("passive_until")
        last_toggle = user_data.get("last_passive_toggle")

        if last_toggle:
            last_toggle_dt = datetime.fromisoformat(last_toggle)
            if last_toggle_dt.tzinfo is None:
                last_toggle_dt = last_toggle_dt.replace(tzinfo=timezone.utc)
            time_since = (now - last_toggle_dt).total_seconds()
            if time_since < 180:
                remaining = int(180 - time_since)
                return await ctx.send(
                    f"⏳ You must wait **{remaining} seconds** before toggling "
                    "passive mode again."
                )

        if passive_until:
            dt = datetime.fromisoformat(passive_until)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > now:
                await economy_col.update_one(
                    {"_id": user_id},
                    {
                        "$unset": {"passive_until": ""},
                        "$set": {"last_passive_toggle": now.isoformat()},
                    },
                )
                if target == ctx.author:
                    return await ctx.send(
                        "🛡️ Passive mode disabled. You can now rob and be robbed."
                    )
                return await ctx.send(
                    f"🛡️ Disabled passive mode for {target.display_name}."
                )

        until_time = now + timedelta(hours=24)
        await economy_col.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "passive_until": until_time.isoformat(),
                    "last_passive_toggle": now.isoformat(),
                }
            },
            upsert=True,
        )
        if target == ctx.author:
            await ctx.send(
                "🛡️ Passive mode enabled for 24 hours - you can't rob or be robbed."
            )
        else:
            await ctx.send(
                f"🛡️ Enabled passive mode for {target.display_name} for 24 hours."
            )

    @commands.hybrid_command(
        name="sell",
        description="Sell items, investments, or everything at once.",
    )
    @app_commands.describe(
        item=(
            "What to sell: item name (e.g., 'rabbit', 'fish'), 'all' to sell "
            "everything, or 'inv' to sell inventory"
        )
    )
    @blacklist_barrier()
    @xp_earn(9, 18)
    async def sell(
        self, ctx: commands.Context, *, item: str | None = None
    ) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            inventory = data.get("inventory", [])
            wallet = data.get("wallet", 0)

            if not item:
                return await ctx.send(
                    "❌ Please specify what to sell (example: `sell rabbit 2` or `sell all`)."
                )

            item_parts = item.lower().strip().split()
            amount = 1
            if item_parts[-1].isdigit():
                amount = int(item_parts[-1])
                item_name = " ".join(item_parts[:-1])
            else:
                item_name = " ".join(item_parts)

            total_gain = 0
            sold_items: list[str] = []
            prices = SELL_PRICES

            if item_name == "all":
                confirm_view = ConfirmSellAll(ctx, prices, inventory, user_id, wallet)
                confirm_embed = discord.Embed(
                    title="⚠️ Confirm Sell All",
                    description=(
                        "You are about to sell **ALL ores, hunted animals, and "
                        "investments.**\n\nThis includes:\n"
                        "• Rabbits, deer, bears, fish, ores, diamonds\n"
                        "• All company investments\n\n"
                        "Are you sure you want to continue?"
                    ),
                    color=discord.Color.red(),
                )
                confirm_msg = await ctx.send(embed=confirm_embed, view=confirm_view)
                await confirm_view.wait()

                if confirm_view.value is None:
                    return await confirm_msg.edit(
                        content="⌛ Timed out. No items were sold.",
                        embed=None,
                        view=None,
                    )
                if confirm_view.value is False:
                    return await confirm_msg.edit(
                        content="❌ Cancelled. No items were sold.",
                        embed=None,
                        view=None,
                    )

                for inv_item in inventory:
                    if isinstance(inv_item, dict):
                        continue
                    if inv_item in prices:
                        price = prices[inv_item]
                        total_gain += price
                        sold_items.append(f"1x {inv_item} ({price} each)")

                inventory = [
                    i for i in inventory if not (isinstance(i, str) and i in prices)
                ]

                investments = await investments_col.find(
                    {"user_id": user_id}
                ).to_list(length=None)
                for inv in investments:
                    current_value = await calculate_investment_value(inv)
                    total_gain += current_value
                    sold_items.append(
                        f"Investment in {inv['company']} (ID: {inv['_id']}, "
                        f"{inv['amount']} → {current_value})"
                    )
                await investments_col.delete_many({"user_id": user_id})

                if total_gain == 0:
                    return await confirm_msg.edit(
                        content="❌ You had nothing to sell.",
                        embed=None,
                        view=None,
                    )

                await economy_col.update_one(
                    {"_id": user_id},
                    {
                        "$set": {
                            "wallet": wallet + total_gain,
                            "inventory": inventory,
                        }
                    },
                )

                embed = discord.Embed(
                    title="💸 Sell Summary",
                    description="\n".join(sold_items),
                    color=discord.Color.gold(),
                )
                embed.add_field(
                    name="Total Earned",
                    value=f"🪙 {total_gain}",
                    inline=False,
                )
                await confirm_msg.edit(content=None, embed=embed, view=None)
                return

            if item_name in {"inventory", "inv"}:
                for inv_item, price in prices.items():
                    count = inventory.count(inv_item)
                    if count > 0:
                        total_gain += price * count
                        sold_items.append(
                            f"{count}x {inv_item} ({price} each)"
                        )
                        inventory = [i for i in inventory if i != inv_item]

            elif item_name in {"investments", "all investments"}:
                investments = await investments_col.find(
                    {"user_id": user_id}
                ).to_list(length=None)
                for inv in investments:
                    current_value = await calculate_investment_value(inv)
                    total_gain += current_value
                    sold_items.append(
                        f"Investment in {inv['company']} (ID: {inv['_id']}, "
                        f"{inv['amount']} → {current_value})"
                    )
                await investments_col.delete_many({"user_id": user_id})

            else:
                investments = await investments_col.find(
                    {"user_id": user_id}
                ).to_list(length=None)
                found_investment = False
                for inv in investments:
                    if (
                        inv["company"].lower() == item_name
                        or str(inv["_id"]) == item_name
                    ):
                        current_value = await calculate_investment_value(inv)
                        total_gain += current_value
                        sold_items.append(
                            f"Investment in {inv['company']} (ID: {inv['_id']}, "
                            f"{inv['amount']} → {current_value})"
                        )
                        await investments_col.delete_one({"_id": inv["_id"]})
                        found_investment = True
                        break

                if not found_investment:
                    if item_name not in prices:
                        return await ctx.send(
                            "❌ That item or investment cannot be sold."
                        )
                    if inventory.count(item_name) < amount:
                        return await ctx.send(
                            f"❌ You don’t have {amount}x `{item_name}` in your inventory."
                        )
                    for _ in range(amount):
                        inventory.remove(item_name)
                    gain = prices[item_name] * amount
                    total_gain += gain
                    sold_items.append(
                        f"{amount}x {item_name} ({prices[item_name]} each)"
                    )

            if total_gain == 0:
                return await ctx.send("❌ You have nothing to sell.")

            await economy_col.update_one(
                {"_id": user_id},
                {"$set": {"wallet": wallet + total_gain, "inventory": inventory}},
            )

            description = "\n".join(sold_items)
            if len(description) > 4096:
                description = description[:4093] + "..."

            embed = discord.Embed(
                title="💸 Sell Summary",
                description=description,
                color=discord.Color.gold(),
            )
            embed.add_field(
                name="Total Earned",
                value=f"🪙 {total_gain}",
                inline=False,
            )
            await ctx.send(embed=embed)

        except Exception as exc:
            await ctx.send("⚠️ Something went wrong while selling.")
            print(f"[ERROR] sell command: {type(exc).__name__} - {exc}")
            traceback.print_exc()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EconomyCog(bot))
