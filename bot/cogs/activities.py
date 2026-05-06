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

TOOL_DURABILITY: dict[str, int] = {
    "fishing rod": 30,
    "rifle": 25,
    "pickaxe": 25,
    "bug net": 20,
    "shovel": 20,
}

BUG_REWARDS: tuple[tuple[str, int], ...] = (
    ("ladybug", 90),
    ("beetle", 130),
    ("dragonfly", 170),
    ("mantis", 220),
)

DIG_REWARDS: tuple[tuple[str, int], ...] = (
    ("old coin", 120),
    ("silver shard", 180),
    ("golden relic", 260),
    ("buried gem", 340),
)


class ActivitiesCog(commands.Cog, name="Activities"):
    """Cooldown-gated income activities (fish/hunt/mine)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _find_or_init_tool(
        self, inventory: list, tool_name: str
    ) -> tuple[int | None, dict | None]:
        for index, item in enumerate(inventory):
            if isinstance(item, str) and item == tool_name:
                tool_data = {"_id": tool_name, "uses_left": TOOL_DURABILITY[tool_name]}
                inventory[index] = tool_data
                return index, tool_data
            if isinstance(item, dict) and item.get("_id") == tool_name:
                uses_left = item.get("uses_left")
                if not isinstance(uses_left, int) or uses_left <= 0:
                    item["uses_left"] = TOOL_DURABILITY[tool_name]
                return index, item
        return None, None

    def _consume_tool_use(
        self, inventory: list, tool_index: int, tool_data: dict
    ) -> tuple[int, bool]:
        uses_left = int(tool_data.get("uses_left", 1))
        uses_left -= 1
        if uses_left <= 0:
            inventory.pop(tool_index)
            return 0, True
        tool_data["uses_left"] = uses_left
        inventory[tool_index] = tool_data
        return uses_left, False

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
            rod_index, rod_data = self._find_or_init_tool(inventory, "fishing rod")
            if rod_data is None or rod_index is None:
                ctx._skip_xp_award = True
                ctx.command.reset_cooldown(ctx)
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
                remaining_uses, broke = self._consume_tool_use(
                    inventory, rod_index, rod_data
                )
                updates = {"inventory": inventory, "last_fished": now.isoformat()}
                await economy_col.update_one({"_id": user_id}, {"$set": updates})
                ctx._skip_xp_award = True
                if broke:
                    return await ctx.send(
                        "🐟 You came up empty-handed, and your fishing rod broke."
                    )
                return await ctx.send(
                    "🐟 You tried fishing, but came up empty-handed! "
                    f"(🎣 Rod durability: {remaining_uses})"
                )

            catch = random.choice(FISHES)
            coins_earned = int(catch[1] * (1 + luck_buff))
            await add_balance(ctx.author.id, ctx.guild.id, coins_earned)
            remaining_uses, broke = self._consume_tool_use(inventory, rod_index, rod_data)
            await economy_col.update_one(
                {"_id": user_id},
                {
                    "$set": {"inventory": inventory, "last_fished": now.isoformat()},
                    "$inc": {"fish_count": 1},
                },
            )
            durability_text = (
                " Your fishing rod broke after this catch."
                if broke
                else f" (🎣 Rod durability: {remaining_uses})"
            )
            await ctx.send(
                f"🎣 You caught a **{catch[0]}** and earned **{coins_earned} coins**!"
                f"{durability_text}"
            )

        except Exception as exc:
            ctx._skip_xp_award = True
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
            rifle_index, rifle_data = self._find_or_init_tool(inventory, "rifle")
            if rifle_data is None or rifle_index is None:
                ctx._skip_xp_award = True
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("🔫 You need a rifle to hunt!")

            catch = random.choice(HUNT_ANIMALS)
            animal, value = catch
            inventory.append(animal)
            remaining_uses, broke = self._consume_tool_use(
                inventory, rifle_index, rifle_data
            )

            await economy_col.update_one(
                {"_id": user_id},
                {"$set": {"inventory": inventory}, "$inc": {"hunt_count": 1}},
            )
            durability_text = (
                " Your rifle broke after this hunt."
                if broke
                else f" (🔫 Rifle durability: {remaining_uses})"
            )
            await ctx.send(
                f"🏹 You hunted a **{animal}**! (Sell value: {value} coins)"
                f"{durability_text}"
            )
        except Exception as exc:
            ctx._skip_xp_award = True
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
            pickaxe_index, pickaxe_data = self._find_or_init_tool(inventory, "pickaxe")
            if pickaxe_data is None or pickaxe_index is None:
                ctx._skip_xp_award = True
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("⛏️ You need a pickaxe to mine!")

            catch = random.choice(MINE_ORES)
            ore, value = catch
            inventory.append(ore)
            remaining_uses, broke = self._consume_tool_use(
                inventory, pickaxe_index, pickaxe_data
            )

            await economy_col.update_one(
                {"_id": user_id},
                {"$set": {"inventory": inventory}, "$inc": {"mine_count": 1}},
            )
            durability_text = (
                " Your pickaxe broke after this mining run."
                if broke
                else f" (⛏️ Pickaxe durability: {remaining_uses})"
            )
            await ctx.send(
                f"⛏️ You mined **{ore}**! (Sell value: {value} coins)"
                f"{durability_text}"
            )
        except Exception as exc:
            ctx._skip_xp_award = True
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

    @commands.hybrid_command(
        name="dig",
        description="Dig for buried finds and coins.",
        aliases=["excavate"],
    )
    @commands.cooldown(1, 1800, commands.BucketType.member)
    @blacklist_barrier()
    @xp_earn(10, 20)
    async def dig(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            inventory = data.get("inventory", [])

            shovel_index, shovel_data = self._find_or_init_tool(inventory, "shovel")
            if shovel_data is None or shovel_index is None:
                ctx._skip_xp_award = True
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("🪏 You need a shovel to dig!")

            find_name, coins = random.choice(DIG_REWARDS)
            await add_balance(ctx.author.id, ctx.guild.id, coins)
            remaining_uses, broke = self._consume_tool_use(
                inventory, shovel_index, shovel_data
            )
            await economy_col.update_one(
                {"_id": user_id},
                {"$set": {"inventory": inventory}, "$inc": {"dig_count": 1}},
            )

            durability_text = (
                " Your shovel broke after this dig."
                if broke
                else f" (🪏 Shovel durability: {remaining_uses})"
            )
            await ctx.send(
                f"🪏 You dug up a **{find_name}** and sold it for **{coins} coins**!"
                f"{durability_text}"
            )
        except Exception as exc:
            ctx._skip_xp_award = True
            ctx.command.reset_cooldown(ctx)
            await ctx.send("⚠️ Something went wrong while digging. Contact thetruck.")
            print(f"[ERROR] dig command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @dig.error
    async def dig_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await send_hybrid_error(
                ctx, content=f"🕒 You can dig again in {minutes} minutes."
            )
        await send_hybrid_error(
            ctx, content="⚠️ An unexpected error occurred while digging."
        )

    @commands.hybrid_command(
        name="bugcatch",
        description="Catch bugs and cash them in.",
        aliases=["bugs", "catchbugs"],
    )
    @commands.cooldown(1, 1800, commands.BucketType.member)
    @blacklist_barrier()
    @xp_earn(10, 20)
    async def bugcatch(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            inventory = data.get("inventory", [])

            net_index, net_data = self._find_or_init_tool(inventory, "bug net")
            if net_data is None or net_index is None:
                ctx._skip_xp_award = True
                ctx.command.reset_cooldown(ctx)
                return await ctx.send("🪲 You need a bug net to catch bugs!")

            bug_name, coins = random.choice(BUG_REWARDS)
            await add_balance(ctx.author.id, ctx.guild.id, coins)
            remaining_uses, broke = self._consume_tool_use(inventory, net_index, net_data)
            await economy_col.update_one(
                {"_id": user_id},
                {"$set": {"inventory": inventory}, "$inc": {"bugcatch_count": 1}},
            )

            durability_text = (
                " Your bug net tore after this catch."
                if broke
                else f" (🪲 Bug net durability: {remaining_uses})"
            )
            await ctx.send(
                f"🪲 You caught a **{bug_name}** and earned **{coins} coins**!"
                f"{durability_text}"
            )
        except Exception as exc:
            ctx._skip_xp_award = True
            ctx.command.reset_cooldown(ctx)
            await ctx.send(
                "⚠️ Something went wrong while bug catching. Contact thetruck."
            )
            print(f"[ERROR] bugcatch command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @bugcatch.error
    async def bugcatch_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            total_seconds = int(error.retry_after)
            minutes = total_seconds // 60
            return await send_hybrid_error(
                ctx, content=f"🕒 You can bugcatch again in {minutes} minutes."
            )
        await send_hybrid_error(
            ctx, content="⚠️ An unexpected error occurred while bug catching."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActivitiesCog(bot))
