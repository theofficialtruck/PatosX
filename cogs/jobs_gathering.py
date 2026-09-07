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

"""Jobs and gathering activities: choosejob/work/jobstatus, fish/swim/hunt/mine/dig/bugcatch, crime and attack."""

import random
import traceback
from datetime import datetime, timedelta, timezone

import discord
from dateutil import parser
from discord import (
    ButtonStyle,
    app_commands,
    ui,
)
from discord.ext import commands

from core import config as cfg
from core import economyHelperFuncs as econ
from core import errors, state
from core import permsHelperFuncs as perms
from core import xpHelperFuncs as xp


class JobPicker(ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=30)
        self.ctx = ctx

    async def interaction_check(self, interaction):
        return interaction.user == self.ctx.author

    @ui.button(label="Developer 🧑\u200d💻", style=ButtonStyle.blurple)
    async def dev_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.set_job(interaction, "developer")

    @ui.button(label="Duck 🦆", style=discord.ButtonStyle.green)
    async def duck_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.set_job(interaction, "duck")

    async def set_job(self, interaction, job_name):
        await state.economy_col.update_one(
            {"_id": f"{self.ctx.guild.id}-{self.ctx.author.id}"},
            {
                "$set": {
                    "job": job_name,
                    "job_start": datetime.now(timezone.utc).isoformat(),
                    "promotion_level": 0,
                    "promotion_chance": 20.0,
                    "last_promo_check": None,
                }
            },
            upsert=True,
        )
        await interaction.response.edit_message(
            content=f"✅ You are now working as a **{job_name.capitalize()}**!", view=None
        )


_ATTACK_MESSAGES = [
    "🍞 {attacker} pelted {victim} with a barrage of breadcrumbs and waddled away with **{amount}** coins!",
    "🦆 {attacker} body-slammed {victim} with a rubber duck and snatched **{amount}** coins in the chaos!",
    "💦 {attacker} splashed {victim} with a bucket of pond water, causing **{amount}** coins to spill out!",
    "🪶 {attacker} unleashed a feather storm on {victim} and pinched **{amount}** coins while they were distracted!",
    "🌊 {attacker} chased {victim} around the pond, honking aggressively, and pickpocketed **{amount}** coins!",
    "🥖 {attacker} lured {victim} with moldy bread then grabbed **{amount}** coins when they weren't looking!",
    "🐾 {attacker} let out the loudest quack ever at {victim}, startling them into dropping **{amount}** coins!",
]


class JobsGathering(commands.Cog):
    """Jobs and gathering activities: choosejob/work/jobstatus, fish/swim/hunt/mine/dig/bugcatch, crime and attack."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="choosejob", description="Choose your dream job")
    @perms.blacklist_barrier()
    @xp.xp_earn(5, 10)
    async def choosejob(self, ctx):
        view = JobPicker(ctx)
        await ctx.send("💼 Choose your job by clicking one of the buttons below:", view=view)

    @commands.hybrid_command(name="work", description="Work to earn coins.")
    @perms.blacklist_barrier()
    @xp.xp_earn(20, 35)
    async def work(self, ctx):
        """Perform a work shift in the user's current job. Enforces a per job cooldown,
        applies food item bonuses, and calculates earnings based on job tier and promotions."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            job = data.get("job")
            if not job:
                doc = await state.settings_col.find_one({"guild": str(ctx.guild.id)})
                prefix = doc.get("prefix", "?") if doc else "?"
                return await ctx.send(f"❌ You don't have a job yet! Use `{prefix}choosejob` to get one.")
            inventory = data.get("inventory", [])
            has_laptop = any(econ.normalize_item_key(item) == "laptop" for item in inventory)
            if job == "developer" and (not has_laptop):
                return await ctx.send("💻 You need a **laptop** to work as a developer!")
            if job not in ["developer", "duck"]:
                prefix = await self.bot.get_prefix(ctx.message)
                return await ctx.send(f"⚠️ You have an invalid job. Please use `{prefix}choosejob` to pick a valid one.")
            cooldown_key = f"work_cooldown_{ctx.guild.id}-{ctx.author.id}"
            cooldown_data = await state.economy_col.find_one({"_id": cooldown_key})
            energy_drink_in_inv = any(econ.normalize_item_key(i) == "energy drink" for i in inventory)
            if cooldown_data:
                last_work = cooldown_data.get("timestamp")
                if last_work:
                    time_since = datetime.now(timezone.utc) - parser.isoparse(last_work)
                    cooldown_duration = 43200
                    if energy_drink_in_inv:
                        cooldown_duration = int(cooldown_duration * 0.5)
                    if time_since.total_seconds() < cooldown_duration:
                        remaining = int(cooldown_duration - time_since.total_seconds())
                        hours, remainder = divmod(remaining, 3600)
                        minutes, _ = divmod(remainder, 60)
                        if hours > 0:
                            return await ctx.send(f"⏰ You're on cooldown! Try again in {hours}h {minutes}m.")
                        else:
                            return await ctx.send(
                                f"⏰ You're on cooldown! Try again in {minutes}m {int(remainder % 60)}s."
                            )
            promo_level = data.get("promotion_level", 0)
            has_drink = econ.pop_food_item(inventory, "energy_drink")
            cooldown_reduction = 0.5 if has_drink else 1.0
            has_cookie = econ.pop_food_item(inventory, "lucky_cookie")
            earnings_multiplier = 2.0 if has_cookie else 1.0
            inventory_dirty = has_drink or has_cookie
            tool_break_notice = ""
            if job == "developer":
                consumed, broke, _ = econ.consume_tool_use(inventory, "laptop")
                if not consumed:
                    return await ctx.send("💻 You need a **laptop** to work as a developer!")
                inventory_dirty = True
                if broke:
                    tool_break_notice = "\n💥 Your **Laptop** broke. Buy a new one with `.buy laptop`."
            duck_used = False
            for i, item in enumerate(inventory):
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    earnings_multiplier *= 1.3
                    item["uses_left"] -= 1
                    await ctx.send("🦆 Your Pet Duck boosted your work earnings by 30%!")
                    if item["uses_left"] <= 0:
                        inventory.pop(i)
                        await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
                    duck_used = True
                    break
            nitro_used, nitro_expired = econ.consume_nitro_boost(inventory)
            nitro_reduction_seconds = 0
            if nitro_used:
                nitro_reduction_seconds = int(43200 * cfg.NITRO_BOOST_COOLDOWN_REDUCTION_PCT)
                inventory_dirty = True
            if duck_used:
                inventory_dirty = True
            if inventory_dirty:
                await state.economy_col.update_one(
                    {"_id": f"{ctx.guild.id}-{ctx.author.id}"}, {"$set": {"inventory": inventory}}, upsert=True
                )
            if has_drink:
                await ctx.send("⚡ **Energy Drink consumed!** Work cooldown reduced by 50%!")
            if nitro_used:
                await ctx.send(
                    f"🚀 Your Nitro Boost cut your next work cooldown by {nitro_reduction_seconds // 3600} hours!"
                )
                if nitro_expired:
                    await ctx.send("💨 Your Nitro Boost ran out after 3 uses.")
            base_payouts = {"developer": (300, 600), "duck": (200, 500)}
            descriptions = {
                "developer": "You wrote some killer code 💻",
                "duck": "You danced and quacked around the duck pond 🦆",
            }
            low, high = base_payouts[job]
            multiplier = 1 + 0.2 * promo_level
            multiplier *= earnings_multiplier
            low = int(low * multiplier)
            high = int(high * multiplier)
            earned = random.randint(low, high)
            await econ.add_balance(ctx.author.id, ctx.guild.id, earned)
            effective_ts = datetime.now(timezone.utc) - timedelta(
                seconds=int(3600 * (1 - cooldown_reduction)) + nitro_reduction_seconds
            )
            await state.economy_col.update_one(
                {"_id": cooldown_key}, {"$set": {"timestamp": effective_ts.isoformat()}}, upsert=True
            )
            msg = f"🧾 {descriptions.get(job, 'You worked hard!')}\n💰 You earned **{earned} coins** as a level `{promo_level}` {job}!"
            if has_cookie:
                msg += "\n🍪 **Lucky Cookie consumed!** Earnings doubled!"
            if tool_break_notice:
                msg += tool_break_notice
            await ctx.send(msg)
        except Exception as e:
            await ctx.send(
                "⚠️ Something went wrong while processing your work. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )
            print(f"[ERROR] work command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @work.error
    async def work_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                return await errors.send_hybrid_error(
                    ctx, content=f"⏰ You're on cooldown! Try again in {hours}h {minutes}m {seconds}s."
                )
            elif minutes > 0:
                return await errors.send_hybrid_error(
                    ctx, content=f"⏰ You're on cooldown! Try again in {minutes}m {seconds}s."
                )
            else:
                return await errors.send_hybrid_error(ctx, content=f"⏰ You're on cooldown! Try again in {seconds}s.")
        elif isinstance(error, commands.CommandError):
            await errors.send_hybrid_error(
                ctx,
                content="⚠️ Something went wrong while processing your work. Please contact " + cfg.BOT_ADMIN_NAME + ".",
            )
            print(f"[ERROR] work command: {type(error).__name__} - {error}")

    @commands.hybrid_command(name="jobstatus", description="Check your next promotion.")
    @perms.blacklist_barrier()
    @xp.xp_earn(4, 8)
    async def jobstatus(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            user_data = await state.economy_col.find_one({"_id": user_id}) or {}
            job = user_data.get("job")
            job_start_str = user_data.get("job_start")
            promo_level = user_data.get("promotion_level", 0)
            promo_chance = user_data.get("promotion_chance", 20.0)
            last_check_str = user_data.get("last_promo_check")
            last_roll_str = user_data.get("last_promo_roll")
            if not job or not job_start_str:
                prefix = await self.bot.get_prefix(ctx.message)
                return await ctx.send(f"💼 You don't currently have a job. Choose one with `{prefix}choosejob`.")
            try:
                job_start = datetime.fromisoformat(job_start_str)
                if job_start.tzinfo is None:
                    job_start = job_start.replace(tzinfo=timezone.utc)
            except Exception as e:
                print(f"[ERROR] jobstatus date parse: {type(e).__name__}: {e}")
                traceback.print_exc()
                return await ctx.send("⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + ".")
            now = datetime.now(timezone.utc)
            delta = now - job_start
            days = delta.days
            hours = delta.seconds // 3600
            minutes = delta.seconds % 3600 // 60
            promoted = False
            if days >= 7:
                if last_check_str:
                    try:
                        last_check = datetime.fromisoformat(last_check_str).replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
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
                    except (ValueError, TypeError):
                        last_roll = None
                    if last_roll:
                        allow_roll = now - last_roll >= timedelta(days=1)
                if allow_roll:
                    if elapsed_days > 0:
                        promo_chance += elapsed_days * 0.5
                        promo_chance = min(promo_chance, 100)
                    if random.random() <= promo_chance / 100:
                        promo_level += 1
                        promo_chance = 20.0
                        promoted = True
                        update_fields = {
                            "promotion_level": promo_level,
                            "promotion_chance": promo_chance,
                            "last_promo_check": now.isoformat(),
                            "last_promo_roll": now.isoformat(),
                        }
                        await state.economy_col.update_one({"_id": user_id}, {"$set": update_fields}, upsert=True)
                        embed = discord.Embed(
                            title="🎉 Promotion Achieved!",
                            description=f"Congratulations {ctx.author.mention}, you've been **promoted** to level `{promo_level}` in your job as a **{job.capitalize()}**!\n\n💰 You will now earn **even more coins** when you work!",
                            color=discord.Color.gold(),
                        )
                        embed.set_thumbnail(url="https://media.tenor.com/I5qPz6wS1jAAAAAC/congratulations-clapping.gif")
                        await ctx.send(embed=embed)
                if not promoted:
                    embed = discord.Embed(
                        title=f"📋 Job Status for {ctx.author.display_name}", color=discord.Color.blue()
                    )
                    embed.add_field(name="Job", value=job.capitalize(), inline=False)
                    embed.add_field(name="Promotion Level", value=str(promo_level), inline=False)
                    embed.add_field(name="Time on Job", value=f"{days}d {hours}h {minutes}m", inline=False)
                    if allow_roll:
                        embed.add_field(
                            name="Promotion Chance", value=f"✅ Eligible ({promo_chance:.2f}%)", inline=False
                        )
                    else:
                        next_time = last_roll + timedelta(days=1) if last_roll_str else now + timedelta(days=1)
                        embed.add_field(
                            name="Promotion Chance",
                            value=f"⏳ On cooldown ({promo_chance:.2f}%) - next roll <t:{int(next_time.timestamp())}:f>",
                            inline=False,
                        )
                    await ctx.send(embed=embed)
            else:
                embed = discord.Embed(title=f"📋 Job Status for {ctx.author.display_name}", color=discord.Color.blue())
                embed.add_field(name="Job", value=job.capitalize(), inline=False)
                embed.add_field(name="Promotion Level", value=str(promo_level), inline=False)
                embed.add_field(name="Time on Job", value=f"{days}d {hours}h {minutes}m", inline=False)
                embed.add_field(
                    name="Promotion Chance", value=f"❌ Not eligible yet (need {7 - days} more day(s))", inline=False
                )
                await ctx.send(embed=embed)
            if not promoted:
                update_fields = {"promotion_level": promo_level, "promotion_chance": promo_chance}
                if days >= 7 and "allow_roll" in locals() and allow_roll:
                    update_fields["last_promo_check"] = now.isoformat()
                    update_fields["last_promo_roll"] = now.isoformat()
                await state.economy_col.update_one({"_id": user_id}, {"$set": update_fields}, upsert=True)
        except Exception as e:
            print(f"[jobstatus command error] {type(e).__name__}: {e}")
            traceback.print_exc()
            await ctx.send("⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + ".")

    @commands.hybrid_command(name="fish", description="Go fishing to earn coins.")
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(14, 26)
    async def fish(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            now = datetime.now(timezone.utc)
            inventory = data.get("inventory", [])
            consumed, rod_broke, _ = econ.consume_tool_use(inventory, "fishing rod")
            if not consumed:
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("🎣 You need a fishing rod to fish!")
            tool_break_notice = (
                "\n💥 Your **Fishing Rod** broke. Buy a new one with `.buy fishing rod`." if rod_broke else ""
            )
            base_chance = 1.0
            luck_buff = 0.0
            for i, item in enumerate(inventory):
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    luck_buff = 0.3
                    item["uses_left"] -= 1
                    await ctx.send("🦆 Your Pet Duck helped you catch more fish!")
                    if item["uses_left"] <= 0:
                        inventory.pop(i)
                        await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
                    break
            nitro_used, nitro_expired = econ.consume_nitro_boost(inventory)
            if nitro_used:
                reduction_seconds = int(3600 * cfg.NITRO_BOOST_COOLDOWN_REDUCTION_PCT)
                econ.reduce_command_cooldown(ctx, reduction_seconds)
                await ctx.send(f"🚀 Your Nitro Boost cut your next fish cooldown by {reduction_seconds // 60} minutes!")
                if nitro_expired:
                    await ctx.send("💨 Your Nitro Boost ran out after 3 uses.")
            adjusted_chance = min(base_chance + luck_buff, 1.0)
            success = random.random() < adjusted_chance
            if not success:
                await state.economy_col.update_one({"_id": user_id}, {"$set": {"inventory": inventory}})
                return await ctx.send(f"🐟 You tried fishing, but came up empty handed!{tool_break_notice}")
            catch = random.choice(cfg.fishes)
            coins_earned = int(catch[1] * (1 + luck_buff))
            await econ.add_balance(ctx.author.id, ctx.guild.id, coins_earned)
            await state.economy_col.update_one(
                {"_id": user_id}, {"$set": {"inventory": inventory, "last_fished": now.isoformat()}}
            )
            msg = f"🎣 You caught a **{catch[0]}** and earned **{coins_earned} coins**!"
            if tool_break_notice:
                msg += tool_break_notice
            await ctx.send(msg)
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "fish_count")
            fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
        except Exception as e:
            ctx.command.reset_cooldown(ctx)
            print(f"[ERROR] fish command: {type(e).__name__} - {e}")
            traceback.print_exc()
            await ctx.send("⚠️ Something went wrong while fishing. Please contact " + cfg.BOT_ADMIN_NAME + ".")

    @fish.error
    async def fish_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await errors.send_hybrid_error(ctx, content=f"🕒 You can fish again in {minutes} minutes.")
        else:
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(name="swim", description="Swim into the deep ocean to find exotic fish.")
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(14, 26)
    async def swim(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            now = datetime.now(timezone.utc)
            inventory = data.get("inventory", [])
            consumed, gear_broke, _ = econ.consume_tool_use(inventory, "scuba gear")
            if not consumed:
                ctx._skip_xp_award = True
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("❌ You need **Scuba Gear** to swim! Buy it with `.buy scuba gear`.")
            tool_break_notice = (
                "\n💥 Your **Scuba Gear** broke. Buy a new one with `.buy scuba gear`." if gear_broke else ""
            )
            base_chance = 1.0
            luck_buff = 0.0
            for i, item in enumerate(inventory):
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    luck_buff = 0.3
                    item["uses_left"] -= 1
                    await ctx.send("🦆 Your Pet Duck brought you luck in the deep!")
                    if item["uses_left"] <= 0:
                        inventory.pop(i)
                        await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
                    break
            nitro_used, nitro_expired = econ.consume_nitro_boost(inventory)
            if nitro_used:
                reduction_seconds = int(3600 * cfg.NITRO_BOOST_COOLDOWN_REDUCTION_PCT)
                econ.reduce_command_cooldown(ctx, reduction_seconds)
                await ctx.send(f"🚀 Your Nitro Boost cut your next swim cooldown by {reduction_seconds // 60} minutes!")
                if nitro_expired:
                    await ctx.send("💨 Your Nitro Boost ran out after 3 uses.")
            adjusted_chance = min(base_chance + luck_buff, 1.0)
            success = random.random() < adjusted_chance
            if not success:
                await state.economy_col.update_one({"_id": user_id}, {"$set": {"inventory": inventory}})
                return await ctx.send(f"🌊 You dove deep, but found nothing this time!{tool_break_notice}")
            catch = random.choice(cfg.deep_ocean_fishes)
            coins_earned = int(catch[1] * (1 + luck_buff))
            await econ.add_balance(ctx.author.id, ctx.guild.id, coins_earned)
            await state.economy_col.update_one(
                {"_id": user_id}, {"$set": {"inventory": inventory, "last_swam": now.isoformat()}}
            )
            msg = f"🤿 You swam into the deep ocean and found a **{catch[0]}**! You sold it immediately for **{coins_earned} coins**!"
            if tool_break_notice:
                msg += tool_break_notice
            await ctx.send(msg)
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "fish_count")
            fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
        except Exception as e:
            ctx.command.reset_cooldown(ctx)
            print(f"[ERROR] swim command: {type(e).__name__} - {e}")
            traceback.print_exc()
            await ctx.send("⚠️ Something went wrong while swimming. Please contact " + cfg.BOT_ADMIN_NAME + ".")

    @swim.error
    async def swim_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await errors.send_hybrid_error(ctx, content=f"🕒 You can swim again in {minutes} minutes.")
        else:
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(
        name="attack", description="Attack another user and steal their coins.", aliases=["rob", "steal"]
    )
    @app_commands.describe(member="The user to attack (mention or name)")
    @perms.blacklist_barrier()
    @xp.xp_earn(14, 28)
    async def attack(self, ctx, member: discord.Member):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        if member == ctx.author:
            return await ctx.send("❌ You can't attack yourself!")
        now = datetime.now(timezone.utc)
        robber_id = f"{ctx.guild.id}-{ctx.author.id}"
        victim_id = f"{ctx.guild.id}-{member.id}"
        r_doc = await state.economy_col.find_one({"_id": robber_id}) or {}
        v_doc = await state.economy_col.find_one({"_id": victim_id}) or {}
        cooldown = r_doc.get("rob_cooldown")
        if cooldown:
            cooldown_dt = datetime.fromisoformat(cooldown)
            if cooldown_dt.tzinfo is None:
                cooldown_dt = cooldown_dt.replace(tzinfo=timezone.utc)
            if now < cooldown_dt:
                remaining = cooldown_dt - now
                mins = int(remaining.total_seconds() // 60)
                return await ctx.send(f"🕒 You can attack again in {mins} minute(s).")
        if r_doc.get("passive_until"):
            until = datetime.fromisoformat(r_doc["passive_until"])
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > now:
                return await ctx.send("🔒 You have passive mode enabled, disable it to attack others.")
        if v_doc.get("passive_until"):
            until = datetime.fromisoformat(v_doc["passive_until"])
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > now:
                return await ctx.send("🔒 That user has passive mode enabled, you can't attack them.")
        last_robbed = v_doc.get("last_robbed")
        if last_robbed:
            if isinstance(last_robbed, str):
                last_robbed = datetime.fromisoformat(last_robbed)
                if last_robbed.tzinfo is None:
                    last_robbed = last_robbed.replace(tzinfo=timezone.utc)
            if now - last_robbed < timedelta(hours=1):
                rem = timedelta(hours=1) - (now - last_robbed)
                minutes = round(rem.total_seconds() / 60)
                return await ctx.send(f"🛡️ {member.display_name} is under protection. Try again in {minutes} minutes.")
        if r_doc.get("wallet", 0) < 500:
            return await ctx.send("❌ You need at least 500 coins to attack.")
        if v_doc.get("wallet", 0) < 300:
            return await ctx.send("❌ They don't have enough coins to steal from.")
        amount = random.randint(100, min(500, v_doc["wallet"], r_doc["wallet"]))
        await econ.add_balance(ctx.author.id, ctx.guild.id, amount)
        await econ.subtract_balance(member.id, ctx.guild.id, amount)
        await state.economy_col.update_one(
            {"_id": robber_id}, {"$set": {"rob_cooldown": (now + timedelta(hours=3)).isoformat()}}
        )
        await state.economy_col.update_one({"_id": victim_id}, {"$set": {"last_robbed": now.isoformat()}})
        template = random.choice(_ATTACK_MESSAGES)
        msg = template.format(attacker=ctx.author.display_name, victim=member.display_name, amount=amount)
        await ctx.send(msg)

    @attack.error
    async def attack_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await errors.send_hybrid_error(
                ctx, content="❌ You must mention someone to attack. Example: `.attack @User`"
            )
        elif isinstance(error, commands.BadArgument):
            await errors.send_hybrid_error(ctx, content="❌ That's not a valid user.")
        else:
            root_error = errors.unwrap_command_error(error)
            if isinstance(root_error, commands.CommandOnCooldown):
                return await errors.send_hybrid_error(
                    ctx, content=f"❌ You are on cooldown. Try again in {root_error.retry_after:.2f}s"
                )
            print(f"[ERROR] attack_error: {root_error}")
            traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(name="crime", description="Attempt a risky crime to earn coins.")
    @perms.blacklist_barrier()
    @xp.xp_earn(14, 28)
    async def crime(self, ctx, *, choice: str):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
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
                    return await ctx.send(f"🕒 You can commit a crime again in {hours}h {minutes}m.")
            choice = choice.lower().strip()
            valid = ["bank", "shoplift", "payroll"]
            if choice not in valid:
                return await ctx.send("❌ Choose a valid crime: `bank`, `shoplift`, or `payroll`.")
            lockpick_break_notice = ""
            if choice == "bank":
                consumed, broke, _ = econ.consume_tool_use(inventory, "lockpick")
                if not consumed:
                    return await ctx.send("🔐 You need to buy a **🗝️ Lockpick** to rob the bank!")
                if broke:
                    lockpick_break_notice = "\n💥 Your **Lockpick** broke. Buy a new one with `.buy lockpick`."
            config = {
                "bank": {"chance": 0.4, "gain": (1200, 3000), "fine": (600, 1500)},
                "shoplift": {"chance": 0.5, "gain": (300, 600), "fine": (150, 400)},
                "payroll": {"chance": 0.4, "gain": (800, 1500), "fine": (400, 800)},
            }
            conf = config[choice]
            luck_buff = 0.0
            for i, item in enumerate(inventory):
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    luck_buff = 0.3
                    item["uses_left"] -= 1
                    await ctx.send("🦆 Your Pet Duck increased your crime success chance!")
                    if item["uses_left"] <= 0:
                        inventory.pop(i)
                        await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
                    break
            coffee_used = econ.pop_food_item(inventory, "coffee_cup")
            coffee_bonus = 0.25 if coffee_used else 0.0
            if coffee_used:
                await ctx.send("☕ **Coffee Cup consumed!** Crime success chance increased by 25%!")
            nitro_used, nitro_expired = econ.consume_nitro_boost(inventory)
            nitro_reduction_seconds = 0
            if nitro_used:
                nitro_reduction_seconds = int(86400 * cfg.NITRO_BOOST_COOLDOWN_REDUCTION_PCT)
                await ctx.send(
                    f"🚀 Your Nitro Boost cut your next crime cooldown by {nitro_reduction_seconds // 3600} hours!"
                )
                if nitro_expired:
                    await ctx.send("💨 Your Nitro Boost ran out after 3 uses.")
            adjusted_chance = min(conf["chance"] + luck_buff + coffee_bonus, 1.0)
            success = random.random() < adjusted_chance
            if success:
                amount = random.randint(*conf["gain"])
                await econ.add_balance(ctx.author.id, ctx.guild.id, amount)
                effective_crime_ts = now - timedelta(seconds=nitro_reduction_seconds)
                await state.economy_col.update_one(
                    {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                    {"$set": {"inventory": inventory, "last_crime": effective_crime_ts.isoformat()}},
                )
                msg = f"💥 Crime successful! You earned **{amount} coins** via `{choice}` crime."
                if lockpick_break_notice:
                    msg += lockpick_break_notice
                await ctx.send(msg)
            else:
                fine = random.randint(*conf["fine"])
                new_wallet = max(0, wallet - fine)
                await state.economy_col.update_one(
                    {"_id": f"{ctx.guild.id}-{ctx.author.id}"}, {"$set": {"wallet": new_wallet, "inventory": inventory}}
                )
                await ctx.send(
                    f"🚓 You were caught during the `{choice}` attempt. Fined **{fine} coins**.{lockpick_break_notice}"
                )
        except Exception as e:
            print(f"[ERROR] crime command: {type(e).__name__} - {e}")
            traceback.print_exc()
            await ctx.send("⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + ".")

    @crime.error
    async def crime_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            seconds = int(error.retry_after)
            hours, remainder = divmod(seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            await errors.send_hybrid_error(ctx, content=f"🕒 You can commit a crime again in {hours}h {minutes}m.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await errors.send_hybrid_error(ctx, content="❌ You must specify a crime type. Example: `?crime bank`")
        else:
            root_error = errors.unwrap_command_error(error)
            if isinstance(root_error, commands.CommandOnCooldown):
                return await errors.send_hybrid_error(
                    ctx, content=f"❌ You are on cooldown. Try again in {root_error.retry_after:.2f}s"
                )
            print(f"[ERROR] crime_error: {root_error}")
            traceback.print_exception(type(root_error), root_error, root_error.__traceback__)
            await errors.send_hybrid_error(
                ctx, content="⚠️ An unexpected error occurred. Please contact " + cfg.BOT_ADMIN_NAME + "."
            )

    @commands.hybrid_command(name="hunt", description="Go hunting for animals.")
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(12, 24)
    async def hunt(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            inventory = data.get("inventory", [])
            consumed, rifle_broke, _ = econ.consume_tool_use(inventory, "rifle")
            if not consumed:
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("🔫 You need a rifle to hunt!")
            tool_break_notice = "\n💥 Your **Rifle** broke. Buy a new one with `.buy rifle`." if rifle_broke else ""
            animals = [("rabbit", 200), ("deer", 450), ("bear", 600)]
            catch = random.choice(animals)
            animal, value = catch
            inventory.append(animal)
            await state.economy_col.update_one({"_id": user_id}, {"$set": {"inventory": inventory}})
            await ctx.send(f"🏹 You hunted a **{animal}**! (Sell value: {value} coins){tool_break_notice}")
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "hunt_count")
            fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
        except Exception as e:
            ctx.command.reset_cooldown(ctx)
            await ctx.send("⚠️ Something went wrong while hunting. Please contact " + cfg.BOT_ADMIN_NAME + ".")
            print(f"[ERROR] hunt command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @hunt.error
    async def hunt_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await errors.send_hybrid_error(ctx, content=f"🕒 You can hunt again in {minutes} minutes.")
        else:
            await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred while hunting.")

    @commands.hybrid_command(name="mine", description="Go mining for ores.")
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(12, 24)
    async def mine(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            inventory = data.get("inventory", [])
            consumed, pickaxe_broke, _ = econ.consume_tool_use(inventory, "pickaxe")
            if not consumed:
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("⛏️ You need a pickaxe to mine!")
            tool_break_notice = (
                "\n💥 Your **Pickaxe** broke. Buy a new one with `.buy pickaxe`." if pickaxe_broke else ""
            )
            ores = [("iron ore", 200), ("gold ore", 500), ("diamond", 1200)]
            catch = random.choice(ores)
            ore, value = catch
            inventory.append(ore)
            await state.economy_col.update_one({"_id": user_id}, {"$set": {"inventory": inventory}})
            await ctx.send(f"⛏️ You mined **{ore}**! (Sell value: {value} coins){tool_break_notice}")
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "mine_count")
            fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
        except Exception as e:
            ctx.command.reset_cooldown(ctx)
            await ctx.send("⚠️ Something went wrong while mining. Please contact " + cfg.BOT_ADMIN_NAME + ".")
            print(f"[ERROR] mine command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @mine.error
    async def mine_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await errors.send_hybrid_error(ctx, content=f"🕒 You can mine again in {minutes} minutes.")
        else:
            await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred while mining.")

    @commands.hybrid_command(name="dig", description="Dig for cool rocks.")
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(12, 24)
    async def dig(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            inventory = data.get("inventory", [])
            consumed, shovel_broke, _ = econ.consume_tool_use(inventory, "shovel")
            if not consumed:
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("🪏 You need a shovel to dig!")
            tool_break_notice = "\n💥 Your **Shovel** broke. Buy a new one with `.buy shovel`." if shovel_broke else ""
            found_rock, value = random.choice(cfg.dig_rocks)
            inventory.append(found_rock)
            await state.economy_col.update_one({"_id": user_id}, {"$set": {"inventory": inventory}})
            await ctx.send(f"🪏 You dug up **{found_rock}**! (Sell value: {value} coins){tool_break_notice}")
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "dig_count")
            fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
        except Exception as e:
            ctx.command.reset_cooldown(ctx)
            await ctx.send("⚠️ Something went wrong while digging. Please contact " + cfg.BOT_ADMIN_NAME + ".")
            print(f"[ERROR] dig command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @dig.error
    async def dig_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await errors.send_hybrid_error(ctx, content=f"🕒 You can dig again in {minutes} minutes.")
        else:
            await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred while digging.")

    @commands.hybrid_command(
        name="bugcatch", description="Catch bugs and sell them instantly for coins.", aliases=["catch"]
    )
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @perms.blacklist_barrier()
    @xp.xp_earn(12, 24)
    async def bugcatch(self, ctx):
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            inventory = data.get("inventory", [])
            consumed, net_broke, _ = econ.consume_tool_use(inventory, "butterfly net")
            if not consumed:
                ctx.command.reset_cooldown(ctx)
                return await ctx.send(
                    "🦋 You need a **Butterfly Net** to catch bugs! Buy one with `.buy butterfly net`."
                )
            coins_multiplier = 1.0
            for i, item in enumerate(inventory):
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    coins_multiplier *= 1.3
                    item["uses_left"] -= 1
                    await ctx.send("🦆 Your Pet Duck helped you sniff out better bugs!")
                    if item["uses_left"] <= 0:
                        inventory.pop(i)
                        await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
                    break
            nitro_used, nitro_expired = econ.consume_nitro_boost(inventory)
            if nitro_used:
                reduction_seconds = int(3600 * cfg.NITRO_BOOST_COOLDOWN_REDUCTION_PCT)
                econ.reduce_command_cooldown(ctx, reduction_seconds)
                await ctx.send(
                    f"🚀 Your Nitro Boost cut your next bugcatch cooldown by {reduction_seconds // 60} minutes!"
                )
                if nitro_expired:
                    await ctx.send("💨 Your Nitro Boost ran out after 3 uses.")
            bug_name, base_value = random.choice(cfg.bugs_to_catch)
            coins_earned = int(base_value * coins_multiplier)
            await econ.add_balance(ctx.author.id, ctx.guild.id, coins_earned)
            await state.economy_col.update_one({"_id": user_id}, {"$set": {"inventory": inventory}})
            message = f"🪲 You caught **{bug_name}** and sold it immediately for **{coins_earned} coins**!"
            if net_broke:
                message += "\n💥 Your **Butterfly Net** broke. Buy a new one with `.buy butterfly net`."
            await ctx.send(message)
            await xp.increment_badge_counter(str(ctx.guild.id), str(ctx.author.id), "bug_count")
            fresh_data = await econ.get_user(ctx, ctx.guild.id, ctx.author.id)
            await xp.check_and_award_badges(ctx, ctx.guild, ctx.author, fresh_data)
        except Exception as e:
            ctx.command.reset_cooldown(ctx)
            await ctx.send("⚠️ Something went wrong while bug catching. Please contact " + cfg.BOT_ADMIN_NAME + ".")
            print(f"[ERROR] bugcatch command: {type(e).__name__} - {e}")
            traceback.print_exc()

    @bugcatch.error
    async def bugcatch_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await errors.send_hybrid_error(ctx, content=f"🕒 You can bugcatch again in {minutes} minutes.")
        else:
            await errors.send_hybrid_error(ctx, content="⚠️ An unexpected error occurred while bug catching.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JobsGathering(bot))
