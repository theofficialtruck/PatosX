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

"""Sticky notes: stickynote/unstickynote commands, repost-on-message, and the repost-if-deleted loop."""

import asyncio
from collections import defaultdict

import discord
from discord.ext import commands, tasks

from core import config as cfg
from core import permsHelperFuncs as perms
from core import state

# Tracks when each channel last had a sticky note reposted, to avoid excessive API calls
last_sticky_trigger = defaultdict(float)


# Maps channel_id to the most recently posted sticky message ID
last_sticky_msg = {}


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


class StickyNotes(commands.Cog):
    """Sticky notes: stickynote/unstickynote commands, repost-on-message, and the repost-if-deleted loop."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    async def load_sticky_messages(self):
        """Populate last_sticky_msg from the database at startup so existing stickies are tracked."""
        try:
            cursor = state.sticky_col.find({})
            async for doc in cursor:
                if "message" in doc:
                    channel_key = int(doc["channel"])
                    last_sticky_msg[channel_key] = int(doc["message"])
            print(f"[Sticky Notes] Loaded {len(last_sticky_msg)} sticky message IDs from database")
        except Exception as e:
            print(f"[Sticky Notes] Error loading sticky messages: {e}")

    @tasks.loop(minutes=2)
    async def check_and_repost_stickies(self):
        """Scan all sticky note configurations and repost any whose stored message has been deleted."""
        try:
            cursor = state.sticky_col.find({})
            async for doc in cursor:
                guild_id = doc["guild"]
                channel_id = int(doc["channel"])
                sticky_text = doc["text"]
                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    continue
                channel = guild.get_channel(channel_id)
                if not channel:
                    continue
                stored_message_id = doc.get("message")
                message_exists = False
                if stored_message_id:
                    try:
                        await channel.fetch_message(stored_message_id)
                        message_exists = True
                    except discord.NotFound:
                        print(f"[Sticky Notes] Message {stored_message_id} not found, reposting...")
                    except discord.Forbidden:
                        print(f"[Sticky Notes] No permission to check message {stored_message_id}")
                        continue
                    except Exception as e:
                        print(f"[Sticky Notes] Error checking message {stored_message_id}: {e}")
                        continue
                if not message_exists:
                    try:
                        sent = await channel.send(sticky_text)
                        last_sticky_msg[channel_id] = sent.id
                        await state.sticky_col.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"guild": str(guild_id), "channel": str(channel_id), "message": sent.id}},
                        )
                        print(f"[Sticky Notes] Reposted sticky note in channel {channel_id}")
                    except Exception as e:
                        print(f"[Sticky Notes] Failed to repost sticky note: {e}")
        except Exception as e:
            print(f"[Sticky Notes] Error in check_and_repost_stickies: {e}")

    async def load_sticky_notes(self):
        """Re post all sticky notes at startup, replacing old messages with fresh ones so they appear
        at the bottom of each channel even if messages accumulated while the bot was offline."""
        print("📝 Loading sticky notes...")
        loaded_count = 0
        async for doc in state.sticky_col.find({}):
            try:
                guild = self.bot.get_guild(int(doc["guild"]))
                if not guild:
                    continue
                channel = guild.get_channel(int(doc["channel"]))
                if not channel:
                    continue
                try:
                    existing_msg = await channel.fetch_message(doc["message"])
                    await existing_msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
                new_msg = await channel.send(doc["text"])
                await state.sticky_col.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"guild": str(doc["guild"]), "channel": str(doc["channel"]), "message": new_msg.id}},
                )
                last_sticky_msg[int(doc["channel"])] = new_msg.id
                loaded_count += 1
            except Exception as e:
                print(f"❌ Failed to load sticky note for {doc['guild']}-{doc['channel']}: {e}")
        print(f"✅ Loaded {loaded_count} sticky notes")

    async def repost_sticky_note(self, channel_id, guild_id):
        """Delete the previous sticky note message and post a fresh one so it remains at the bottom."""
        doc = await find_sticky_note_doc(int(guild_id), int(channel_id))
        if not doc:
            return
        try:
            guild = self.bot.get_guild(int(guild_id))
            channel = guild.get_channel(int(channel_id))
            try:
                old_msg = await channel.fetch_message(doc["message"])
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            new_msg = await channel.send(doc["text"])
            await state.sticky_col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"guild": str(guild_id), "channel": str(channel_id), "message": new_msg.id}},
            )
            last_sticky_msg[int(channel_id)] = new_msg.id
        except Exception as e:
            print(f"❌ Failed to repost sticky note: {e}")

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
                    old_msg = await ctx.channel.fetch_message(int(doc["message"]))
                    await old_msg.delete()
                except discord.NotFound:
                    print(f"[stickynote] Previous message {doc['message']} not found, creating new one")
                except discord.Forbidden:
                    print(f"[stickynote] No permission to delete message {doc['message']}")
                except Exception as e:
                    print(f"[stickynote delete error] {e}")
            sent = await ctx.send(reply.content)
            if doc:
                await state.sticky_col.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "guild": str(ctx.guild.id),
                            "channel": str(ctx.channel.id),
                            "text": reply.content,
                            "message": sent.id,
                        }
                    },
                )
            else:
                await state.sticky_col.update_one(
                    {"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)},
                    {"$set": {"text": reply.content, "message": sent.id}},
                    upsert=True,
                )
            last_sticky_msg[ctx.channel.id] = sent.id
            await ctx.send("✅ Sticky note created.")
        except asyncio.TimeoutError:
            await ctx.send("❌ Timeout. Sticky note creation cancelled.")

    @commands.hybrid_command(name="unstickynote", description="Remove the sticky note. Staff only.")
    @perms.staffperm("stickynotes")
    @perms.staff_only()
    async def unstickynote(self, ctx):
        doc = await find_sticky_note_doc(ctx.guild.id, ctx.channel.id)
        if doc:
            try:
                msg = await ctx.channel.fetch_message(int(doc["message"]))
                await msg.delete()
            except discord.NotFound:
                print(f"[unstickynote] Message {doc['message']} not found, removing from database")
            except discord.Forbidden:
                print(f"[unstickynote] No permission to delete message {doc['message']}")
                await ctx.send("❌ I don't have permission to delete the sticky message.")
            except Exception as e:
                print(f"[unstickynote error] {e}")
                await ctx.send("❌ Could not remove stickynote.")
                return
            await state.sticky_col.delete_one({"_id": doc["_id"]})
            last_sticky_msg.pop(ctx.channel.id, None)
            await ctx.send("✅ Sticky note removed.")
        else:
            await ctx.send("⚠️ No sticky note set for this channel.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Re-post every sticky note so it sits at the bottom of its channel, then start the
        repost-if-deleted loop (runs once; the loop only starts after the initial load so the two
        never race to post duplicates)."""
        if self._ready_done:
            return
        self._ready_done = True
        await self.load_sticky_notes()
        await self.load_sticky_messages()
        self.check_and_repost_stickies.start()

    @commands.Cog.listener()
    async def on_message(self, message):
        """Move the channel's sticky note back to the bottom after every new message."""
        if message.author.bot:
            return
        if not message.guild:
            return
        if message.type in cfg.BOOST_MESSAGE_TYPES:
            return
        try:
            doc = await find_sticky_note_doc(message.guild.id, message.channel.id)
            if doc:
                old_id = last_sticky_msg.get(message.channel.id) or doc.get("message")
                if old_id:
                    try:
                        old = await message.channel.fetch_message(int(old_id))
                        await old.delete()
                    except discord.NotFound:
                        print(f"[sticky note] Previous message {old_id} not found, creating new one")
                    except discord.Forbidden:
                        print(f"[sticky note] No permission to delete message {old_id}")
                    except Exception as e:
                        print(f"[sticky note delete error] {e}")
                sent = await message.channel.send(doc["text"])
                last_sticky_msg[message.channel.id] = sent.id
                await state.sticky_col.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"guild": str(message.guild.id), "channel": str(message.channel.id), "message": sent.id}},
                )
        except Exception as e:
            print(f"[sticky note error] {e}")

    def cog_unload(self):
        """Cancel the repost loop this cog owns."""
        if self.check_and_repost_stickies.is_running():
            self.check_and_repost_stickies.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StickyNotes(bot))
