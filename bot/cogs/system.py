"""System commands: sync, stop, override, maintenance, debug."""

from __future__ import annotations

import ast
import asyncio
import os
import sys

import discord
from discord.ext import commands

from bot.config.constants import STAFF_OVERRIDE_IDS
from bot.database import settings_col
from bot.utils.checks import maintenance_bypass, staff_only, staffperm


class SystemCog(commands.Cog, name="System"):
    """Owner-level system commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command()
    async def sync(self, ctx: commands.Context) -> None:
        try:
            print("✅ Manual command sync activated")
            synced = await asyncio.wait_for(self.bot.tree.sync(), timeout=30.0)
            await ctx.send(f"✅ Synced **{len(synced)}** global commands!")
        except asyncio.TimeoutError:
            await ctx.send(
                "⚠️ Sync timed out - Discord may be rate limiting. "
                "Try again in a few minutes."
            )
        except discord.HTTPException as exc:
            if exc.status == 429:
                retry_after = (
                    exc.retry_after if hasattr(exc, "retry_after") else 300
                )
                await ctx.send(
                    f"⚠️ Rate limited, wait {retry_after}s and try again."
                )
            else:
                await ctx.send(f"⚠️ Discord API error: `{exc}`")
        except Exception as exc:
            await ctx.send(f"⚠️ Sync failed: `{exc}`")
            print(f"Sync error details: {type(exc).__name__} - {exc}")

    @commands.command()
    @staffperm("stopbot")
    @staff_only()
    async def stop(self, ctx: commands.Context) -> None:
        self.bot.bot_locks[str(ctx.guild.id)] = True
        await ctx.send(
            "🔒 Bot locked. Use 'override' by theofficialtruck or CuteBatak to unlock."
        )

    @commands.command()
    async def override(self, ctx: commands.Context) -> None:
        if ctx.author.id in STAFF_OVERRIDE_IDS:
            self.bot.bot_locks[str(ctx.guild.id)] = False
            await ctx.send("🚀 Bot unlocked!")
        else:
            await ctx.send("❌ You don't have permission.")

    @commands.command(
        name="maintenance",
        description="Toggle maintenance mode (staff only access).",
    )
    @staffperm("config")
    @staff_only()
    @maintenance_bypass()
    async def maintenance(
        self,
        ctx: commands.Context,
        action: str | None = None,
    ) -> None:
        guild_id = str(ctx.guild.id)

        if action is None:
            settings = await settings_col.find_one({"guild": guild_id})
            is_maintenance = (
                settings.get("maintenance_mode", False) if settings else False
            )

            embed = discord.Embed(
                title="🔧 Maintenance Status",
                description=(
                    f"Maintenance mode is currently: "
                    f"{'**ON**' if is_maintenance else '**OFF**'}"
                ),
                color=(
                    discord.Color.orange() if is_maintenance else discord.Color.green()
                ),
            )

            if is_maintenance:
                embed.add_field(
                    name="⚠️ Current Status",
                    value=(
                        "• Only staff can use bot commands\n"
                        "• Channel restrictions are bypassed for staff\n"
                        "• Regular users cannot use any commands"
                    ),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="✅ Current Status",
                    value=(
                        "• All users can use bot commands\n"
                        "• Channel restrictions are enforced\n"
                        "• Normal operation mode"
                    ),
                    inline=False,
                )

            embed.add_field(
                name="📝 Usage",
                value=(
                    "`.maintenance on` - Enable maintenance mode\n"
                    "`.maintenance off` - Disable maintenance mode"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        action = action.lower()
        if action not in {"on", "off"}:
            return await ctx.send(
                "❌ Invalid action. Use `on`, `off`, or no argument to check status."
            )

        await settings_col.update_one(
            {"guild": guild_id},
            {"$set": {"maintenance_mode": action == "on"}},
            upsert=True,
        )

        if action == "on":
            embed = discord.Embed(
                title="🔧 Maintenance Mode Enabled",
                description="**Bot is now in maintenance mode!**",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="⚠️ What Changed",
                value=(
                    "• Only staff members can use bot commands\n"
                    "• Channel restrictions are ignored for staff\n"
                    "• Regular users see maintenance messages"
                ),
                inline=False,
            )
            embed.add_field(
                name="👤 Who Can Use Commands",
                value=(
                    "• Server Owner\n"
                    "• Staff members (with configured staff role)\n"
                    "• Users with admin permissions"
                ),
                inline=False,
            )
            embed.set_footer(text="Use `.maintenance off` to disable maintenance mode")
        else:
            embed = discord.Embed(
                title="✅ Maintenance Mode Disabled",
                description="**Bot is back to normal operation!**",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="🔄 What Changed",
                value=(
                    "• All users can use bot commands again\n"
                    "• Channel restrictions are enforced\n"
                    "• Normal operation resumed"
                ),
                inline=False,
            )
            embed.set_footer(text="Use `.maintenance on` to enable maintenance mode")

        await ctx.send(embed=embed)

    @commands.command()
    @staff_only()
    async def debug(self, ctx: commands.Context) -> None:
        await ctx.send("🧪 Scanning bot code for issues... This may take a moment.")

        async def _run_debug_checks() -> list[str]:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            base_dir = os.path.dirname(base_dir)  # repo root

            def _syntax_check() -> list[str]:
                syntax_errors: list[str] = []
                for root, _, files in os.walk(base_dir):
                    for filename in files:
                        if not filename.endswith(".py"):
                            continue
                        path = os.path.join(root, filename)
                        try:
                            with open(path, "r", encoding="utf-8") as fp:
                                source = fp.read()
                            ast.parse(source, filename=path)
                            compile(source, path, "exec")
                        except SyntaxError as exc:
                            syntax_errors.append(
                                f"❌ `{path}`: SyntaxError at line {exc.lineno} "
                                f"- {exc.msg}"
                            )
                        except Exception as exc:
                            syntax_errors.append(
                                f"⚠️ `{path}`: {type(exc).__name__} - {exc}"
                            )
                return syntax_errors

            syntax_errors = await asyncio.to_thread(_syntax_check)

            try:
                config_path = os.path.join(base_dir, "flake8_config.txt")
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "flake8",
                    base_dir,
                    "--config",
                    config_path,
                    "--exclude=.venv,__pycache__,build,dist",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, _ = await asyncio.wait_for(
                        process.communicate(), timeout=10
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await process.communicate()
                    lint_errors = ["⚠️ `flake8` lint check timed out."]
                else:
                    if process.returncode != 0 and stdout:
                        lint_errors = [
                            f"❗ {line}" for line in stdout.decode().strip().splitlines()
                        ]
                    else:
                        lint_errors = []
            except FileNotFoundError:
                lint_errors = [
                    "⚠️ `flake8` module not found; make sure it's in your `requirements.txt`."
                ]

            return syntax_errors + lint_errors

        errors = await _run_debug_checks()
        if errors:
            await ctx.send(f"❗ Found `{len(errors)}` issue(s):")
            for error in errors[:10]:
                await ctx.send(error)
            if len(errors) > 10:
                await ctx.send("...and more. Check logs for full list.")
            print("\n[DEBUG LOG]")
            for err in errors:
                print(err)
        else:
            await ctx.send("✅ No syntax or lint issues found.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SystemCog(bot))
