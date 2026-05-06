"""Economy admin commands: addmoney, removemoney, drop."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.config.constants import ECONOMY_ADMIN_IDS
from bot.database import drop_instances_col, drops_col, economy_col
from bot.utils.channels import check_channel
from bot.utils.checks import staffperm, xp_earn
from bot.utils.economy import get_user
from bot.utils.errors import send_hybrid_error
from bot.utils.logging import log_action
from bot.utils.time_parsing import parse_amount
from bot.views.drops import DropClaimView


def _is_authorized(ctx: commands.Context) -> bool:
    return ctx.author.id in ECONOMY_ADMIN_IDS or ctx.author.id == ctx.guild.owner_id


class MoneyAdminCog(commands.Cog, name="MoneyAdmin"):
    """Owner-only commands that mutate user balances directly."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="addmoney",
        description="Add money to a user (economy admin only).",
    )
    @app_commands.describe(
        amount="Amount to add (supports k, m, b suffixes)",
        user="User to give money to",
    )
    @staffperm("economy")
    @xp_earn(4, 8)
    async def addmoney(
        self,
        ctx: commands.Context,
        amount: str,
        user: discord.Member,
    ) -> None:
        if not _is_authorized(ctx):
            return await ctx.send("❌ You are not authorized to use this command.")

        coins = parse_amount(amount)
        if coins is None or coins <= 0:
            return await ctx.send(
                "❌ Invalid amount.\n"
                "Use formats like: `100`, `4k`, `2m`, `1.5mil`, `123,456`"
            )

        user_data = await get_user(ctx, ctx.guild.id, user.id)
        new_bank = user_data.get("bank", 0) + coins
        await economy_col.update_one(
            {"_id": f"{ctx.guild.id}-{user.id}"},
            {"$set": {"bank": new_bank}},
        )
        await log_action(
            ctx,
            f"Added 🪙 {coins:,} to {user.mention}'s bank.",
            user_id=user.id,
            action_type="AddMoney",
        )
        await ctx.send(
            f"✅ Added 🪙 {coins:,} to {user.mention} (New bank: {new_bank:,})"
        )

    @addmoney.error
    async def addmoney_error(self, ctx: commands.Context, error) -> None:
        try:
            if isinstance(error, commands.CheckFailure):
                return
            from bot.prefix import get_prefix

            prefix = await get_prefix(self.bot, ctx.message)
            if isinstance(error, commands.BadArgument):
                return await send_hybrid_error(
                    ctx,
                    content=(
                        f"❌ Invalid arguments. Usage: `{prefix}addmoney <amount> @user`\n"
                        f"Example: `{prefix}addmoney 100 @User`"
                    ),
                )
            if isinstance(error, commands.MissingRequiredArgument):
                return await send_hybrid_error(
                    ctx,
                    content=(
                        f"❌ Missing arguments. Usage: `{prefix}addmoney <amount> @user`\n"
                        f"Example: `{prefix}addmoney 100 @User`"
                    ),
                )
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ Error running addmoney: "
                    f"`{type(error).__name__}: {error}`"
                ),
            )
        except Exception:
            pass

    @commands.hybrid_command(
        name="removemoney",
        description="Remove money from a user (economy admin only).",
    )
    @app_commands.describe(
        amount="Amount to remove (supports k, m, b suffixes)",
        user="User to take money from",
    )
    @staffperm("economy")
    @xp_earn(4, 8)
    async def removemoney(
        self,
        ctx: commands.Context,
        amount: str,
        user: discord.Member,
    ) -> None:
        # The original code excluded one of the broader admin IDs from removemoney;
        # we keep that exclusion explicit here.
        if (
            ctx.author.id not in ECONOMY_ADMIN_IDS
            and ctx.author.id != ctx.guild.owner_id
        ):
            return await ctx.send("❌ You are not authorized to use this command.")

        coins = parse_amount(amount)
        if coins is None or coins <= 0:
            return await ctx.send(
                "❌ Invalid amount.\n"
                "Use formats like: `100`, `4k`, `2m`, `1.5mil`, `123,456`"
            )

        user_data = await get_user(ctx, ctx.guild.id, user.id)
        wallet = user_data.get("wallet", 0)
        bank = user_data.get("bank", 0)
        total = wallet + bank

        if total < coins:
            return await ctx.send(f"❌ {user.mention} does not have enough funds.")

        if wallet >= coins:
            new_wallet = wallet - coins
            new_bank = bank
        else:
            new_wallet = 0
            new_bank = bank - (coins - wallet)

        await economy_col.update_one(
            {"_id": f"{ctx.guild.id}-{user.id}"},
            {"$set": {"wallet": new_wallet, "bank": new_bank}},
        )
        await log_action(
            ctx,
            f"Removed 🪙 {coins:,} from {user.mention}'s balance.",
            user_id=user.id,
            action_type="RemoveMoney",
        )
        await ctx.send(
            f"✅ Removed 🪙 {coins:,} from {user.mention} — "
            f"Wallet: {new_wallet:,} | Bank: {new_bank:,}"
        )

    @commands.hybrid_command(
        name="drop",
        description="Create a money drop (staff spawns money, members pay).",
    )
    @app_commands.describe(
        amount="Amount to drop", message="Optional message to include"
    )
    @xp_earn(8, 16)
    async def drop(
        self,
        ctx: commands.Context,
        amount: str,
        *,
        message: str | None = None,
    ) -> None:
        if not ctx.guild:
            return await ctx.send("❌ This command can only be used in a server.")

        guild_id = ctx.guild.id
        user_id = ctx.author.id

        coins = parse_amount(amount)
        if coins is None or coins <= 0:
            return await ctx.send(
                "❌ Invalid amount.\n"
                "Use formats like: `100`, `4k`, `2m`, `1.5mil`"
            )

        is_staff = False
        try:
            is_staff = await staffperm("money_drop").predicate(ctx)
        except Exception:
            is_staff = False

        if not is_staff:
            ok = await check_channel(ctx, "DROP_CHANNELS", "Drop")
            if not ok:
                return

        if not is_staff:
            try:
                data = await get_user(ctx, guild_id, user_id)
                wallet = int(data.get("wallet", 0))
                bank = int(data.get("bank", 0))
                if bank >= coins:
                    new_bank = bank - coins
                    new_wallet = wallet
                elif bank + wallet >= coins:
                    take_from_wallet = coins - bank
                    new_bank = 0
                    new_wallet = wallet - take_from_wallet
                else:
                    total = wallet + bank
                    return await ctx.send(
                        "❌ You don’t have enough money.\n"
                        f"🏦 Bank: **{bank:,}** | 🪙 Wallet: **{wallet:,}**\n"
                        f"🪙 Required: **{coins:,}** (Total: {total:,})"
                    )
                await economy_col.update_one(
                    {"_id": f"{guild_id}-{user_id}"},
                    {"$set": {"wallet": new_wallet, "bank": new_bank}},
                    upsert=True,
                )
            except Exception:
                return await ctx.send(
                    "⚠️ Failed to process your balance.\nPlease try again later."
                )

        role_id = None
        if is_staff:
            try:
                settings = await drops_col.find_one({"_id": guild_id})
                role_id = settings.get("role_id") if settings else None
            except Exception:
                role_id = None

        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = discord.Embed(
            title="💰 Money Drop!",
            description=(
                f"Someone dropped **🪙 {coins:,}**!\n\n"
                "Click the button below to claim it!"
            ),
            color=discord.Color.gold(),
        )
        if message:
            embed.add_field(name="💬 Message", value=message, inline=False)
        embed.set_footer(
            text=f"Dropped by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )

        view = DropClaimView()
        role_ping = f"<@&{role_id}>" if (is_staff and role_id) else ""

        try:
            msg = await ctx.send(content=role_ping, embed=embed, view=view)
        except Exception:
            if not is_staff:
                await economy_col.update_one(
                    {"_id": f"{guild_id}-{user_id}"},
                    {"$inc": {"wallet": coins}},
                    upsert=True,
                )
            return await ctx.send(
                "❌ Failed to send the drop message. You have been refunded."
            )

        try:
            await drop_instances_col.update_one(
                {"message_id": str(msg.id)},
                {
                    "$set": {
                        "message_id": str(msg.id),
                        "channel_id": str(ctx.channel.id),
                        "guild_id": str(guild_id),
                        "amount": int(coins),
                        "author_id": str(user_id),
                        "claimed": False,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "staff_drop": is_staff,
                    }
                },
                upsert=True,
            )
        except Exception:
            pass

    @drop.error
    async def drop_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await send_hybrid_error(
                ctx,
                content=(
                    "❌ Missing arguments!\n"
                    "**Usage:** `.drop <amount> [message]`\n"
                    "**Example:** `.drop 5000 Enjoy the coins!`"
                ),
            )
        elif isinstance(error, commands.BadArgument):
            await send_hybrid_error(
                ctx,
                content=(
                    "❌ Invalid argument.\n"
                    "Use formats like: `100`, `4k`, `2m`, `1.5mil`"
                ),
            )
        elif isinstance(error, commands.CommandInvokeError):
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ Something went wrong while running this command.\n"
                    "Please try again later."
                ),
            )
        else:
            await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ An unexpected error occurred.\n"
                    "Please contact an administrator."
                ),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MoneyAdminCog(bot))
