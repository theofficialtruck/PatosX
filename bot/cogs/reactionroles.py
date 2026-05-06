"""``/reactionrole`` setup command (the listener lives in events/reactions.py)."""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database import reaction_col
from bot.utils.checks import staff_only, staffperm


class ReactionRolesCog(commands.Cog, name="ReactionRoles"):
    """Configure a reaction role; the actual grant happens in event handlers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="reactionrole",
        description="Set up a reaction role. Staff-only.",
    )
    @staffperm("reactionroles")
    @staff_only()
    async def reactionrole(
        self,
        ctx: commands.Context,
        message_id: int,
        emoji: str,
        role: discord.Role,
    ) -> None:
        try:
            msg = await ctx.channel.fetch_message(message_id)
            await msg.add_reaction(emoji)
            await reaction_col.update_one(
                {"message": message_id},
                {"$set": {"emoji": str(emoji), "role": role.id}},
                upsert=True,
            )
            await ctx.send(
                f"✅ Reaction role set: {emoji} will grant {role.mention}."
            )
        except Exception as exc:
            print(f"[reactionrole error] {exc}")
            await ctx.send(
                "❌ Could not set reaction role. Check your permissions and message ID."
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRolesCog(bot))
