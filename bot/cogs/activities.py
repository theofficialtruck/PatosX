"""Activity commands: hunt, mine, fish."""

from __future__ import annotations

import random
import traceback
from datetime import datetime, timezone

from discord.ext import commands

from bot.config.constants import FISHES, HUNT_ANIMALS, MINE_ORES
from bot.database import economy_col
from bot.utils.channels import check_channel_setting as check_channel
from bot.utils.checks import blacklist_barrier, xp_earn
from bot.utils.economy import add_balance, get_user
from bot.utils.errors import send_hybrid_error


class ActivitiesCog(commands.Cog, name="Activities"):
    """Cooldown-gated income activities (fish/hunt/mine)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="fish", description="Go fishing to earn coins."
    )
    @commands.cooldown(1, 10800, commands.BucketType.member)
    @blacklist_barrier()
    @xp_earn(14, 26)
    async def fish(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            now = datetime.now(timezone.utc)

            inventory = data.get("inventory", [])
            has_fishing_rod = any(
                (isinstance(item, str) and item == "fishing rod")
                or (isinstance(item, dict) and item.get("_id") == "fishing rod")
                for item in inventory
            )
            if not has_fishing_rod:
                return await ctx.send("🎣 You need a fishing rod to fish!")

            base_chance = 1.0
            luck_buff = 0.0

            for index, item in enumerate(inventory):
                if isinstance(item, dict) and item.get("_id") == "pet_duck":
                    luck_buff = 0.3
                    item["uses_left"] -= 1
                    await ctx.send(
                        "🦆 Your Pet Duck helped you catch more fish!"
                    )
                    if item["uses_left"] <= 0:
                        inventory.pop(index)
                        await ctx.send(
                            "💔 One of your Pet Ducks has left after 3 uses."
                        )
                    break

            adjusted_chance = min(base_chance + luck_buff, 1.0)
            success = random.random() < adjusted_chance
            if not success:
                return await ctx.send(
                    "🐟 You tried fishing, but came up empty-handed!"
                )

            catch = random.choice(FISHES)
            coins_earned = int(catch[1] * (1 + luck_buff))
            await add_balance(ctx.author.id, ctx.guild.id, coins_earned)
            await economy_col.update_one(
                {"_id": user_id},
                {"$set": {"inventory": inventory, "last_fished": now.isoformat()}},
            )
            await ctx.send(
                f"🎣 You caught a **{catch[0]}** and earned **{coins_earned} coins**!"
            )

        except Exception as exc:
            print(f"[ERROR] fish command: {type(exc).__name__} - {exc}")
            traceback.print_exc()
            await ctx.send(
                "⚠️ Something went wrong while fishing. Contact thetruck."
            )

    @fish.error
    async def fish_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            hours, remainder = divmod(total_seconds, 10800)
            minutes, _ = divmod(remainder, 60)
            return await send_hybrid_error(
                ctx, content=f"🕒 You can fish again in {hours}h {minutes}m."
            )
        await send_hybrid_error(
            ctx, content="⚠️ An unexpected error occurred. Contact thetruck."
        )

    @commands.hybrid_command(
        name="hunt", description="Go hunting for animals."
    )
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @blacklist_barrier()
    @xp_earn(12, 24)
    async def hunt(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)

            inventory = data.get("inventory", [])
            has_rifle = any(
                (isinstance(item, str) and item == "rifle")
                or (isinstance(item, dict) and item.get("_id") == "rifle")
                for item in inventory
            )
            if not has_rifle:
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("🔫 You need a rifle to hunt!")

            catch = random.choice(HUNT_ANIMALS)
            animal, value = catch
            inventory.append(animal)

            await economy_col.update_one(
                {"_id": user_id}, {"$set": {"inventory": inventory}}
            )
            await ctx.send(
                f"🏹 You hunted a **{animal}**! (Sell value: {value} coins)"
            )
        except Exception as exc:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(
                "⚠️ Something went wrong while hunting. Contact thetruck."
            )
            print(f"[ERROR] hunt command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @hunt.error
    async def hunt_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await send_hybrid_error(
                ctx, content=f"🕒 You can hunt again in {minutes} minutes."
            )
        await send_hybrid_error(
            ctx, content="⚠️ An unexpected error occurred while hunting."
        )

    @commands.hybrid_command(
        name="mine", description="Go mining for ores."
    )
    @commands.cooldown(1, 3600, commands.BucketType.member)
    @blacklist_barrier()
    @xp_earn(12, 24)
    async def mine(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)

            inventory = data.get("inventory", [])
            has_pickaxe = any(
                (isinstance(item, str) and item == "pickaxe")
                or (isinstance(item, dict) and item.get("_id") == "pickaxe")
                for item in inventory
            )
            if not has_pickaxe:
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("⛏️ You need a pickaxe to mine!")

            catch = random.choice(MINE_ORES)
            ore, value = catch
            inventory.append(ore)

            await economy_col.update_one(
                {"_id": user_id}, {"$set": {"inventory": inventory}}
            )
            await ctx.send(
                f"⛏️ You mined **{ore}**! (Sell value: {value} coins)"
            )
        except Exception as exc:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(
                "⚠️ Something went wrong while mining. Contact thetruck."
            )
            print(f"[ERROR] mine command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @mine.error
    async def mine_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await send_hybrid_error(
                ctx, content=f"🕒 You can mine again in {minutes} minutes."
            )
        await send_hybrid_error(
            ctx, content="⚠️ An unexpected error occurred while mining."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActivitiesCog(bot))
