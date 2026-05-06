"""Investment commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, Select, View

from bot.config.constants import INVESTMENT_COMPANIES
from bot.database import economy_col, investments_col
from bot.utils.channels import check_channel_setting as check_channel
from bot.utils.checks import blacklist_barrier, staff_only, staffperm, xp_earn
from bot.utils.economy import get_user, subtract_balance
from bot.utils.investments import (
    backfill_investment_dates_from_timestamp,
    calculate_investment_value,
    create_investment,
    get_investment_date,
)


class InvestmentsCog(commands.Cog, name="Investments"):
    """Buy fake-company shares and watch them appreciate over time."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="invest",
        description="Invest in fake companies for profit.",
    )
    @app_commands.describe(
        company="Company to invest in (e.g., 'Techify', 'MineCorp', 'Oceanic')",
        amount="Amount to invest (number or 'all')",
    )
    @blacklist_barrier()
    @xp_earn(16, 30)
    async def invest(
        self,
        ctx: commands.Context,
        company: str | None = None,
        amount: str | None = None,
    ) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        companies = INVESTMENT_COMPANIES

        if company and amount:
            company = company.title()
            if company not in companies:
                return await ctx.send(
                    f"❌ Invalid company! Available: {', '.join(companies.keys())}"
                )

            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data.get("wallet", 0)

            if amount.lower() == "all":
                invest_amount = wallet
            else:
                try:
                    invest_amount = int(amount)
                except ValueError:
                    return await ctx.send(
                        "❌ Invalid amount! Use a number or 'all'."
                    )

            stats = companies[company]
            if invest_amount < stats["min"]:
                return await ctx.send(
                    f"❌ Minimum investment for {company} is {stats['min']} coins!"
                )
            if invest_amount > stats["max"]:
                return await ctx.send(
                    f"❌ Maximum investment for {company} is {stats['max']} coins!"
                )
            if invest_amount > wallet:
                return await ctx.send("❌ You don't have enough coins!")

            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            await create_investment(user_id, company, invest_amount)
            await subtract_balance(ctx.author.id, ctx.guild.id, invest_amount)
            await ctx.send(f"📈 Invested {invest_amount} coins in {company}!")
            return

        embed = discord.Embed(
            title="📈 Investment Opportunities",
            description="Choose a company to invest in!",
            color=discord.Color.green(),
        )
        for name, stats in companies.items():
            embed.add_field(
                name=name,
                value=f"Investment Range: {stats['min']} - {stats['max']} coins",
                inline=False,
            )
        embed.set_footer(
            text="Unofficial Analyst Ranking: Techify ⭐⭐⭐ > MineCorp ⭐⭐ > Oceanic ⭐"
        )

        view = View()
        for company_name, stats in companies.items():
            button = Button(
                label=company_name,
                style=discord.ButtonStyle.green,
                custom_id=f"invest_{company_name}",
            )
            button.callback = self._make_company_callback(ctx, company_name, stats)
            view.add_item(button)

        await ctx.send(embed=embed, view=view)

    def _make_company_callback(
        self, ctx: commands.Context, company: str, stats: dict[str, int]
    ):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user != ctx.author:
                return await interaction.response.send_message(
                    "❌ Not your investment.", ephemeral=True
                )

            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data.get("wallet", 0)
            user_investments = await investments_col.count_documents(
                {"user_id": user_id}
            )

            if user_investments >= 5:
                return await interaction.response.send_message(
                    "❌ You can only have up to **5 active investments** at a "
                    "time. Sell some before investing again.",
                    ephemeral=True,
                )

            step = 500
            amounts = list(range(stats["min"], stats["max"] + step, step))
            options = [
                discord.SelectOption(label=f"{amt} coins", value=str(amt))
                for amt in amounts
            ]
            select = Select(
                placeholder=f"Choose amount to invest in {company}",
                options=options,
            )

            async def select_callback(inter: discord.Interaction) -> None:
                if inter.user != ctx.author:
                    return await inter.response.send_message(
                        "❌ Not your selection.", ephemeral=True
                    )

                invest_amount = int(select.values[0])
                if wallet < invest_amount:
                    return await inter.response.send_message(
                        f"❌ You only have {wallet} coins but tried to invest "
                        f"{invest_amount}.",
                        ephemeral=True,
                    )

                new_wallet = wallet - invest_amount
                await economy_col.update_one(
                    {"_id": user_id},
                    {"$set": {"wallet": new_wallet}},
                    upsert=True,
                )
                await create_investment(user_id, company, invest_amount)
                await inter.response.send_message(
                    f"✅ You invested **{invest_amount} coins** in **{company}**."
                )

            select.callback = select_callback
            view = View()
            view.add_item(select)
            await interaction.response.send_message(
                f"💰 Choose how much to invest in **{company}**:",
                view=view,
                ephemeral=True,
            )

        return callback

    @commands.hybrid_command(
        name="investstatus",
        description="Check your investments.",
    )
    @blacklist_barrier()
    @xp_earn(4, 8)
    async def investstatus(self, ctx: commands.Context) -> None:
        if not await check_channel(ctx, "economy_channel", "Economy"):
            return

        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        investments = await investments_col.find({"user_id": user_id}).to_list(length=None)

        if not investments:
            return await ctx.send("❌ You don’t have any active investments.")

        embed = discord.Embed(
            title=f"📊 {ctx.author.display_name}'s Investments",
            color=discord.Color.blue(),
        )

        for inv in investments:
            company = inv["company"]
            amount = inv["amount"]
            current_value = await calculate_investment_value(inv)
            inv_id = inv["_id"]
            date_obj = get_investment_date(inv)
            unix_timestamp = int(date_obj.timestamp())

            embed.add_field(
                name=f"{company} (ID: {inv_id})",
                value=(
                    f"Invested: 🪙 {amount}\n"
                    f"Current Value: 🪙 {current_value}\n"
                    f"Date: <t:{unix_timestamp}:F>"
                ),
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="investmigrate",
        description="Backfill missing investment dates from legacy timestamps.",
    )
    @staffperm("economy")
    @staff_only()
    async def investmigrate(self, ctx: commands.Context) -> None:
        stats = await backfill_investment_dates_from_timestamp()
        await ctx.send(
            "✅ Investment date backfill completed.\n"
            f"Scanned: `{stats['scanned']}`\n"
            f"Updated: `{stats['updated']}`\n"
            f"Invalid timestamp: `{stats['invalid_timestamp']}`\n"
            f"Skipped (conflict/already set): `{stats['skipped_conflict']}`\n"
            f"Write errors: `{stats['write_errors']}`"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InvestmentsCog(bot))
