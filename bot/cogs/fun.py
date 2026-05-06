"""Lightweight community commands: slap, duckfact, duck, quote, quack counter, etc."""

from __future__ import annotations

import json
import random

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import BucketType, cooldown

from bot.config.secrets import TENOR_API_KEY
from bot.database import config_col
from bot.utils.checks import blacklist_barrier
from bot.views.leaderboard import QuackTopView


class FunCog(commands.Cog, name="Fun"):
    """Slap, duck pictures, random quotes, quack counters, etc."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="slap", description="Slap another user")
    @app_commands.describe(
        member="The user to slap (optional - will slap yourself if not provided)"
    )
    @commands.cooldown(1, 5, commands.BucketType.member)
    @blacklist_barrier()
    async def slap(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        if not member:
            await ctx.send("❌ You need to mention someone to slap!")
            return

        try:
            await ctx.defer()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://tenor.googleapis.com/v2/search?q=anime slap"
                    f"&key={TENOR_API_KEY}&limit=20",
                    timeout=5,
                ) as response:
                    if response.status != 200:
                        raise Exception(f"HTTP {response.status}")
                    data = await response.json()

            results = data.get("results", [])
            if not results:
                await ctx.send("❌ Couldn't find any slap GIFs right now.")
                return

            gif_url = random.choice(results)["media_formats"]["gif"]["url"]
            embed = discord.Embed(
                title="👋 Slap!",
                description=f"{ctx.author.mention} slapped {member.mention}! Ouch!",
                color=discord.Color.red(),
            )
            embed.set_image(url=gif_url)
            await ctx.send(embed=embed)
        except Exception as exc:
            await ctx.send(
                f"⚠️ Something went wrong while fetching the slap GIF: `{exc}`"
            )

    @commands.hybrid_command(
        name="duckfact", description="Get a random duck fact"
    )
    @commands.cooldown(1, 5, commands.BucketType.member)
    @blacklist_barrier()
    async def duckfact(self, ctx: commands.Context) -> None:
        try:
            with open("duckfacts.txt", "r", encoding="utf-8") as fp:
                facts = [line.strip() for line in fp if line.strip()]
            if not facts:
                raise ValueError("Duck facts file is empty.")

            fact = random.choice(facts)
            embed = discord.Embed(
                title="🦆 Duck Fact",
                description=fact,
                color=discord.Color.teal(),
            )
            embed.set_thumbnail(url="https://random-d.uk/api/v2/random")
            await ctx.send(embed=embed)
        except FileNotFoundError:
            await ctx.send(
                "❌ Could not find `duckfacts.txt`. Please create it in the bot folder."
            )
        except Exception as exc:
            await ctx.send(
                f"⚠️ Something went wrong while fetching a duck fact: `{exc}`"
            )

    @commands.hybrid_command(
        name="duck", description="Random picture of a duck."
    )
    @cooldown(1, 5, BucketType.member)
    @blacklist_barrier()
    async def duck(self, ctx: commands.Context) -> None:
        config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}
        allowed_channels = config.get("ALLOWED_DUCK_CHANNELS", [])
        if allowed_channels and ctx.channel.id not in allowed_channels:
            return await ctx.send("🚫 You can't use this command here.")

        async with aiohttp.ClientSession() as session:
            async with session.get("https://random-d.uk/api/random") as response:
                if response.status != 200:
                    return await ctx.send(
                        "❌ Could not get a duck right now, try again later!"
                    )
                data = await response.json()
                url = data.get("url")
                if not url:
                    return await ctx.send("❌ Duck image not found, sorry!")

        embed = discord.Embed(title="🦆 Quack!", color=discord.Color.blue())
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="quote", description="Get a random quote."
    )
    @cooldown(1, 5, BucketType.member)
    @blacklist_barrier()
    async def quote(self, ctx: commands.Context) -> None:
        api_url = "https://zenquotes.io/api/random"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    text = await response.text()
                    if response.status != 200:
                        return await ctx.send(
                            f"❌ Could not fetch a quote right now (Status {response.status})"
                        )
                    try:
                        data = json.loads(text)
                    except Exception as exc:
                        print(f"[JSON PARSE ERROR] {type(exc).__name__} - {exc}")
                        return await ctx.send(
                            f"⚠️ API returned invalid data:\n```{text[:200]}...```"
                        )

            if not data or not isinstance(data, list):
                return await ctx.send(
                    "❌ Couldn't fetch a quote this time, try again!"
                )

            quote_text = str(data[0].get("q") or "No quote found")
            author = str(data[0].get("a") or "Unknown")
            embed = discord.Embed(
                title="💬 Random Quote",
                description=f"“{quote_text}”\n\n- *{author}*",
                color=discord.Color.purple(),
            )
            await ctx.send(embed=embed)
        except Exception as exc:
            await ctx.send(
                "⚠️ Something went wrong while fetching a quote. Contact thetruck."
            )
            print(f"[QUOTE ERROR] {type(exc).__name__} - {exc}")

    @commands.hybrid_command(
        name="quackcount",
        description="Check the server's total quacks and a user's quacks.",
    )
    async def quackcount(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        guild_id = str(ctx.guild.id)
        config = await config_col.find_one({"guild": guild_id})
        if not config or config.get("quack_count", 0) == 0:
            return await ctx.send("🦆 No quacks have been counted yet!")

        target = member or ctx.author
        user_quacks = config.get("quacks", {}).get(str(target.id), 0)
        total_quacks = config.get("quack_count", 0)
        label = (
            "Your"
            if target.id == ctx.author.id
            else f"{target.display_name}'s"
        )
        await ctx.send(
            f"🦆 **Server Quacks:** {total_quacks}\n"
            f"🦆 **{label} Quacks:** {user_quacks}"
        )

    @commands.hybrid_command(
        name="quacktop", description="View the top quackers in this server."
    )
    async def quacktop(self, ctx: commands.Context) -> None:
        guild_id = str(ctx.guild.id)
        config = await config_col.find_one({"guild": guild_id})
        if not config or not config.get("quacks"):
            return await ctx.send("🦆 No quacks have been counted yet!")

        top_quackers = sorted(
            config["quacks"].items(), key=lambda kv: kv[1], reverse=True
        )
        view = QuackTopView(ctx, top_quackers)
        await ctx.send(embed=view.get_embed(), view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FunCog(bot))
