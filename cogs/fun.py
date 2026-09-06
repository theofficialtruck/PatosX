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

"""Fun commands (slap, duckfact, duck, quote, afk, quack counter) plus the AFK and quack on_message handlers."""

import asyncio
import json
import random
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from dateutil import parser
from discord import (
    app_commands,
)
from discord.ext import commands
from discord.ext.commands import BucketType, cooldown
from discord.ui import Button, View

from core import config as cfg
from core import permsHelperFuncs as perms
from core import state


class QuackTopView(View):
    def __init__(self, ctx, entries, per_page=10):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.entries = entries
        self.per_page = per_page
        self.page = 0
        self.max_page = (len(entries) - 1) // per_page
        self.user_id = str(ctx.author.id)
        self.user_rank = None
        for i, (uid, _) in enumerate(entries, start=1):
            if uid == self.user_id:
                self.user_rank = i
                break

    def get_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        description = ""
        for i, (user_id, count) in enumerate(self.entries[start:end], start=start + 1):
            member = self.ctx.guild.get_member(int(user_id))
            name = member.display_name if member else f"User ID {user_id}"
            description += f"**{i}. {name}** - {count} quacks\n"
        embed = discord.Embed(
            title=f"🦆 Quack Leaderboard (Page {self.page + 1}/{self.max_page + 1})",
            description=description,
            color=discord.Color.green(),
        )
        if self.user_rank:
            embed.set_footer(text=f"Your rank: #{self.user_rank}")
        else:
            embed.set_footer(text="You haven't quacked yet!")
        return embed

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.blurple)
    async def prev_page(self, interaction: discord.Interaction, button: Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        if self.page < self.max_page:
            self.page += 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)


# Resolved from this file rather than the process CWD so the bot works no matter where it is
# launched from (the heartbeat file in cogs/admin.py is located the same way).
DUCK_FACTS_FILE = Path(__file__).resolve().parent.parent / "data" / "duckfacts.txt"


def _read_duck_facts():
    with open(DUCK_FACTS_FILE, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


class Fun(commands.Cog):
    """Fun commands (slap, duckfact, duck, quote, afk, quack counter) plus the AFK and quack on_message handlers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="quackcount", description="Check the server's total quacks and a user's quacks.")
    async def quackcount(self, ctx, member: discord.Member | None = None):
        guild_id = str(ctx.guild.id)
        config = await state.config_col.find_one({"guild": guild_id})
        if not config or config.get("quack_count", 0) == 0:
            return await ctx.send("🦆 No quacks have been counted yet!")
        target = member or ctx.author
        user_id = str(target.id)
        user_quacks = config.get("quacks", {}).get(user_id, 0)
        total_quacks = config.get("quack_count", 0)
        label = "Your" if target.id == ctx.author.id else f"{target.display_name}'s"
        await ctx.send(f"🦆 **Server Quacks:** {total_quacks}\n🦆 **{label} Quacks:** {user_quacks}")

    @commands.hybrid_command(name="quacktop", description="View the top quackers in this server.")
    async def quacktop(self, ctx):
        guild_id = str(ctx.guild.id)
        config = await state.config_col.find_one({"guild": guild_id})
        if not config or not config.get("quacks"):
            return await ctx.send("🦆 No quacks have been counted yet!")
        top_quackers = sorted(config["quacks"].items(), key=lambda x: x[1], reverse=True)
        view = QuackTopView(ctx, top_quackers)
        await ctx.send(embed=view.get_embed(), view=view)

    @commands.hybrid_command(name="slap", description="Slap another user")
    @app_commands.describe(member="The user to slap (optional - will slap yourself if not provided)")
    @commands.cooldown(1, 5, commands.BucketType.member)
    @perms.blacklist_barrier()
    async def slap(self, ctx, member: discord.Member = None):
        if not member:
            await ctx.send("❌ You need to mention someone to slap!")
            return
        try:
            await ctx.defer()
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"https://api.giphy.com/v1/gifs/search?q=anime%20slap&api_key={cfg.GIPHY_API_KEY}&limit=20&rating=g&lang=en",
                    timeout=5,
                ) as r,
            ):
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                data = await r.json()
            # Giphy returns results under the "data" key; each item has an "images" map with various sizes.
            results = data.get("data", [])
            if not results:
                await ctx.send("❌ Couldn't find any slap GIFs right now.")
                return
            gif_item = random.choice(results)
            images = gif_item.get("images", {})
            gif_url = None
            # prefer original, then common fallbacks
            for key in ("original", "downsized", "fixed_height", "fixed_width"):
                img = images.get(key)
                if img and img.get("url"):
                    gif_url = img["url"]
                    break
            # fallback to top level urls
            if not gif_url:
                gif_url = gif_item.get("url") or gif_item.get("embed_url")
            if not gif_url:
                await ctx.send("❌ Couldn't find any slap GIFs right now.")
                return
            embed = discord.Embed(
                title="👋 Slap!",
                description=f"{ctx.author.mention} slapped {member.mention}! Ouch!",
                color=discord.Color.red(),
            )
            embed.set_image(url=gif_url)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"⚠️ Something went wrong while fetching the slap GIF: `{e}`")

    @commands.hybrid_command(name="duckfact", description="Get a random duck fact")
    @commands.cooldown(1, 5, commands.BucketType.member)
    @perms.blacklist_barrier()
    async def duckfact(self, ctx):
        try:
            facts = await asyncio.to_thread(_read_duck_facts)
            if not facts:
                raise ValueError("Duck facts file is empty.")
            fact = random.choice(facts)
            embed = discord.Embed(title="🦆 Duck Fact", description=fact, color=discord.Color.teal())
            embed.set_thumbnail(url="https://random-d.uk/api/v2/random")
            await ctx.send(embed=embed)
        except FileNotFoundError:
            await ctx.send("❌ Could not find `duckfacts.txt`. Please create it in the bot's `data` folder.")
        except Exception as e:
            print(f"[ERROR] duckfact command: {e}")
            traceback.print_exc()
            await ctx.send("⚠️ Something went wrong while fetching a duck fact.")

    @commands.hybrid_command(name="afk", description="Set your AFK status.")
    async def afk(self, ctx, *, reason="AFK"):
        afk_key = f"{ctx.guild.id}-{ctx.author.id}"
        await state.afk_col.update_one(
            {"_id": afk_key},
            {
                "$set": {
                    "reason": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "original_nick": ctx.author.nick,
                }
            },
            upsert=True,
        )
        if ctx.author.display_name.startswith("[AFK]"):
            await ctx.send(f"🛌 You are now AFK: {reason}", delete_after=7)
            return
        try:
            new_nick = f"[AFK] {ctx.author.display_name}"
            await ctx.author.edit(nick=new_nick)
        except discord.Forbidden:
            await ctx.send(
                "⚠️ I can't change your nickname (role hierarchy or missing permissions). AFK still set!", delete_after=5
            )
        except discord.HTTPException:
            await ctx.send("⚠️ Something went wrong while changing your nickname. AFK still set!")
        await ctx.send(f"🛌 You are now AFK: {reason}", delete_after=7)

    @commands.hybrid_command(name="duck", description="Random picture of a duck.")
    @cooldown(1, 5, BucketType.member)
    @perms.blacklist_barrier()
    async def duck(self, ctx):
        config = await state.config_col.find_one({"guild": str(ctx.guild.id)}) or {}
        allowed_channels = config.get("ALLOWED_DUCK_CHANNELS", [])
        if allowed_channels and ctx.channel.id not in allowed_channels:
            return await ctx.send("🚫 You can't use this command here.")
        async with aiohttp.ClientSession() as session, session.get("https://random-d.uk/api/random") as resp:
            if resp.status != 200:
                return await ctx.send("❌ Could not get a duck right now, try again later!")
            data = await resp.json()
            url = data.get("url")
            if not url:
                return await ctx.send("❌ Duck image not found, sorry!")
        embed = discord.Embed(title="🦆 Quack!", color=discord.Color.blue())
        embed.set_image(url=url)
        await ctx.send(embed=embed, ephemeral=False)

    @commands.hybrid_command(name="quote", description="Get a random quote.")
    @cooldown(1, 5, BucketType.member)
    @perms.blacklist_barrier()
    async def quote(self, ctx):
        api_url = "https://zenquotes.io/api/random"
        try:
            async with aiohttp.ClientSession() as session, session.get(api_url) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return await ctx.send(f"❌ Could not fetch a quote right now (Status {resp.status})")
                try:
                    data = json.loads(text)
                except Exception as e:
                    print(f"[JSON PARSE ERROR] {type(e).__name__} - {e}")
                    return await ctx.send(f"⚠️ API returned invalid data:\n```{text[:200]}...```")
            if not data or not isinstance(data, list):
                return await ctx.send("❌ Couldn't fetch a quote this time, try again!")
            quote_text = str(data[0].get("q") or "No quote found")
            author = str(data[0].get("a") or "Unknown")
            embed = discord.Embed(
                title="💬 Random Quote", description=f"“{quote_text}”\n\n- *{author}*", color=discord.Color.purple()
            )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send("⚠️ Something went wrong while fetching a quote. Please contact " + cfg.BOT_ADMIN_NAME + ".")
            print(f"[QUOTE ERROR] {type(e).__name__} - {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Count quacks in non-command messages, announce AFK users who get mentioned, and clear
        the author's own AFK status when they talk again."""
        if message.author.bot:
            return
        if not message.guild:
            return
        if message.type in cfg.BOOST_MESSAGE_TYPES:
            return
        try:
            guild_id = str(message.guild.id)
            content = getattr(message, "content", "") or ""
            ctx = await self.bot.get_context(message)
            if not ctx.valid:
                # ctx.valid means discord.py resolved this message to a real command
                # (e.g. `.quackcount`/`.quacktop`) - checking that directly instead of
                # re-implementing prefix matching here means we can't drift out of sync
                # with what actually counts as a command invocation.
                await state.config_col.update_one(
                    {"guild": guild_id},
                    {"$setOnInsert": {"quack_count": 0, "quacks": {}, "QUACK_CHANNELS": "all"}},
                    upsert=True,
                )
                config = await state.config_col.find_one({"guild": guild_id}) or {}
                quack_channels = config.get("QUACK_CHANNELS", [])
                counts_everywhere = quack_channels == "all" or quack_channels == []
                in_quack_channel = counts_everywhere or (
                    isinstance(quack_channels, list) and message.channel.id in quack_channels
                )
                occurrences = len(re.findall("\\bquack\\b", content.lower()))
                if in_quack_channel and occurrences > 0:
                    user_id = str(message.author.id)
                    await state.config_col.update_one(
                        {"guild": guild_id}, {"$inc": {"quack_count": occurrences, f"quacks.{user_id}": occurrences}}
                    )
        except Exception as e:
            print(f"[Quack Counter Error] {e}")
        try:
            for user in message.mentions:
                doc = await state.afk_col.find_one({"_id": f"{message.guild.id}-{user.id}"})
                if doc:
                    reason = doc.get("reason", "AFK")
                    timestamp = doc.get("timestamp")
                    if timestamp:
                        dt = parser.isoparse(timestamp)
                        elapsed = datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)
                        mins = int(elapsed.total_seconds() // 60)
                        hours, mins = divmod(mins, 60)
                        time_str = f"{hours}h {mins}m ago" if hours else f"{mins} minutes ago"
                        await message.channel.send(f"📨 {user.display_name} is AFK ({reason}) - set {time_str}.")
                    else:
                        await message.channel.send(f"📨 {user.display_name} is AFK: {reason}")
            content_lower = message.content.lower()
            if not content_lower.startswith((".afk", "/afk")):
                afk_key = f"{message.guild.id}-{message.author.id}"
                doc = await state.afk_col.find_one({"_id": afk_key})
                if doc:
                    await state.afk_col.delete_one({"_id": afk_key})
                    original_nick = doc.get("original_nick")
                    current_nick = message.author.display_name
                    try:
                        if current_nick.startswith("[AFK]"):
                            await message.author.edit(nick=original_nick)
                    except discord.Forbidden:
                        await message.channel.send(
                            "⚠️ I couldn't restore your nickname due to role hierarchy, but AFK is removed.",
                            delete_after=5,
                        )
                    except discord.HTTPException:
                        await message.channel.send(
                            "⚠️ Something went wrong while restoring your nickname, but AFK is removed."
                        )
                    await message.channel.send(
                        f"✅ Welcome back, {message.author.mention}! AFK removed.", delete_after=5
                    )
        except Exception as e:
            print(f"[afk error] {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
