"""``/disable``, ``/enable`` and ``/listdisabled``."""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database import disabled_col
from bot.utils.checks import staff_only, staffperm


class DisableCmdsCog(commands.Cog, name="DisabledCommands"):
    """Toggle commands and categories on/off per guild."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="disable",
        description="Disable a command or a category. Staff-only.",
    )
    @staffperm("toggle_commands")
    @staff_only()
    async def disable(self, ctx: commands.Context, target: str) -> None:
        guild_id = str(ctx.guild.id)
        doc = await disabled_col.find_one({"guild": guild_id}) or {
            "disabled_commands": [],
            "disabled_categories": [],
        }
        commands_set = set(doc["disabled_commands"])
        categories_set = set(doc["disabled_categories"])

        target = target.lower()
        all_cmds = [c.name for c in self.bot.commands]
        all_cats = ["economy", "moderation", "duckgpt", "general"]

        if target in all_cmds:
            if target in commands_set:
                return await ctx.send(f"❌ `{target}` is already disabled.")
            commands_set.add(target)
            await ctx.send(f"✅ Disabled command `{target}`.")
        elif target in all_cats:
            if target in categories_set:
                return await ctx.send(
                    f"❌ Category `{target}` is already disabled."
                )
            categories_set.add(target)
            await ctx.send(f"✅ Disabled category `{target}`.")
        else:
            return await ctx.send("⚠️ Unknown command or category.")

        await disabled_col.update_one(
            {"guild": guild_id},
            {
                "$set": {
                    "disabled_commands": list(commands_set),
                    "disabled_categories": list(categories_set),
                }
            },
            upsert=True,
        )

    @commands.hybrid_command(
        name="enable",
        description="Enable a disabled command or category. Staff-only.",
    )
    @staffperm("toggle_commands")
    @staff_only()
    async def enable(self, ctx: commands.Context, target: str) -> None:
        guild_id = str(ctx.guild.id)
        doc = await disabled_col.find_one({"guild": guild_id}) or {
            "disabled_commands": [],
            "disabled_categories": [],
        }
        commands_set = set(doc["disabled_commands"])
        categories_set = set(doc["disabled_categories"])

        target = target.lower()
        if target in commands_set:
            commands_set.remove(target)
            await ctx.send(f"✅ Enabled command `{target}`.")
        elif target in categories_set:
            categories_set.remove(target)
            await ctx.send(f"✅ Enabled category `{target}`.")
        else:
            return await ctx.send("❌ That wasn't disabled.")

        await disabled_col.update_one(
            {"guild": guild_id},
            {
                "$set": {
                    "disabled_commands": list(commands_set),
                    "disabled_categories": list(categories_set),
                }
            },
            upsert=True,
        )

    @commands.hybrid_command(
        name="listdisabled",
        description="List currently disabled commands and categories. Staff-only.",
    )
    @staffperm("toggle_commands")
    @staff_only()
    async def listdisabled(self, ctx: commands.Context) -> None:
        doc = await disabled_col.find_one({"guild": str(ctx.guild.id)})
        if not doc or ("commands" not in doc and "categories" not in doc):
            return await ctx.send(
                "✅ No commands or categories are currently disabled."
            )

        disabled_cmds = doc.get("commands", [])
        disabled_cats = doc.get("categories", [])

        embed = discord.Embed(
            title="🔒 Disabled Features", color=discord.Color.red()
        )
        if disabled_cmds:
            embed.add_field(
                name="Commands",
                value="\n".join(f"`{cmd}`" for cmd in disabled_cmds),
                inline=False,
            )
        if disabled_cats:
            embed.add_field(
                name="Categories",
                value="\n".join(f"`{cat}`" for cat in disabled_cats),
                inline=False,
            )

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DisableCmdsCog(bot))
