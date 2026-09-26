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

"""Sticky notes: stickynote/unstickynote commands and a timer loop that re-posts every sticky note."""

import asyncio

import discord
from discord.ext import commands, tasks

from core import permsHelperFuncs as perms
from core import state

# How often every sticky note is deleted and posted again at the bottom of its channel
STICKY_REPOST_MINUTES = 5


async def find_sticky_note_doc(guild_id: int, channel_id: int):
    """Fetch the sticky note document for a channel, tolerating mixed int and string field storage."""
    guild_str = str(guild_id)
    channel_str = str(channel_id)
    return await state.sticky_col.find_one(
        {
            "$or": [
                {"guild": guild_str, "channel": channel_str},
                {"guild": guild_id, "channel": channel_id},
                {"guild": guild_str, "channel": channel_id},
                {"guild": guild_id, "channel": channel_str},
            ]
        }
    )


async def delete_sticky_message(channel, message_id) -> None:
    """Delete a previously posted sticky message. A missing or unusable id is fine (nothing to delete);
    Forbidden and other HTTP errors propagate so the caller can decide what to tell the user."""
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return
    try:
        await channel.get_partial_message(message_id).delete()
    except discord.NotFound:
        pass


async def repost_sticky_note(channel, doc) -> None:
    """Post the sticky note's text again, remember the new message and delete the previous one.
    The new message goes out first so a failed post never leaves the channel without a sticky."""
    sent = await channel.send(doc["text"])
    await state.sticky_col.update_one(
        {"_id": doc["_id"]},
        {"$set": {"guild": str(channel.guild.id), "channel": str(channel.id), "message": sent.id}},
    )
    try:
        await delete_sticky_message(channel, doc.get("message"))
    except discord.HTTPException as e:
        print(f"[Sticky Notes] Couldn't delete old sticky message {doc.get('message')}: {e}")


class StickyNotes(commands.Cog):
    """Sticky notes: stickynote/unstickynote commands and a timer loop that re-posts every sticky note."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    @tasks.loop(minutes=STICKY_REPOST_MINUTES)
    async def repost_stickies(self):
        """Re-post every sticky note. Deliberately dumb: no message tracking or deletion detection,
        so a missed event or a deleted message can never leave a sticky note stuck missing. Each
        note is handled on its own so one broken channel can't stop the rest (or the loop)."""
        try:
            docs = [doc async for doc in state.sticky_col.find({})]
        except Exception as e:
            print(f"[Sticky Notes] Couldn't load sticky notes: {e}")
            return
        for doc in docs:
            try:
                guild = self.bot.get_guild(int(doc["guild"]))
                channel = guild.get_channel(int(doc["channel"])) if guild else None
                if channel is None or not doc.get("text"):
                    continue
                await repost_sticky_note(channel, doc)
            except Exception as e:
                print(f"[Sticky Notes] Failed to repost sticky note {doc.get('guild')}-{doc.get('channel')}: {e}")

    @repost_stickies.before_loop
    async def before_repost_stickies(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="stickynote", description="Set a sticky note in this channel. Staff only.")
    @perms.staffperm("stickynotes")
    @perms.staff_only()
    async def stickynote(self, ctx):
        await ctx.send("📝 Please type the message to pin as sticky:")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=60)
            doc = await find_sticky_note_doc(ctx.guild.id, ctx.channel.id)
            if doc:
                try:
                    await delete_sticky_message(ctx.channel, doc.get("message"))
                except discord.HTTPException as e:
                    print(f"[stickynote] Couldn't delete previous message {doc.get('message')}: {e}")
            sent = await ctx.send(reply.content)
            query = {"_id": doc["_id"]} if doc else {"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)}
            await state.sticky_col.update_one(
                query,
                {
                    "$set": {
                        "guild": str(ctx.guild.id),
                        "channel": str(ctx.channel.id),
                        "text": reply.content,
                        "message": sent.id,
                    }
                },
                upsert=True,
            )
            await ctx.send(f"✅ Sticky note created. It will be re-posted every {STICKY_REPOST_MINUTES} minutes.")
        except asyncio.TimeoutError:
            await ctx.send("❌ Timeout. Sticky note creation cancelled.")

    @commands.hybrid_command(name="unstickynote", description="Remove the sticky note. Staff only.")
    @perms.staffperm("stickynotes")
    @perms.staff_only()
    async def unstickynote(self, ctx):
        doc = await find_sticky_note_doc(ctx.guild.id, ctx.channel.id)
        if not doc:
            await ctx.send("⚠️ No sticky note set for this channel.")
            return
        try:
            await delete_sticky_message(ctx.channel, doc.get("message"))
        except discord.Forbidden:
            print(f"[unstickynote] No permission to delete message {doc.get('message')}")
            await ctx.send("❌ I don't have permission to delete the sticky message.")
        except Exception as e:
            print(f"[unstickynote error] {e}")
            await ctx.send("❌ Could not remove stickynote.")
            return
        await state.sticky_col.delete_one({"_id": doc["_id"]})
        await ctx.send("✅ Sticky note removed.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the repost loop once (on_ready can fire again after a reconnect). The loop's first
        run happens immediately, which also refreshes every sticky note after a restart."""
        if self._ready_done:
            return
        self._ready_done = True
        if not self.repost_stickies.is_running():
            self.repost_stickies.start()

    def cog_unload(self):
        """Cancel the repost loop this cog owns."""
        if self.repost_stickies.is_running():
            self.repost_stickies.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StickyNotes(bot))
