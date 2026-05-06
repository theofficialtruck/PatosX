"""Casino mini-games and the gambling commands (mines, doors, towers, coinflip, etc.)."""

from __future__ import annotations

import asyncio
import random
import traceback
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View

from bot.config.constants import DISCORD_SERVICE_UNAVAILABLE_MESSAGE
from bot.database import economy_col
from bot.utils.channels import check_channel_setting as check_channel
from bot.utils.checks import blacklist_barrier, xp_earn
from bot.utils.economy import (
    add_balance,
    get_balance,
    get_user,
    subtract_balance,
)
from bot.utils.errors import (
    is_discord_service_unavailable_error,
    send_hybrid_error,
)
from bot.utils.minigame_player import ensure_user
from bot.utils.numbers import add_suffix, suffix_to_int
from bot.views.games import (
    DifficultySelect,
    DoorCountSelect,
    MinesBombSelect,
)


class GamesCog(commands.Cog, name="Games"):
    """Casino-style commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Door Game
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="doorgame", description="Try your luck through multiple doors!"
    )
    @commands.cooldown(1, 5, commands.BucketType.member)
    @blacklist_barrier()
    @xp_earn(12, 24)
    async def doorgame(self, ctx: commands.Context) -> None:
        try:
            await ctx.send("💰 Please type your **bet amount** (e.g. `100`, `1k`, `1.5m`):")

            def check(message: discord.Message) -> bool:
                return (
                    message.author == ctx.author
                    and message.channel == ctx.channel
                )

            try:
                msg = await self.bot.wait_for("message", check=check, timeout=30.0)
            except asyncio.TimeoutError:
                return await ctx.send(
                    "⌛ You took too long to respond. The game has been cancelled."
                )

            try:
                bet = suffix_to_int(msg.content.strip())
            except ValueError:
                return await ctx.send(
                    "❌ Invalid bet amount! Please enter a number like "
                    "`100`, `1k`, or `1.5m`."
                )

            uid = str(ctx.author.id)
            guild_id = str(ctx.guild.id)

            user_doc = await economy_col.find_one({"_id": f"{guild_id}-{uid}"})
            wallet = user_doc.get("wallet", 0) if user_doc else 0

            if wallet < bet:
                return await ctx.send("❌ You don’t have enough coins for that bet!")

            await economy_col.update_one(
                {"_id": f"{guild_id}-{uid}"},
                {"$inc": {"wallet": -bet}},
                upsert=True,
            )

            user_doc = await economy_col.find_one({"_id": f"{guild_id}-{uid}"})
            current_balance = user_doc.get("wallet", 0)

            select = DoorCountSelect(ctx, bet)
            await select.set_start_balance(current_balance)

            class DoorCountView(View):
                def __init__(self, parent_ctx, child_select) -> None:
                    super().__init__(timeout=30.0)
                    self.ctx = parent_ctx
                    self.add_item(child_select)
                    self.message: discord.Message | None = None

                async def on_timeout(self) -> None:
                    for child in self.children:
                        child.disabled = True
                    try:
                        await self.message.edit(
                            content="⌛ You didn’t select a door count in time. Game cancelled.",
                            embed=None,
                            view=self,
                        )
                    except discord.Forbidden:
                        await self.ctx.send(
                            "⌛ You didn’t select a door count in time. Game cancelled."
                        )

            embed = discord.Embed(
                title="🚪 Door Game Setup",
                description=(
                    f"Your bet: **{add_suffix(bet)} coins**\n"
                    f"Current Balance: `{add_suffix(current_balance)}`\n\n"
                    "Now choose how many doors you want to go through:"
                ),
                color=0xFFA500,
            )

            view = DoorCountView(ctx, select)
            bot_msg = await ctx.send(embed=embed, view=view)
            view.message = bot_msg

        except Exception as exc:
            await ctx.send(
                "⚠️ Something went wrong while setting up the game. Contact thetruck."
            )
            print(f"[ERROR] doorgame setup: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Mines
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="mines", description="Play Mines and test your luck!"
    )
    @blacklist_barrier()
    @xp_earn(12, 24)
    async def mines(self, ctx: commands.Context) -> None:
        await ctx.send("💎 How much would you like to bet? (Type a number or 'all')")

        def check_bet(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        try:
            bet_msg = await self.bot.wait_for("message", check=check_bet, timeout=60.0)
        except asyncio.TimeoutError:
            await ctx.send("⏱ You took too long to respond. Command cancelled.")
            return

        bet_input = bet_msg.content.strip()
        uid = ctx.author.id
        guild_id = ctx.guild.id

        user_balance = await get_balance(uid, guild_id)

        if bet_input.lower() == "all":
            bet = user_balance
        else:
            cleaned_bet = bet_input.replace(",", "").replace("$", "").strip().lower()
            if not any(ch.isdigit() for ch in cleaned_bet):
                await ctx.send(
                    "❌ Please enter a valid number (like `100`, `1k`, or `all`)."
                )
                return
            try:
                bet = suffix_to_int(cleaned_bet)
            except ValueError:
                await ctx.send(
                    "❌ Invalid bet format! Try something like `500`, `1k`, or `2m`."
                )
                return

        if bet <= 0:
            await ctx.send("❌ Bet must be greater than 0.")
            return
        if bet > user_balance:
            await ctx.send("💎 You don’t have enough balance for that bet!")
            return

        await add_balance(uid, guild_id, -bet)
        house_edge = 0.15
        select = MinesBombSelect(ctx, bet, house_edge)
        view = View()
        view.add_item(select)
        await ctx.send("🧨 Choose the number of bombs:", view=view)

    # ------------------------------------------------------------------
    # Duck Towers
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="ducktowers", description="Play a game of Duck Towers!"
    )
    @commands.cooldown(1, 15, commands.BucketType.member)
    @blacklist_barrier()
    @xp_earn(12, 24)
    async def ducktowers(self, ctx: commands.Context) -> None:
        try:
            uid = ctx.author.id
            guild_id = ctx.guild.id
            await ensure_user(str(uid))

            await ctx.send("💎 How much would you like to bet? (Type a number, or 'all')")

            def check_bet(message: discord.Message) -> bool:
                return message.author == ctx.author and message.channel == ctx.channel

            bet_msg = await self.bot.wait_for("message", check=check_bet, timeout=60.0)
            bet_input = bet_msg.content.strip()

            user_balance = await get_balance(uid, guild_id)
            bet = (
                user_balance if bet_input.lower() == "all" else suffix_to_int(bet_input)
            )

            if bet <= 0:
                return await ctx.send("❌ Bet must be greater than zero.")
            if bet > user_balance:
                return await ctx.send(
                    f"💎 You only have `{add_suffix(user_balance)}`, "
                    "not enough for that bet!"
                )

            select = DifficultySelect(ctx, bet)
            view = View()
            view.add_item(select)
            await ctx.send("🦆 Choose your difficulty:", view=view)

        except asyncio.TimeoutError:
            await ctx.send("⌛ You took too long to respond — game canceled.")
        except ValueError:
            await ctx.send(
                "⚠️ Invalid bet amount. Try again using a number or 'all'."
            )
        except Exception as exc:
            await ctx.send(
                "⚠️ Something went wrong while starting your Duck Towers game."
            )
            print(f"[ERROR] ducktowers command: {type(exc).__name__} - {exc}")

    # ------------------------------------------------------------------
    # Coinflip / Duckroll / Lottery
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="coinflip",
        description="Coin flip for coins.",
        aliases=["cf"],
    )
    @app_commands.describe(amount="Amount to bet (number or 'all')")
    @blacklist_barrier()
    @xp_earn(10, 20)
    async def coinflip(self, ctx: commands.Context, amount: str) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            ctx._skip_xp_award = True
            return

        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        wallet = data.get("wallet", 0)

        if amount.lower() == "all":
            bet_amount = wallet
        else:
            try:
                bet_amount = int(amount)
            except ValueError:
                ctx._skip_xp_award = True
                return await ctx.send("❌ Please enter a valid number or `all`.")

        if bet_amount <= 0:
            ctx._skip_xp_award = True
            return await ctx.send("❌ Invalid amount to coin flip.")
        if bet_amount > wallet:
            ctx._skip_xp_award = True
            return await ctx.send("❌ You can't afford that!")

        luck_buff = data.get("luck_buff", False)
        if luck_buff:
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$unset": {"luck_buff": ""}},
            )

        if random.random() < 0.5:
            await add_balance(ctx.author.id, ctx.guild.id, bet_amount)
            await ctx.send(
                f"🎉 You won {bet_amount} coins from flipping a coin!"
            )
        else:
            await subtract_balance(ctx.author.id, ctx.guild.id, bet_amount)
            await ctx.send(
                f"💸 You lost {bet_amount} coins from flipping a coin."
            )

    @coinflip.error
    async def coinflip_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await send_hybrid_error(
                ctx, content="❌ You must specify an amount (number or `all`)."
            )
        elif is_discord_service_unavailable_error(error):
            await send_hybrid_error(
                ctx, content=DISCORD_SERVICE_UNAVAILABLE_MESSAGE
            )
        else:
            await send_hybrid_error(ctx, content="⚠️ Error, contact thetruck.")

    @commands.hybrid_command(
        name="duckroll",
        description="Guess if the ducks are higher or lower than 50!",
    )
    @blacklist_barrier()
    @xp_earn(10, 20)
    async def duckroll(self, ctx: commands.Context, guess: str) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return
        try:
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data.get("wallet", 0)

            guess = guess.lower()
            if guess not in {"high", "low"}:
                return await ctx.send(
                    "❌ Invalid choice! Use `.duckroll high` or `.duckroll low`."
                )

            bet_amount = 150
            if wallet < bet_amount:
                return await ctx.send(
                    "❌ You don’t have enough coins to play duckroll! "
                    "(Need at least 150)"
                )

            roll = random.randint(1, 100)
            if (roll > 50 and guess == "high") or (roll < 50 and guess == "low"):
                await add_balance(ctx.author.id, ctx.guild.id, bet_amount)
                await ctx.send(
                    f"🦆 You rolled **{roll} ducks**!\n"
                    f"✅ Correct guess! You won **{bet_amount} coins** 🎉"
                )
            elif roll == 50:
                await ctx.send(
                    f"🦆 You rolled exactly **50 ducks**!\n"
                    "🤷 It’s a draw. No win, no loss."
                )
            else:
                await subtract_balance(ctx.author.id, ctx.guild.id, bet_amount)
                await ctx.send(
                    f"🦆 You rolled **{roll} ducks**!\n"
                    f"❌ Wrong guess! You lost **{bet_amount} coins** 💸"
                )
        except Exception as exc:
            await ctx.send(
                "⚠️ Something went wrong while processing your duckroll. Contact thetruck."
            )
            print(f"[ERROR] duckroll command: {type(exc).__name__} - {exc}")
            traceback.print_exc()

    @commands.hybrid_command(name="lottery", description="Join the lottery.")
    @blacklist_barrier()
    @xp_earn(10, 20)
    async def lottery(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        now = datetime.now(timezone.utc)

        last_time = data.get("last_lottery")
        if last_time:
            last_dt = datetime.fromisoformat(last_time)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now - last_dt < timedelta(hours=1):
                rem = timedelta(hours=1) - (now - last_dt)
                return await ctx.send(
                    f"🕒 You can try the lottery again in {rem.seconds // 60}m "
                    f"{rem.seconds % 60}s."
                )

        ticket_price = 300
        jackpot = random.randint(15000, 20000)
        base_chance = 0.05

        if data["wallet"] < ticket_price:
            return await ctx.send(
                "🎟️ You need at least 300 coins to buy a lottery ticket."
            )

        inventory = data.get("inventory", [])
        luck_boost = 1.0
        for index, item in enumerate(inventory):
            if isinstance(item, dict) and item.get("_id") == "pet_duck":
                luck_boost = 1.3
                item["uses_left"] -= 1
                await ctx.send("🦆 Your Pet Duck boosted your lottery luck by 30%!")
                if item["uses_left"] <= 0:
                    inventory.pop(index)
                    await ctx.send(
                        "💔 One of your Pet Ducks has left after 3 uses."
                    )
                break

        chance = base_chance * luck_boost
        data["wallet"] -= ticket_price
        await economy_col.update_one(
            {"_id": user_id}, {"$set": {"wallet": data["wallet"]}}
        )

        if random.random() <= chance:
            await add_balance(ctx.author.id, ctx.guild.id, jackpot)
            await ctx.send(f"🎉 You hit the jackpot and won **{jackpot} coins**!")
        else:
            await ctx.send("😢 No luck this time. Better luck next draw!")

        data["inventory"] = inventory
        await economy_col.update_one(
            {"_id": user_id},
            {"$set": {"inventory": inventory, "last_lottery": now.isoformat()}},
        )

    @lottery.error
    async def lottery_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            rem = timedelta(seconds=error.retry_after)
            await send_hybrid_error(
                ctx,
                content=f"🕒 Try again in {rem.seconds // 60}m {rem.seconds % 60}s.",
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GamesCog(bot))
