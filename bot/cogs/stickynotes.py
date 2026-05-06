"""``/stickynote`` and ``/unstickynote``."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from bot.database import sticky_col
from bot.utils.checks import staff_only, staffperm


class StickyNotesCog(commands.Cog, name="StickyNotes"):
    """Pin a self-reposting note in a channel."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="stickynote",
        description="Set a sticky note in this channel. Staff-only.",
    )
    @staffperm("stickynotes")
    @staff_only()
    async def stickynote(self, ctx: commands.Context) -> None:
        await ctx.send("📝 Please type the message to pin as sticky:")

        def check(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=60)

            doc = await sticky_col.find_one(
                {"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)}
            )
            if doc:
                try:
                    old_msg = await ctx.channel.fetch_message(doc["message"])
                    await old_msg.delete()
                except discord.NotFound:
                    print(
                        f"[stickynote] Previous message {doc['message']} not found, "
                        "creating new one"
                    )
                except discord.Forbidden:
                    print(
                        f"[stickynote] No permission to delete message {doc['message']}"
                    )
                except Exception as exc:
                    print(f"[stickynote delete error] {exc}")

            sent = await ctx.send(reply.content)
            await sticky_col.update_one(
                {"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)},
                {"$set": {"text": reply.content, "message": sent.id}},
                upsert=True,
            )
            await ctx.send("✅ Sticky note created.")
        except asyncio.TimeoutError:
            await ctx.send("❌ Timeout. Sticky note creation cancelled.")

    @commands.hybrid_command(
        name="unstickynote",
        description="Remove the sticky note. Staff-only.",
    )
    @staffperm("stickynotes")
    @staff_only()
    async def unstickynote(self, ctx: commands.Context) -> None:
        doc = await sticky_col.find_one(
            {"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)}
        )
        if not doc:
            return await ctx.send("⚠️ No sticky note set for this channel.")

        try:
            msg = await ctx.channel.fetch_message(doc["message"])
            await msg.delete()
        except discord.NotFound:
            print(
                f"[unstickynote] Message {doc['message']} not found, "
                "removing from database"
            )
        except discord.Forbidden:
            print(
                f"[unstickynote] No permission to delete message {doc['message']}"
            )
            await ctx.send("❌ I don't have permission to delete the sticky message.")
            return
        except Exception as exc:
            print(f"[unstickynote error] {exc}")
            await ctx.send("❌ Could not remove stickynote.")
            return

        await sticky_col.delete_one(
            {"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)}
        )
        await ctx.send("✅ Sticky note removed.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StickyNotesCog(bot))
