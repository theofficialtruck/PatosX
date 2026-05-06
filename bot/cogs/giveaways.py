"""``/giveaway``, ``/draw`` and ``/reroll``."""

from __future__ import annotations

import random
import traceback

from discord.ext import commands

from bot.database import giveaway_col
from bot.utils.checks import staff_only, staffperm
from bot.utils.errors import send_hybrid_error
from bot.views.giveaways import GiveawayModal


class GiveawaysCog(commands.Cog, name="Giveaways"):
    """Create giveaways via a modal and pick winners."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="giveaway",
        description="Create a giveaway using a form. Staff-only.",
    )
    @staffperm("giveaways")
    @staff_only()
    async def giveaway(self, ctx: commands.Context) -> None:
        if ctx.interaction is None:
            return await send_hybrid_error(
                ctx,
                content=(
                    "⚠️ This command requires the slash variant `/giveaway` "
                    "so the form can open."
                ),
            )
        await ctx.interaction.response.send_modal(GiveawayModal())

    @giveaway.error
    async def giveaway_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_hybrid_error(
                ctx, content="❌ You don't have permission to create giveaways."
            )
        elif isinstance(error, commands.BadArgument):
            await send_hybrid_error(
                ctx, content="❌ Invalid input. Please check your command format."
            )
        else:
            traceback.print_exc()
            await send_hybrid_error(
                ctx,
                content="⚠️ An unexpected error occurred while processing the giveaway.",
            )

    @commands.hybrid_command(
        name="reroll",
        description="Pick new winners for a past giveaway.",
    )
    @staffperm("giveaways")
    @staff_only()
    async def reroll(
        self, ctx: commands.Context, message_id: int
    ) -> None:
        data = await giveaway_col.find_one(
            {"message_id": message_id, "ended": True}
        )
        if not data:
            return await ctx.send(
                "❌ Giveaway not found or hasn't ended yet.", ephemeral=True
            )

        participants = data.get("participants", {})
        if isinstance(participants, list):
            participants = {}
        if not participants:
            return await ctx.send(
                "😔 No participants in that giveaway.", ephemeral=True
            )

        ticket_pool: list[int] = []
        for uid, entries in participants.items():
            ticket_pool.extend([int(uid)] * entries)
        if not ticket_pool:
            return await ctx.send(
                "😔 No valid tickets found.", ephemeral=True
            )

        winners: list[int] = []
        while len(winners) < data["winners_count"] and ticket_pool:
            pick = random.choice(ticket_pool)
            if pick not in winners:
                winners.append(pick)

        winners_mentions = ", ".join(f"<@{wid}>" for wid in winners)
        await giveaway_col.update_one(
            {"_id": data["_id"]}, {"$set": {"winners": winners}}
        )

        try:
            channel = ctx.bot.get_channel(data["channel_id"])
            message = await channel.fetch_message(data["message_id"])
            embed = message.embeds[0]
            for index, field in enumerate(embed.fields):
                if field.name == "Winners":
                    embed.set_field_at(
                        index,
                        name="Winners",
                        value=winners_mentions,
                        inline=False,
                    )
                    break
            await message.edit(embed=embed)
            await channel.send(
                f"🔄 **Reroll!** New winners for **{data['prize']}**: "
                f"{winners_mentions}"
            )
        except Exception as exc:
            return await ctx.send(
                f"⚠️ Winners updated in database, but failed to update message: {exc}",
                ephemeral=True,
            )

        await ctx.send(
            "✅ Reroll complete and announced in the giveaway's channel.",
            ephemeral=True,
        )

    @reroll.error
    async def reroll_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_hybrid_error(
                ctx, content="❌ You don't have permission to reroll giveaways."
            )
        elif isinstance(error, commands.BadArgument):
            await send_hybrid_error(ctx, content="❌ Invalid message ID.")
        else:
            traceback.print_exc()
            await send_hybrid_error(
                ctx,
                content="⚠️ An unexpected error occurred while rerolling.",
            )

    @commands.hybrid_command(
        name="draw",
        description="Instantly draw winners from a giveaway using its message ID. Staff-only.",
    )
    @staffperm("giveaways")
    @staff_only()
    async def draw(self, ctx: commands.Context, message_id: int) -> None:
        data = await giveaway_col.find_one({"message_id": message_id})
        if not data:
            return await ctx.send("❌ Giveaway not found.", ephemeral=True)

        participants = data.get("participants", {})
        if isinstance(participants, list):
            participants = {}
        if not participants:
            return await ctx.send(
                "😔 No participants in that giveaway.", ephemeral=True
            )

        ticket_pool: list[int] = []
        for uid, entries in participants.items():
            ticket_pool.extend([int(uid)] * entries)
        if not ticket_pool:
            return await ctx.send(
                "😔 No valid tickets found.", ephemeral=True
            )

        winners: list[int] = []
        while len(winners) < data["winners_count"] and ticket_pool:
            pick = random.choice(ticket_pool)
            if pick not in winners:
                winners.append(pick)

        winners_mentions = ", ".join(f"<@{wid}>" for wid in winners)
        await giveaway_col.update_one(
            {"_id": data["_id"]},
            {"$set": {"winners": winners, "ended": True}},
        )

        try:
            channel = ctx.bot.get_channel(data["channel_id"])
            message = await channel.fetch_message(data["message_id"])
            embed = message.embeds[0]
            for index, field in enumerate(embed.fields):
                if field.name == "Winners":
                    embed.set_field_at(
                        index,
                        name="Winners",
                        value=winners_mentions,
                        inline=False,
                    )
                    break
            await message.edit(embed=embed)
            await channel.send(
                f"🎉 **Giveaway Ended!** Winners for **{data['prize']}**: "
                f"{winners_mentions}"
            )
        except Exception as exc:
            return await ctx.send(
                f"⚠️ Winners drawn but failed to update message: {exc}",
                ephemeral=True,
            )

        await ctx.send("✅ Winners drawn and giveaway ended.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GiveawaysCog(bot))
