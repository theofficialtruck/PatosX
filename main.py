import sys, types
sys.modules["audioop"] = types.ModuleType("audioop")

import os, asyncio, random, traceback
from datetime import datetime, timedelta, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from duckquiz_questions import questions
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Select
from discord import ButtonStyle, Interaction, ui, NotFound, app_commands, SelectOption, VerificationLevel, AllowedMentions, Embed, File
from typing import Union
from flask import Flask
import aiohttp
from discord.ext.commands import cooldown, BucketType
from pytz import UTC
from collections import defaultdict
import time
from dateutil import parser
import traceback
import re
import ast
import subprocess
from dotenv import load_dotenv
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
try:
    import google.genai as genai_new
except Exception:
    genai_new = None
genai_old = None
from itertools import cycle
from pathlib import Path
import math
import random

# 1. SETUP ====================================================
load_dotenv()

required_keys = [
    "DISCORD_TOKEN",
    "MONGO_URI",
    "TENOR_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEYS"
]

env_vars = {key: os.getenv(key) for key in required_keys}

missing = [key for key, value in env_vars.items() if not value]

if missing:
    raise ValueError(f"❌ Missing required environment variables: {', '.join(missing)}")

mongo = AsyncIOMotorClient(env_vars["MONGO_URI"])
print(f"All required environment variables loaded: {', '.join(required_keys)}")

TOKEN = env_vars["DISCORD_TOKEN"]
MONGO_URI = env_vars["MONGO_URI"]
TENOR_API_KEY = env_vars["TENOR_API_KEY"]
OPENROUTER_API_KEY = env_vars["OPENROUTER_API_KEY"]
GEMINI_API_KEYS = os.getenv("GEMINI_API_KEYS", "").split(",")
GEMINI_API_KEYS = [k.strip() for k in GEMINI_API_KEYS if k.strip()]

db = mongo["discord_bot"]
settings_col = db["guild_settings"]
config_col = db["configuration"]
logs_col = db["logs"]
economy_col = db["economy"]
mod_col = db["moderation"]
afk_col = db["afk"]
vanity_col = db["vanityroles"]
sticky_col = db["stickynotes"]
reaction_col = db["reactionroles"]
shop_col = db["shop"]
fines_col = db["fines"]
welcome_col = db["welcome"]
boost_col = db["boost"]
guild_shop_col = db["guild_shop"]
quiz_col = db['quiz']
disabled_col = db["disabled"]
tickets_col = db["tickets"]
ticket_panels_col = db["ticket_panels"]
tickets_counter_col = db["tickets_counter"]
giveaway_col = db["giveaway_col"]
guild_config_col = db["guild_config"]
invites_col = db["invites"]
invite_config_col = db["invite_config"]
blacklist_col = db["blacklist"]
reminders_col = db["reminders"]
polls_col = db["polls"]
investments_col = db["investments"]
drops_col = db["drops"]
drop_instances_col = db["drop_instances"]
roles_col = db["roles"]
mutes_col = db["mutes"]
duck_conversations_col = db["duck_conversations"]
staffperms_col = db["staffperms"]
minigameplayerdata_col = db["minigameplayerdata"]

fishes = [
    ("🦐 Shrimp", 100),
    ("🐟 Fish", 200),
    ("🐠 Tropical Fish", 300),
    ("🦑 Squid", 400),
    ("🐡 Pufferfish", 500)
]

NUM_Q = 10
PASS_PCT = 80.0

intents = discord.Intents.all()

invite_cache = {}
last_invite_fetch = {}
INVITE_CACHE_DURATION = 300
GLOBAL_RATE_LIMIT = 30
last_global_invite_fetch = 0
invite_queue = asyncio.Queue()
processing_invite = False

async def process_invite_queue():
    global processing_invite, last_global_invite_fetch
    processing_invite = True
    
    while True:
        try:
            guild, future = await invite_queue.get()
            
            current_time = time.time()
            time_since_global = current_time - last_global_invite_fetch
            if time_since_global < GLOBAL_RATE_LIMIT:
                await asyncio.sleep(GLOBAL_RATE_LIMIT - time_since_global)
            
            guild_id = guild.id
            if guild_id in last_invite_fetch:
                time_since_last = current_time - last_invite_fetch[guild_id]
                if time_since_last < 60:
                    await asyncio.sleep(60 - time_since_last)
            
            try:
                last_global_invite_fetch = time.time()
                invites = await guild.invites()
                invite_cache[guild_id] = (current_time, invites)
                last_invite_fetch[guild_id] = current_time
                future.set_result(invites)
            except discord.HTTPException as e:
                if e.status == 429:
                    print(f"⚠️ Rate limited for guild {guild.name}, waiting {e.retry_after or 10}s...")
                    await asyncio.sleep(e.retry_after if hasattr(e, 'retry_after') else 10)
                    try:
                        invites = await guild.invites()
                        invite_cache[guild_id] = (current_time, invites)
                        last_invite_fetch[guild_id] = current_time
                        future.set_result(invites)
                    except Exception as retry_e:
                        print(f"❌ Retry failed for {guild.name}: {retry_e}")
                        future.set_exception(retry_e)
                else:
                    future.set_exception(e)
            
            invite_queue.task_done()
            await asyncio.sleep(5)
            
        except Exception as e:
            print(f"❌ Error in invite queue processor: {e}")
            await asyncio.sleep(10)

async def get_guild_invites(guild):
    guild_id = guild.id
    current_time = time.time()
    
    if guild_id in invite_cache:
        cached_time, cached_invites = invite_cache[guild_id]
        if current_time - cached_time < INVITE_CACHE_DURATION:
            return cached_invites
    
    global processing_invite
    if not processing_invite:
        asyncio.create_task(process_invite_queue())
    
    future = asyncio.Future()
    await invite_queue.put((guild, future))
    
    try:
        return await future
    except discord.HTTPException as e:
        if e.status == 429:
            print(f"⚠️ Rate limited for guild {guild.name}, using cached data...")
            return invite_cache.get(guild_id, (0, []))[1]
        else:
            print(f"❌ Error fetching invites for {guild.name}: {e}")
            return invite_cache.get(guild_id, (0, []))[1]

async def get_prefix(bot, message):
    if not message.guild:
        return "?"
    doc = await settings_col.find_one({"guild": str(message.guild.id)})
    return doc.get("prefix", "?") if doc else "?"

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True)
)

print("🔧 Bot initialized with built-in tree")
print(f"🔧 Bot object: {bot}")
print(f"🔧 Tree object: {bot.tree}")

print("📊 Checking registered commands...")
for cmd in bot.tree.walk_commands():
    print(f"📌 Registered command: {cmd.name}, guilds: {cmd.guild_ids}")
print(f"📊 Total commands registered: {len(list(bot.tree.walk_commands()))}")

@bot.event
async def on_guild_join(guild):
    await settings_col.update_one(
        {"guild": str(guild.id)},
        {"$setOnInsert": {"prefix": "?"}},
        upsert=True
    )

bot_locks = {}

@bot.check
async def global_lock_check(ctx):
    if ctx.command.name == "override":
        return True

    if bot_locks.get(str(ctx.guild.id)):
        await ctx.send("🔒 The bot is locked - only `override` by theofficialtruck works.")
        return False
    return True
    
@bot.event
async def on_disconnect():
    print("⚠️ Bot disconnected from Discord. Will attempt reconnect soon.")

# 2. UTIL FUNCTIONS ===========================================
def staff_only():
    async def predicate(ctx):
        guild_id = str(ctx.guild.id)
        settings = await settings_col.find_one({"guild": guild_id})
        if not settings or "staff_role" not in settings:
            return False
        role = discord.utils.get(ctx.guild.roles, id=settings["staff_role"])
        return bool(role and role in ctx.author.roles)
    return commands.check(predicate)

async def check_staff_perm(ctx, perm_name: str):
    if ctx.author == ctx.guild.owner or ctx.author.id == 1059882387590365314:
        return True

    if ctx.author.guild_permissions.administrator:
        return True

    data = await staffperms_col.find_one({
        "guild": str(ctx.guild.id),
        "user": str(ctx.author.id)
    })

    if not data or "permissions" not in data:
        return False

    perms = data["permissions"]

    if "all" in perms:
        return True

    if perm_name.startswith("tickets:"):

        if "tickets:all" in perms:
            return True
        
        return perm_name in perms

    return perm_name in perms

def staffperm(perm_name: str):
    async def predicate(ctx):
        return await check_staff_perm(ctx, perm_name)
    return commands.check(predicate)

async def is_blacklisted(guild: discord.Guild, user: discord.Member) -> bool:
    guild_id = str(guild.id)
    settings = await settings_col.find_one({"guild": guild_id})

    if settings and "blacklist_role" in settings:
        role = discord.utils.get(guild.roles, id=settings["blacklist_role"])
        if role and role in user.roles:
            return True

    return False

async def is_maintenance_mode(guild_id):
    settings = await settings_col.find_one({"guild": guild_id})
    return settings.get("maintenance_mode", False) if settings else False

async def is_staff_user(ctx):
    if ctx.author.id == ctx.guild.owner_id:
        return True
    
    if ctx.author.guild_permissions.administrator:
        return True
    
    settings = await settings_col.find_one({"guild": str(ctx.guild.id)})
    if settings and "staff_role" in settings:
        staff_role = ctx.guild.get_role(settings["staff_role"])
        if staff_role and staff_role in ctx.author.roles:
            return True
    
    return False

async def check_maintenance_access(ctx):
    guild_id = str(ctx.guild.id)
    
    if not await is_maintenance_mode(guild_id):
        return True
    
    if await is_staff_user(ctx):
        return True
    
    embed = discord.Embed(
        title="🔧 Bot Under Maintenance",
        description="The bot is currently in maintenance mode. Only staff can use commands at this time.",
        color=discord.Color.orange()
    )
    embed.add_field(
        name="⏰ Estimated Downtime",
        value="Maintenance mode will be disabled when staff complete their work.",
        inline=False
    )
    embed.set_footer(text="Please try again later. Thank you for your patience!")
    
    if hasattr(ctx, 'respond') and ctx.is_interaction():
        await ctx.respond(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)
    
    return False

async def check_channel(ctx, setting_key, channel_type):
    guild_id = str(ctx.guild.id)
    
    if await is_maintenance_mode(guild_id) and await is_staff_user(ctx):
        return True
    
    settings = await settings_col.find_one({"guild": guild_id})
    if not settings:
        return False
    
    channel_id = settings.get(setting_key)
    if not channel_id:
        return False
    
    if ctx.channel.id != channel_id:
        embed = discord.Embed(
            title=f"❌ Wrong Channel",
            description=f"This command can only be used in the {channel_type} channel.",
            color=discord.Color.red()
        )
        channel = bot.get_channel(channel_id)
        if channel:
            embed.add_field(name="Correct Channel", value=f"{channel.mention}", inline=False)
        
        if hasattr(ctx, 'respond') and ctx.is_interaction():
            await ctx.respond(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return False
    
    return True

def blacklist_barrier():
    async def predicate(ctx_or_interaction):
        if hasattr(ctx_or_interaction, "author"):
            user = ctx_or_interaction.author
            guild = ctx_or_interaction.guild
            if await is_blacklisted(guild, user):
                try:
                    await ctx_or_interaction.send("🚫 You are blacklisted and cannot use this command.", delete_after=5)
                    await ctx_or_interaction.message.delete()
                except:
                    pass
                return False
            
            if not await check_maintenance_access(ctx_or_interaction):
                return False
                
        else:
            user = ctx_or_interaction.user
            guild = bot.get_guild(int(ctx_or_interaction.guild_id))
            if guild and await is_blacklisted(guild, user):
                try:
                    await ctx_or_interaction.response.send_message("🚫 You are blacklisted and cannot use this command.", ephemeral=True)
                except:
                    pass
                return False
            
            if not await check_maintenance_access(ctx_or_interaction):
                return False
        
        return True
    return commands.check(predicate)

def maintenance_bypass():
    async def predicate(ctx):
        return True
    return commands.check(predicate)
    
def get_command_syntax(command_name: str) -> str:
    command = bot.get_command(command_name)
    if not command:
        return f"Command `{command_name}` not found."
    
    syntax_parts = [f"**{command.name}**"]
    
    if command.aliases:
        syntax_parts[0] += f" (aliases: {', '.join(f'`{alias}`' for alias in command.aliases)})"
    
    params = []
    for param_name, param in command.clean_params.items():
        if param_name in ('ctx', 'interaction'):
            continue
        
        param_str = param_name
        if param.default is not param.empty:
            param_str = f"[{param_name}]"
        else:
            param_str = f"<{param_name}>"
        
        if param.annotation and param.annotation != param.empty:
            if hasattr(param.annotation, '__name__'):
                param_str += f" ({param.annotation.__name__})"
            elif hasattr(param.annotation, '__origin__'):
                if param.annotation.__origin__ is Union:
                    types = [t.__name__ for t in param.annotation.__args__ if t is not type(None)]
                    param_str += f" ({'|'.join(types)})"
        
        params.append(param_str)
    
    if params:
        syntax_parts.append(" ".join(params))
    
    description = command.description or command.help
    if description:
        syntax_parts.append(f"\n*{description}*")
    
    return " ".join(syntax_parts)

def find_similar_commands(command_name: str, limit: int = 3) -> list:
    command_name = command_name.lower()
    similar_commands = []
    
    for cmd in bot.walk_commands():
        if cmd.name.lower() == command_name:
            continue
        
        cmd_names = [cmd.name.lower()] + [alias.lower() for alias in cmd.aliases]
        found_similar = False
        
        for name in cmd_names:
            if command_name in name or name in command_name:
                similar_commands.append(cmd.name)
                found_similar = True
                break
        
        if not found_similar and len(command_name) >= 3:
            for name in cmd_names:
                if command_name[:3] in name:
                    similar_commands.append(cmd.name)
                    break
    
    return similar_commands[:limit]

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        command_name = ctx.command.name if ctx.command else "unknown"
        syntax = get_command_syntax(command_name)
        return await ctx.send(f"⚠️ **Missing required argument**\n\n**Usage:** {syntax}")
    elif isinstance(error, commands.BadArgument):
        command_name = ctx.command.name if ctx.command else "unknown"
        syntax = get_command_syntax(command_name)
        return await ctx.send(f"⚠️ **Invalid argument provided**\n\n**Usage:** {syntax}")
    elif isinstance(error, commands.CommandNotFound):
        invoked_command = ctx.invoked_with
        similar = find_similar_commands(invoked_command)
        
        if similar:
            similar_text = "\n".join(f"• `{cmd}`" for cmd in similar)
            return await ctx.send(f"⚠️ **Command not found:** `{invoked_command}`\n\n**Did you mean:**\n{similar_text}\n\nUse `.help` to see all available commands.")
        else:
            return await ctx.send(f"⚠️ **Command not found:** `{invoked_command}`\n\nUse `.help` to see all available commands.")
    elif isinstance(error, commands.TooManyArguments):
        command_name = ctx.command.name if ctx.command else "unknown"
        syntax = get_command_syntax(command_name)
        return await ctx.send(f"⚠️ **Too many arguments provided**\n\n**Usage:** {syntax}")
    else:
        print(f"An unexpected error occured: {error}")

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CommandNotFound):
        similar = find_similar_commands(interaction.command.name if interaction.command else "")
        if similar:
            similar_text = "\n".join(f"• `{cmd}`" for cmd in similar)
            embed = discord.Embed(
                title="⚠️ Command Not Found",
                description=f"Command `/{interaction.command.name}` not found.\n\n**Did you mean:**\n{similar_text}\n\nUse `/help` to see all available commands.",
                color=discord.Color.orange()
            )
        else:
            embed = discord.Embed(
                title="⚠️ Command Not Found", 
                description=f"Command `/{interaction.command.name}` not found.\n\nUse `/help` to see all available commands.",
                color=discord.Color.orange()
            )
    elif isinstance(error, app_commands.MissingRequiredArgument):
        command_name = interaction.command.name if interaction.command else "unknown"
        syntax = get_command_syntax(command_name)
        embed = discord.Embed(
            title="⚠️ Missing Required Argument",
            description=f"**Usage:** {syntax}",
            color=discord.Color.orange()
        )
    elif isinstance(error, app_commands.BadArgument):
        command_name = interaction.command.name if interaction.command else "unknown"
        syntax = get_command_syntax(command_name)
        embed = discord.Embed(
            title="⚠️ Invalid Argument",
            description=f"**Usage:** {syntax}",
            color=discord.Color.orange()
        )
    elif isinstance(error, app_commands.CheckFailure):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You don't have permission to use this command.",
            color=discord.Color.red()
        )
    else:
        embed = discord.Embed(
            title="❌ Command Error",
            description=f"An error occurred:\n```{error}```",
            color=discord.Color.red()
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"[APP COMMAND ERROR HANDLER FAILED] {e}")

@bot.check
async def ensure_guild_context(ctx):
    if ctx.guild is None:
        await ctx.send("❌ This bot can only be used in a server, not in DMs.")
        return False
    return True

async def ticket_error(interaction: discord.Interaction, coro):
    try:
        await coro()
    except Exception as e:
        print("⚠️ Ticket System Error:")
        traceback.print_exc()

        if interaction.response.is_done():
            await interaction.followup.send(f"❌ An error occurred: `{e}`", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ An error occurred: `{e}`", ephemeral=True)
        
@bot.check
async def check_disabled(ctx):
    if not ctx.guild:
        return True

    doc = await disabled_col.find_one({"guild": str(ctx.guild.id)})
    if not doc:
        return True

    if ctx.command.name in doc.get("disabled_commands", []):
        return False

    category = ctx.command.cog_name.lower() if ctx.command.cog_name else None
    if category and category in doc.get("disabled_categories", []):
        return False

    return True

async def get_user(ctx, guild_id, user_id):
    key = f"{guild_id}-{user_id}"
    guild_id = str(guild_id)
    user_id = str(user_id)

    defaults = {
        "_id": key,
        "guild": guild_id,
        "user": user_id,
        "wallet": 0,
        "bank": 0,
        "inventory": [],
        "job": None,
        "job_start": None,
        "promoted": False,
        "last_beg": None,
        "last_fished": None,
        "last_daily": None,
        "daily_streak": 0
    }

    u = await economy_col.find_one({"_id": key})
    if not u:
        await economy_col.insert_one(defaults)
        return defaults
    else:
        updated = False
        changes_detected = []
        for k, v in defaults.items():
            if k not in u:
                u[k] = v
                updated = True
                changes_detected.append((k, "None", v))

        for field in ["wallet", "bank"]:
            if field in u:
                try:
                    current_val = int(u[field])
                except:
                    current_val = u[field]
                if isinstance(current_val, int) and current_val != defaults[field]:
                    changes_detected.append((field, u[field], current_val))

        if updated:
            await economy_col.update_one({"_id": key}, {"$set": u})

        return u
        
async def prompt_for_role(ctx):
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    messages_to_delete = []

    while True:
        bot_msg = await ctx.send("📌 Please enter the **role ID or role name** (or type `cancel` to skip):")
        messages_to_delete.append(bot_msg)

        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            messages_to_delete.append(msg)
        except asyncio.TimeoutError:
            timeout_msg = await ctx.send("⌛ Cancelled due to timeout.")
            messages_to_delete.append(timeout_msg)
            await asyncio.sleep(3)
            await ctx.channel.delete_messages(messages_to_delete)
            return None

        content = msg.content.strip()
        if content.lower() == "cancel":
            cancel_msg = await ctx.send("❌ Role linking cancelled.")
            messages_to_delete.append(cancel_msg)
            await asyncio.sleep(3)
            await ctx.channel.delete_messages(messages_to_delete)
            return None

        role = None
        try:
            role_id = int(content)
            role = ctx.guild.get_role(role_id)
        except ValueError:
            role = discord.utils.get(ctx.guild.roles, name=content)
            if not role:
                role = discord.utils.find(lambda r: r.name.lower() == content.lower(), ctx.guild.roles)

        if not role:
            err_msg = await ctx.send("❌ No role found with that ID or name. Please try again.")
            messages_to_delete.append(err_msg)
            continue

        success_msg = await ctx.send(f"✅ Linked role: {role.mention}")
        messages_to_delete.append(success_msg)
        await asyncio.sleep(3)
        await ctx.channel.delete_messages(messages_to_delete)
        return role.id

def check_target_permission(ctx, member: discord.Member):
    if member == ctx.author:
        return "❌ You can't perform this action on yourself."
    if member == ctx.guild.owner:
        return "❌ You can't perform this action on the server owner."
    if ctx.author.top_role <= member.top_role and ctx.author != ctx.guild.owner:
        return "❌ You can't perform this action on someone with an equal or higher role."
    return None

async def check_channel(ctx, config_key: str, friendly_name: str = None) -> bool:
    settings = await settings_col.find_one({"guild": str(ctx.guild.id)}) or {}
    staff_role_id = settings.get("staff_role")

    if staff_role_id and discord.utils.get(ctx.author.roles, id=staff_role_id):
        return True

    config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception:
            config = {}
    value = config.get(config_key)

    if not value:
        return True

    if isinstance(value, int):
        allowed_channels = [value]
    elif isinstance(value, str):
        if value.lower() == "all":
            return True
        if value.isdigit():
            allowed_channels = [int(value)]
        else:
            ids = [int(x) for x in re.findall(r"\\d+", value)]
            allowed_channels = ids
    elif isinstance(value, list):
        allowed_channels = [int(x) for x in value if str(x).isdigit()]
    else:
        return True

    if allowed_channels and ctx.channel.id not in allowed_channels:
        mention = f"<#{allowed_channels[0]}>" if allowed_channels else "`a configured channel`"
        fname = friendly_name or config_key.replace("_", " ").title()
        await ctx.send(f"🚫 {fname} commands can only be used in {mention}.")
        return False

    return True

async def log_action(ctx, message, user_id=None, action_type=None):
    try:
        guild_id = str(ctx.guild.id)
        settings = await settings_col.find_one({"guild": guild_id})
        log_channel_id = settings.get("log_channel") if settings else None

        if log_channel_id:
            log_channel = bot.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="📋 Moderation Log",
                    description=message,
                    color=discord.Color.dark_blue(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_footer(text=f"By {ctx.author} • {ctx.author.id}")
                await log_channel.send(embed=embed)

        if user_id and action_type:
            log_doc = {
                "guild": guild_id,
                "user_id": str(user_id),
                "action": action_type,
                "by": {"name": str(ctx.author), "id": str(ctx.author.id)},
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await logs_col.insert_one(log_doc)
    except Exception as e:
        print(f"[log_action ERROR] {e}")

def add_suffix(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    elif value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"{value / 1_000:.2f}K"
    else:
        return str(value)

def suffix_to_int(s: str) -> int:
    s = s.upper().replace(",", "")
    if s.endswith("B"):
        return int(float(s[:-1]) * 1_000_000_000)
    elif s.endswith("M"):
        return int(float(s[:-1]) * 1_000_000)
    elif s.endswith("K"):
        return int(float(s[:-1]) * 1_000)
    else:
        return int(float(s))

@tasks.loop(seconds=10)
async def check_expired_mutes():
    now = datetime.now(timezone.utc)

    async for doc in mod_col.find({"muted_until": {"$exists": True}}):
        try:
            mute_until = datetime.fromisoformat(doc["muted_until"])
            if mute_until.tzinfo is None:
                mute_until = mute_until.replace(tzinfo=timezone.utc)

            if mute_until <= now:
                guild = bot.get_guild(int(doc["guild"]))
                if not guild:
                    continue

                member = guild.get_member(int(doc["user"]))
                if not member:
                    await mod_col.update_one(
                        {"guild": doc["guild"], "user": doc["user"]},
                        {"$unset": {"muted_until": ""}}
                    )
                    continue

                mute_role = discord.utils.get(guild.roles, name="Muted")
                if mute_role and mute_role in member.roles:
                    try:
                        await member.remove_roles(mute_role, reason="Mute expired")
                        await log_action(
                            ctx=None,
                            message=f"Auto-unmuted {member}",
                            user_id=member.id,
                            action_type="unmute"
                        )
                    except NotFound:
                        print(f"[Auto-unmute] Member {member.id} not found, likely left.")
                    except Exception as inner_e:
                        print(f"[Auto-unmute role removal error] {inner_e}")

                await mod_col.update_one(
                    {"guild": doc["guild"], "user": doc["user"]},
                    {"$unset": {"muted_until": ""}}
                )

        except Exception as e:
            print(f"[Auto-unmute error] {e}")

@check_expired_mutes.before_loop
async def before_unmute_loop():
    await bot.wait_until_ready()

@bot.hybrid_command(name="configure", aliases=["config"], description="Make server configuration.")
@staffperm("config")
@staff_only()
async def configure(ctx):
    prompts = {
        "welcome_channel": "Enter the **welcome channel ID** (required for welcome system):",
        "welcome_message": "Enter the **welcome message** (required):",
        "boost_channel": "Enter the **boost channel ID** (required for boost system):",
        "boost_message": "Enter the **boost message** (required):",
        "ALLOWED_DUCK_CHANNELS": "Enter allowed channel IDs for `.duck` (comma/space separated, required):",
        "ROLE_ID": "Enter role IDs to award for passing `.duckquiz` (required):",
        "QUIZ_CHANNEL": "Enter channel IDs where `.duckquiz` can run (required):",
        "allowed_channel_id": "Enter channel IDs where DuckGPT is allowed (required):",
        "economy_channel": "Enter the channel ID where the economy game is allowed (required):",
        "log_channel": "Enter the log channel ID for moderation logs (optional, type `skip` to disable):",
        "DROP_CHANNELS": "Enter channel IDs where `.drop` can be used by members (comma/space separated, required):",
        "QUACK_CHANNELS": "Enter channel IDs where the quack counter should activate (comma/space separated, optional, type `skip` to disable):"
    }

    config_data = {"guild": str(ctx.guild.id)}

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    await ctx.send("🛠 Starting configuration. Type `cancel` to abort at any time.")

    for key, question in prompts.items():
        await ctx.send(question)
        try:
            msg = await bot.wait_for("message", timeout=90, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Timed out. Configuration cancelled.")

        content = msg.content.strip()
        if content.lower() == "cancel":
            return await ctx.send("❌ Configuration cancelled.")

        if key == "log_channel" and content.lower() == "skip":
            continue
        elif key == "QUACK_CHANNELS" and content.lower() == "skip":
            config_data[key] = []
            continue

        if not content:
            return await ctx.send(f"❌ `{key}` cannot be blank. Please run `.configure` again.")

        try:
            if key in ["log_channel", "economy_channel", "welcome_channel", "boost_channel"]:
                if not content.isdigit():
                    return await ctx.send(f"❌ Please provide a valid channel ID for `{key}`.")
                config_data[key] = int(content)
            elif key in ["welcome_message", "boost_message"]:
                config_data[key] = content
            else:
                if content.lower() == "all":
                    config_data[key] = "all"
                else:
                    ids = [int(x) for x in re.split(r"[,\s]+", content) if x.isdigit()]
                    if not ids:
                        return await ctx.send(f"❌ No valid IDs entered for `{key}`.")
                    config_data[key] = ids

        except ValueError:
            return await ctx.send(f"❌ Couldn't parse IDs for `{key}`.")

        await msg.delete()

    await config_col.update_one({"guild": config_data["guild"]}, {"$set": config_data}, upsert=True)

    await ctx.send("✅ Configuration saved successfully!", delete_after=7)
    await log_action(ctx, f"Configuration updated for {ctx.guild.name}", action_type="configure")

@configure.error
async def configure_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.", delete_after=7)
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Only staff members can use this command.", delete_after=7)
    else:
        await ctx.send(
            f"⚠️ An unexpected error occurred, please contact thetruck: `{type(error).__name__} - {error}`",
            delete_after=10
        )

@bot.hybrid_command(name="editconfig", aliases=["editconfiguration"], description="Edit one configuration setting.")
@app_commands.describe(args="Configuration arguments")
@staffperm("config")
@staff_only()
async def editconfig(ctx, args: str = None):
    def norm(s):
        return re.sub(r"\s+", "_", s.strip().lower())

    valid_settings = {
        "welcome_channel": {"desc": "Welcome channel", "key": "welcome_channel"},
        "welcome_message": {"desc": "Welcome message", "key": "welcome_message"},
        "boost_channel": {"desc": "Boost channel", "key": "boost_channel"},
        "boost_message": {"desc": "Boost message", "key": "boost_message"},
        "allowed_duck_channels": {"desc": "Duck command allowed channels", "key": "ALLOWED_DUCK_CHANNELS"},
        "role_id": {"desc": "Quiz reward role", "key": "ROLE_ID"},
        "quiz_channel": {"desc": "Quiz allowed channels", "key": "QUIZ_CHANNEL"},
        "allowed_channel_id": {"desc": "DuckGPT allowed channels", "key": "allowed_channel_id"},
        "economy_channel": {"desc": "Economy channel", "key": "economy_channel"},
        "log_channel": {"desc": "Log channel", "key": "log_channel"},
        "drop_channels": {"desc": "Drop allowed channels", "key": "DROP_CHANNELS"},
        "quack_channels": {"desc": "Quack Counter Channels", "key": "QUACK_CHANNELS"}
    }

    if not args:
        return await ctx.send("❌ Please specify a setting and value, e.g. `editconfig welcome_channel #general`")

    parts = args.split()
    idx = len(parts)
    for i in range(1, len(parts)):
        p = parts[i]
        if p.isdigit() or p.startswith("<#") or p.startswith("<@&") or p.lower() in ("none","null","remove","delete","all"):
            idx = i
            break
    raw_setting = " ".join(parts[:idx]).strip()
    setting_norm = norm(raw_setting)
    value = " ".join(parts[idx:]).strip() if idx < len(parts) else None

    if setting_norm not in valid_settings:
        pretty_list = "\n".join(
            f"• `{info['key']}` - {info['desc']}" for info in valid_settings.values()
        )
        embed = discord.Embed(
            title="⚙️ Invalid Setting",
            description=(f"❌ **`{raw_setting}`** is not a valid configuration key.\n\n**Available settings:**\n" + pretty_list),
            color=discord.Color.red()
        )
        embed.set_footer(text="Tip: You can type settings with spaces (e.g. 'welcome message')")
        return await ctx.send(embed=embed)

    config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {"guild": str(ctx.guild.id)}

    canonical_key = valid_settings[setting_norm]["key"]
    desc = valid_settings[setting_norm]["desc"]

    if value and value.lower() in ["none", "null", "remove", "delete"]:
        await config_col.update_one({"guild": config["guild"]}, {"$unset": {canonical_key: ""}})
        await ctx.send(f"🗑 **{desc}** has been removed from the configuration.")
        await log_action(ctx, f"{desc} removed from {ctx.guild.name}", action_type="editconfig")
        return

    try:
        if canonical_key in ["welcome_message", "boost_message"] and not value:
            placeholder_info = (
                "🧩 You can use these placeholders in your message:\n"
                "`{username}` - Booster's username\n"
                "`{mention}` - Mention the booster\n"
                "`{server}` - Server name\n"
                "`{boostcount}` - Current server boost count\n\n"
            )
            await ctx.send(
                placeholder_info
                + f"📝 Please enter the new {desc.lower()} below.\n"
                "You can type `cancel` to abort or `none` to remove it."
            )

            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            try:
                msg = await bot.wait_for("message", timeout=180, check=check)
            except asyncio.TimeoutError:
                return await ctx.send("⌛ Timed out. Configuration cancelled.")

            content = msg.content.strip()
            if content.lower() == "cancel":
                return await ctx.send("❌ Edit cancelled.")
            elif content.lower() in ["none", "null", "remove", "delete"]:
                await config_col.update_one({"guild": config["guild"]}, {"$unset": {canonical_key: ""}})
                await ctx.send(f"🗑 **{desc}** has been removed from the configuration.")
                await log_action(ctx, f"{desc} removed from {ctx.guild.name}", action_type="editconfig")
                return

            config[canonical_key] = content
            await msg.delete()

            if canonical_key == "boost_message":
                await ctx.send(
                    "✨ Would you like me to react to each boost message with a custom emoji?\n"
                    "React to **this message** with the emoji you want, or type `none` to skip."
                )

                def emoji_check(reaction, user):
                    return user == ctx.author and reaction.message.channel == ctx.channel

                try:
                    emoji_msg = await ctx.send("⏳ Waiting for your emoji reaction or text reply...")

                    reaction_task = asyncio.create_task(bot.wait_for("reaction_add", timeout=30, check=emoji_check))
                    message_task = asyncio.create_task(bot.wait_for("message", timeout=30, check=check))

                    done, pending = await asyncio.wait(
                        [reaction_task, message_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    for task in pending:
                        task.cancel()

                    result = list(done)[0].result()

                    if isinstance(result, tuple):
                        reaction, _ = result
                        emoji = str(reaction.emoji)
                        config["boost_react_emoji"] = emoji
                        await ctx.send(f"✅ Set boost reaction emoji to {emoji}")
                    elif isinstance(result, discord.Message):
                        if result.content.lower().strip() != "none":
                            await ctx.send("⚠️ Invalid input, skipping emoji reaction setup.")
                        else:
                            await ctx.send("✅ No emoji reaction will be added to boost messages.")
                            config["boost_react_emoji"] = None

                except asyncio.TimeoutError:
                    await ctx.send("⌛ No emoji selected, skipping reaction setup.")
                except Exception as e:
                    await ctx.send(f"⚠️ Error while setting emoji: `{e}`")

        elif canonical_key in ["log_channel", "economy_channel", "welcome_channel", "boost_channel"]:
            match = re.search(r"\d+", value or "")
            if not match:
                return await ctx.send(f"❌ Please mention a valid channel or provide its ID for `{desc}`.")
            config[canonical_key] = int(match.group())

        elif canonical_key in ["welcome_message", "boost_message"]:
            config[canonical_key] = value
            
        else:
            if value and value.lower() == "all":
                config[canonical_key] = "all"
            else:
                ids = [int(x) for x in re.findall(r"\d+", value or "")]
                if not ids:
                    return await ctx.send(f"❌ No valid IDs found for `{desc}`.")
                config[canonical_key] = ids

    except Exception as e:
        return await ctx.send(f"⚠️ Error updating config: `{e}`")

    await config_col.update_one({"guild": config["guild"]}, {"$set": config}, upsert=True)
    await ctx.send(f"✅ **{desc}** updated successfully!")
    await log_action(ctx, f"{desc} updated in {ctx.guild.name}", action_type="editconfig")

@editconfig.error
async def editconfig_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.", ephemeral=True)
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Only staff members can use this command.", ephemeral=True)
    else:
        await ctx.send(
            f"⚠️ An unexpected error occurred, please contact thetruck: `{type(error).__name__} - {error}`",
            ephemeral=True
        )

@bot.hybrid_command(name="viewconfig", description="View the current server configuration.")
@staffperm("config")
@staff_only()
async def viewconfig(ctx: commands.Context):
    config = await config_col.find_one({"guild": str(ctx.guild.id)})

    if not config:
        return await ctx.send("⚠️ No configuration found for this server.", ephemeral=True)

    def format_ids(key):
        value = config.get(key)

        if value == "all":
            return "All channels"

        if not value:
            return "All channels" if "channel" in key.lower() else "Not set"

        if isinstance(value, list):
            return ', '.join(f"<#{i}>" if "channel" in key.lower() else f"<@&{i}>" for i in value)
        elif isinstance(value, int):
            return f"<#{value}>" if "channel" in key.lower() else f"<@&{value}>"

        return str(value)

    embed = discord.Embed(title="🔧 Server Configuration", color=discord.Color.blurple())

    embed.add_field(name="👋 Welcome Channel", value=format_ids("welcome_channel"), inline=False)
    embed.add_field(
        name="👋 Welcome Message",
        value=config.get("welcome_message", "Not set"),
        inline=False
    )

    embed.add_field(name="🚀 Boost Channel", value=format_ids("boost_channel"), inline=False)
    embed.add_field(
        name="🚀 Boost Message",
        value=config.get("boost_message", "Not set"),
        inline=False
    )

    embed.add_field(name="Duck Command Channels", value=format_ids("ALLOWED_DUCK_CHANNELS"), inline=False)
    embed.add_field(name="Quiz Role", value=format_ids("ROLE_ID"), inline=False)
    embed.add_field(name="Quiz Channel", value=format_ids("QUIZ_CHANNEL"), inline=False)
    embed.add_field(name="DuckGPT Allowed Channel", value=format_ids("allowed_channel_id"), inline=False)
    embed.add_field(name="Drop Channels", value=format_ids("DROP_CHANNELS"), inline=False)
    embed.add_field(name="Quack Counter Channels", value=format_ids("QUACK_CHANNELS"), inline=False)
    embed.add_field(name="Economy Channel", value=format_ids("economy_channel"), inline=False)
    embed.add_field(name="Log Channel", value=format_ids("log_channel"), inline=False)

    await ctx.send(embed=embed, ephemeral=True)

@viewconfig.error
async def viewconfig_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.", ephemeral=True)
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Only staff members can use this command.", ephemeral=True)
    else:
        await ctx.send(
            f"⚠️ An unexpected error occurred, please contact thetruck: `{type(error).__name__} - {error}`",
            ephemeral=True
        )

@bot.command()
@staffperm("config")
@staff_only()
async def resetconfig(ctx):
    await config_col.delete_one({"guild": str(ctx.guild.id)})
    await ctx.send("🗑 Configuration has been completely reset for this server.")

duck_conversations = {}
SYSTEM_PROMPT = (
    "You are DuckGPT a knowledgeable talking duck created by 'thetruck'. "
    "You can answer real questions in a SHORT, clear, and funny way while staying in duck character."
    "If the user is named 'thetruck', NEVER EVER EVER EVER say 'my creator is thetruck' or repeat that fact, just talk LIKE A NORMAL HUMAN EVEN THOUGH YOU ARENT. "
    "Always keep your reply to one sentence, humorous if possible, ending with one quack sound like 'Quack!' YOU CAN DO OTHERS PLEASE PLEASE PLEASE DONT STICK TO JUST QUACK. "
    "Never add blank lines or paragraphs. Never say things like 'you told me your name' or 'you didn’t tell me your name'. "
    "If asked any kind of questions, give a short and accurate summary as a talking duck. "
    "If greeted, you can greet back naturally, but DONT YOU DARE repeat the full intro every time. "
    "Your name is DuckGPT when requested for your name MAKE SURE TO RESPOND WITH DuckGPT."
)

async def cleanup_old_conversations():
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        result = await duck_conversations_col.delete_many(
            {"last_updated": {"$lt": cutoff}}
        )

        print(f"[DuckGPT Cleanup] Deleted {result.deleted_count} old conversations.")
        return result.deleted_count
    except Exception as e:
        print(f"[DuckGPT Cleanup Error] {e}")
        return 0

executor = ThreadPoolExecutor()
active_key = None

GEMINI_KEY_CYCLE = cycle(GEMINI_API_KEYS)

def next_gemini_key():
    global active_key
    active_key = next(GEMINI_KEY_CYCLE)
    return active_key

def build_gemini_client_for_key(key: str, model_name: str):
    if genai_new is not None and hasattr(genai_new, "Client"):
        try:
            client = genai_new.Client(api_key=key)
            return {"mode": "new", "client": client, "model": model_name}
        except Exception as e:
            raise e
    else:
        global genai_old
        if genai_old is None:
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=FutureWarning)
                    import google.generativeai as genai_old
            except Exception:
                genai_old = None
        if genai_old is None:
            raise RuntimeError("No Gemini SDK available. Install google-genai or google-generativeai.")
        try:
            genai_old.configure(api_key=key)
            model = genai_old.GenerativeModel(model_name)
            return {"mode": "old", "model": model}
        except Exception as e:
            raise e

def gemini_generate_once(client_info, prompt: str):
    if client_info["mode"] == "new":
        client = client_info["client"]
        model = client_info["model"]
        try:
            return client.models.generate_content(model=model, contents=prompt)
        except Exception:
            if hasattr(client, "responses"):
                return client.responses.generate(model=model, contents=prompt)
            raise
    else:
        model = client_info["model"]
        return model.generate_content(prompt)

async def get_gemini_client():
    global active_key

    for _ in range(len(GEMINI_API_KEYS)):
        key = next_gemini_key()
        try:
            client_info = build_gemini_client_for_key(key, "gemini-2.5-flash-lite")
            return client_info
        except Exception as e:
            print(f"❌ Gemini key {key[:8]} failed: {e}")
            continue

    print("❌ No working Gemini API keys found.")
    return None

async def generate_gemini_response(messages):
    loop = asyncio.get_event_loop()
    prompt = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in messages)

    client_info = await get_gemini_client()
    if not client_info:
        return "🦆 The duck slipped on a banana peel and can’t respond right now."

    for attempt in range(len(GEMINI_API_KEYS)):
        try:
            response = await loop.run_in_executor(
                executor, lambda: gemini_generate_once(client_info, prompt)
            )
            if hasattr(response, "text") and response.text:
                return response.text.strip()
            elif isinstance(response, str):
                return response.strip()
            else:
                return "🦆 The duck was thinking too hard and forgot what it was going to say."

        except Exception as e:
            err_str = str(e)
            print(f"[DuckGPT Gemini Error] {err_str}")

            if any(word in err_str.lower() for word in ["429", "quota", "api key not valid", "exceeded"]):
                print("⚠️ Gemini key hit limit or failed, switching key...")

                delay = 2 ** attempt + random.uniform(0, 1)
                print(f"🕒 Waiting {delay:.1f}s before switching...")
                await asyncio.sleep(delay)

                new_key = next_gemini_key()
                try:
                    client_info = build_gemini_client_for_key(new_key, "gemini-2.0-flash")
                except Exception as e2:
                    print(f"❌ Failed to switch Gemini key: {e2}")
                    continue
                continue

            print("💥 Non-recoverable Gemini error, stopping attempts.")
            break

    print("❌ All Gemini keys failed.")
    return "🦆 The duck slipped on a banana peel and can’t respond right now."

async def ask_duck_gpt(ctx, prompt: str) -> str:
    if not ctx.guild:
        return "🦆 I can only assist you in servers, not in DMs!"
    
    guild_id = str(ctx.guild.id)
    guild_name = ctx.guild.name
    user_id = str(ctx.author.id)
    display_name = ctx.author.display_name

    config = await config_col.find_one({"guild": guild_id}) or {}
    allowed_channels = config.get("allowed_channel_id", [])

    if isinstance(allowed_channels, (str, int)):
        allowed_channels = [int(allowed_channels)]
    elif isinstance(allowed_channels, list):
        allowed_channels = [int(x) for x in allowed_channels if str(x).isdigit()]
    else:
        allowed_channels = []

    if allowed_channels and ctx.channel.id not in allowed_channels:
        mention = f"<#{allowed_channels[0]}>" if allowed_channels else "`a DuckGPT channel`"
        return f"🦆 Please use this command in {mention}!"

    if user_id not in duck_conversations:
        record = await duck_conversations_col.find_one({"user_id": user_id, "guild_id": guild_id})
        if record and "messages" in record:
            duck_conversations[user_id] = record["messages"]
            greeted = False
        else:
            duck_conversations[user_id] = []
            greeted = False
    else:
        greeted = True

    lowered_prompt = prompt.lower()
    greetings = ["hi", "hello", "hey", "yo", "hiya", "sup", "greetings"]
    if any(word in lowered_prompt.split() for word in greetings):
        duck_conversations[user_id] = []
        greeted = False

    duck_conversations[user_id].append({"role": "user", "content": f"{display_name} said: {prompt}"})

    total_tokens = sum(len(msg["content"].split()) * 4 for msg in duck_conversations[user_id])
    if total_tokens > 1500:
        duck_conversations[user_id] = [{"role": "user", "content": prompt}]

    ai_task_keywords = [
        "do my homework", "solve this math", "write this code", "can you code",
        "generate art", "make ai art", "draw me", "write an essay", "make it", "create it"
    ]
    if any(phrase in prompt.lower() for phrase in ai_task_keywords):
        await log_action(
            ctx,
            f"⚠️ Attempted AI misuse: `{prompt}`",
            user_id=ctx.author.id,
            action_type="duckgpt_flag"
        )
        return "🦆 I'm just a talking duck! I can't do things for you."

    async def detect_duck_intent(prompt: str) -> str:
        intent_prompt = f"""
Analyze this message and decide what the user is asking:
- If they ask about your creator/owner, respond "owner".
- If they ask their name, respond "name".
- If they ask the server, respond "server".
- If they ask member count, respond "members".
- Otherwise respond "none".
Message: "{prompt}"
Only return one word: owner, name, server, members, or none.
"""
        client_info = await get_gemini_client()
        if not client_info:
            return "none"

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                executor,
                lambda: gemini_generate_once(client_info, intent_prompt)
            )
            return response.text.strip().lower() if hasattr(response, "text") else "none"
        except Exception as e:
            print(f"[DuckGPT detect intent error] {e}")
            return "none"

    intent = await detect_duck_intent(prompt)

    if intent == "owner":
        if ctx.author.id == 1059882387590365314:
            return "🦆 You are my owner! Quack!"
        elif display_name.lower() == "thetruck":
            return "🦆 You may *look* like my owner, but you’re not the real one! Bad duck! *angry quack!* 🦆"
        else:
            return "🦆 My owner is thetruck! Quack!"
    elif intent == "name":
        return f"🦆 Your name is `{display_name}`! Quack!"
    elif intent == "server":
        return f"🦆 You’re in `{guild_name}`! Quack!"
    elif intent == "members":
        return f"🦆 There are `{ctx.guild.member_count}` members in `{guild_name}`! Quack!"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + duck_conversations[user_id]

    response_text = await generate_gemini_response(messages)
    if not response_text:
        text = "🦆 The duck slipped on a banana peel and can’t respond right now."
    else:
        text = response_text

    duck_conversations[user_id].append({"role": "assistant", "content": text})
    await duck_conversations_col.update_one(
        {"user_id": user_id, "guild_id": guild_id},
        {
            "$set": {
                "messages": duck_conversations[user_id],
                "last_updated": datetime.now(timezone.utc)
            }
        },
        upsert=True
    )

    text = " ".join(text.split())
    if not greeted:
        return f"🦆 Quack! Hello {display_name}! I remember you from {guild_name}! {text}"
    else:
        return f"🦆 {text}"

last_sticky_trigger = defaultdict(float)

last_sticky_msg = {}
onetime_channels = {}  # {guild_id: {channel_id: {user_id: timestamp}}}

async def has_staff_role(member: discord.Member) -> bool:
    """Check if a member has the staff role set via .staffset command."""
    if not member.guild:
        return False
    
    data = await settings_col.find_one({"guild": str(member.guild.id)})
    if not data or "staff_role" not in data:
        return False
    
    staff_role_id = data["staff_role"]
    staff_role = member.guild.get_role(staff_role_id)
    
    if not staff_role:
        return False
    
    return staff_role in member.roles

async def load_sticky_messages():
    try:
        cursor = sticky_col.find({})
        async for doc in cursor:
            if "message" in doc:
                channel_key = int(doc["channel"])
                last_sticky_msg[channel_key] = doc["message"]
        print(f"[Sticky Notes] Loaded {len(last_sticky_msg)} sticky message IDs from database")
    except Exception as e:
        print(f"[Sticky Notes] Error loading sticky messages: {e}")

async def load_onetime_channels():
    try:
        cursor = settings_col.find({"onetime_channels": {"$exists": True}})
        async for doc in cursor:
            guild_id = doc["guild"]
            onetime_data = doc.get("onetime_channels", {})
            if onetime_data:
                if guild_id not in onetime_channels:
                    onetime_channels[guild_id] = {}
                onetime_channels[guild_id].update(onetime_data)
        print(f"[One-Time Channels] Loaded one-time channels for {len(onetime_channels)} guilds")
    except Exception as e:
        print(f"[One-Time Channels] Error loading one-time channels: {e}")

@tasks.loop(minutes=2)
async def check_and_repost_stickies():
    try:
        cursor = sticky_col.find({})
        async for doc in cursor:
            guild_id = doc["guild"]
            channel_id = int(doc["channel"])
            sticky_text = doc["text"]
            
            guild = bot.get_guild(int(guild_id))
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
                    
                    await sticky_col.update_one(
                        {"guild": guild_id, "channel": str(channel_id)},
                        {"$set": {"message": sent.id}}
                    )
                    print(f"[Sticky Notes] Reposted sticky note in channel {channel_id}")
                except Exception as e:
                    print(f"[Sticky Notes] Failed to repost sticky note: {e}")
    except Exception as e:
        print(f"[Sticky Notes] Error in check_and_repost_stickies: {e}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if not message.guild:
        return
    
    await bot.process_commands(message)
    
    # Quack Counter
    try:
        guild_id = str(message.guild.id)
        doc = await settings_col.find_one({"guild": str(message.guild.id)})
        prefix = doc.get("prefix", "?")
        if message.content.lower().startswith(prefix.lower()):
            return
        
        await config_col.update_one(
            {"guild": guild_id},
            {"$setOnInsert": {"quack_count": 0, "quacks": {}, "QUACK_CHANNELS": "all"}},
            upsert=True
        )
        
        config = await config_col.find_one({"guild": guild_id})
        quack_channels = config.get("QUACK_CHANNELS", [])
        
        counts_everywhere = (quack_channels == "all" or quack_channels == [])
        in_quack_channel = counts_everywhere or (isinstance(quack_channels, list) and message.channel.id in quack_channels)

        content_lower = message.content.lower()
        occurrences = len(re.findall(r"\bquack\b", content_lower))

        if in_quack_channel and occurrences > 0:
            user_id = str(message.author.id)
            
            await config_col.update_one(
                {"guild": guild_id},
                {
                    "$inc": {
                        "quack_count": occurrences,
                        f"quacks.{user_id}": occurrences
                    }
                }
            )
        
    except Exception as e:
        print(f"[Quack Counter Error] {e}")

    # Tickets
    try:
        ticket_entry = await tickets_col.find_one(
            {"guild": str(message.guild.id), "channel_id": str(message.channel.id), "close_pending": True}
        )
        if ticket_entry:
            opener_id = int(ticket_entry.get("owner_id"))
            if message.author.id == opener_id:
                if message.content.lower() == "cancel":
                    await tickets_col.update_one(
                        {"_id": ticket_entry["_id"]},
                        {"$set": {"close_pending": False}}
                    )
                    await message.channel.send("❌ Ticket close request canceled.")
                    return

                if message.content.lower() == "confirm":
                    opener = message.guild.get_member(opener_id)
                    class DummyCtx:
                        def __init__(self, channel, author):
                            self.channel = channel
                            self.author = message.author
                            self.guild = message.guild
                    ctx = DummyCtx(message.channel, message.author)
                    await actually_close_ticket(ctx, opener, forced=False)
                    await tickets_col.delete_one({"_id": ticket_entry["_id"]})
                    await message.channel.send("✅ Ticket has been closed.")
                    return

    except Exception as e:
        print(f"[ticket close error] {e}")

    # DuckGPT
    if bot.user in message.mentions:
        prompt = message.clean_content.replace(f"<@{bot.user.id}>", "").strip()
        if not prompt:
            prompt = "Quack!"

        ctx = await bot.get_context(message)

        if not ctx.guild:
            return await message.reply("🦆 I can only assist you in servers, not in DMs!")

        await message.channel.typing()
        reply = await ask_duck_gpt(ctx, prompt)
        await message.reply(reply)

    # Sticky Notes
    try:
        doc = await sticky_col.find_one({
            "guild": str(message.guild.id),
            "channel": str(message.channel.id)
        })
        if doc:
            old_id = last_sticky_msg.get(message.channel.id)
            if old_id:
                try:
                    old = await message.channel.fetch_message(old_id)
                    await old.delete()
                except discord.NotFound:
                    print(f"[sticky note] Previous message {old_id} not found, creating new one")
                except discord.Forbidden:
                    print(f"[sticky note] No permission to delete message {old_id}")
                except Exception as e:
                    print(f"[sticky note delete error] {e}")

            sent = await message.channel.send(doc["text"])
            last_sticky_msg[message.channel.id] = sent.id
            
            await sticky_col.update_one(
                {"guild": str(message.guild.id), "channel": str(message.channel.id)},
                {"$set": {"message": sent.id}}
            )

    except Exception as e:
        print(f"[sticky repost error] {e}")

    try:
        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)
        
        if (guild_id in onetime_channels and 
            channel_id in onetime_channels[guild_id] and
            not await has_staff_role(message.author, message.guild)):
            
            user_id = str(message.author.id)
            
            if user_id not in onetime_channels[guild_id][channel_id]:
                onetime_channels[guild_id][channel_id][user_id] = datetime.now(timezone.utc)
                
                await settings_col.update_one(
                    {"guild": guild_id},
                    {"$set": {f"onetime_channels.{channel_id}.{user_id}": datetime.now(timezone.utc)}},
                    upsert=True
                )
                
                try:
                    await message.channel.set_permissions(
                        message.author, 
                        send_messages=False,
                        reason="One-time message used"
                    )
                    
                    await message.channel.send(
                        f"⚠️ {message.author.mention} has used their one-time message in this channel. "
                        "Staff can restore permissions with `.restore`."
                    )
                except Exception as perm_error:
                    print(f"[One-time permission error] {perm_error}")
    except Exception as e:
        print(f"[One-time message error] {e}")

    # AFK System
    try:
        for user in message.mentions:
            doc = await afk_col.find_one({"_id": f"{message.guild.id}-{user.id}"})
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

        afk_key = f"{message.guild.id}-{message.author.id}"
        doc = await afk_col.find_one({"_id": afk_key})
        if doc:
            await afk_col.delete_one({"_id": afk_key})

            original_nick = doc.get("original_nick")
            current_nick = message.author.display_name

            try:

                if current_nick.startswith("[AFK]"):
                    await message.author.edit(nick=original_nick)
            except discord.Forbidden:
                await message.channel.send(
                    "⚠️ I couldn't restore your nickname due to role hierarchy, but AFK is removed.",
                    delete_after=5
                )
            except discord.HTTPException:
                await message.channel.send(
                    "⚠️ Something went wrong while restoring your nickname, but AFK is removed."
                )

            await message.channel.send(
                f"✅ Welcome back, {message.author.mention}! AFK removed.",
                delete_after=5
            )
    except Exception as e:
        print(f"[afk error] {e}")

    await sticky_col.create_index([("guild", 1), ("channel", 1)], unique=True)

async def get_invites_count(guild_id: int, user_id: int):
    total_uses = 0
    async for code_doc in invites_col.find({"guild_id": str(guild_id), "inviter_id": str(user_id)}):
        try:
            total_uses += int(code_doc.get("uses", 0))
        except Exception:
            pass
    return total_uses

def parse_time(duration_str: str) -> int:
    multipliers = {
        "s": 1, "sec": 1, "second": 1, "seconds": 1,
        "m": 60, "min": 60, "minute": 60, "minutes": 60,
        "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
        "d": 86400, "day": 86400, "days": 86400,
        "w": 604800, "week": 604800, "weeks": 604800,
        "mo": 2592000, "month": 2592000, "months": 2592000,
        "y": 31536000, "yr": 31536000, "year": 31536000, "years": 31536000,
    }

    duration_str = duration_str.lower().replace(",", " ").strip()

    pattern = r"(\d+(?:\.\d+)?)\s*(s|sec|second|seconds|m|min|minute|minutes|h|hr|hour|hours|d|day|days|w|week|weeks|mo|month|months|y|yr|year|years)\b"
    matches = re.findall(pattern, duration_str)
    if not matches:
        raise ValueError(f"Invalid duration format: {duration_str}")

    total_seconds = 0
    for amount_str, unit in matches:
        if unit not in multipliers:
            raise ValueError(f"Unknown time unit: {unit}")
        total_seconds += float(amount_str) * multipliers[unit]

    return int(total_seconds)

WORDS_TO_NUM = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}

def words_to_number(text: str) -> int | None:
    words = text.lower().replace("-", " ").split()
    total, current = 0, 0

    for word in words:
        if word not in WORDS_TO_NUM:
            return None
        value = WORDS_TO_NUM[word]

        if value == 100:
            current *= value
        elif value >= 1000:
            current *= value
            total += current
            current = 0
        else:
            current += value

    return total + current

def parse_amount(amount_str: str) -> int | None:
    if not amount_str:
        return None

    s = amount_str.lower().replace(",", "").strip()
    multiplier = 1

    if any(word in s for word in WORDS_TO_NUM):
        result = words_to_number(s)
        if result is not None:
            return result

    if re.search(r"(k|thousand)$", s):
        multiplier = 1_000
        s = re.sub(r"(k|thousand)$", "", s)

    elif re.search(r"(m|mil|mm|million)$", s):
        multiplier = 1_000_000
        s = re.sub(r"(m|mil|mm|million)$", "", s)

    elif re.search(r"(b|bil|bn|billion)$", s):
        multiplier = 1_000_000_000
        s = re.sub(r"(b|bil|bn|billion)$", "", s)

    elif re.search(r"(t|tr|tril|trillion)$", s):
        multiplier = 1_000_000_000_000
        s = re.sub(r"(t|tr|tril|trillion)$", "", s)

    try:
        return int(float(s) * multiplier)
    except ValueError:
        return None
    
async def get_balance(uid: int, guild_id: int) -> int:
    user_id = f"{guild_id}-{uid}"
    data = await economy_col.find_one({"_id": user_id})
    return data.get("wallet", 0) if data else 0

async def add_balance(uid: int, guild_id: int, amount: int):
    user_id = f"{guild_id}-{uid}"
    await economy_col.update_one(
        {"_id": user_id},
        {"$inc": {"wallet": amount}},
        upsert=True
    )

async def subtract_balance(uid: int, guild_id: int, amount: int):
    user_id = f"{guild_id}-{uid}"
    await economy_col.update_one(
        {"_id": user_id},
        {"$inc": {"wallet": -amount}},
        upsert=True
    )

async def update_user_balance(uid: int, guild_id: int, amount: int):
    user_id = f"{guild_id}-{uid}"
    await economy_col.update_one(
        {"_id": user_id},
        {"$inc": {"wallet": amount}},
        upsert=True
    )

async def schedule_unmute(guild, member, remaining):
    try:
        await asyncio.sleep(remaining)

        if not guild:
            print("[schedule_unmute] Guild not found, skipping.")
            return

        member = guild.get_member(member.id)
        if not member:
            print(f"[schedule_unmute] Member {member.id} not found, likely left the server.")
            await mutes_col.delete_one({"guild_id": guild.id, "user_id": member.id})
            return

        mute_role = discord.utils.get(guild.roles, name="Muted")
        if mute_role and mute_role in member.roles:
            try:
                await member.remove_roles(mute_role, reason="Mute expired")
                print(f"[schedule_unmute] Auto-unmuted {member} in {guild.name}")
            except NotFound:
                print(f"[schedule_unmute] Member {member.id} not found during unmute.")
            except Exception as inner_e:
                print(f"[schedule_unmute role removal error] {inner_e}")

        await mutes_col.delete_one({"guild_id": guild.id, "user_id": member.id})

    except asyncio.CancelledError:
        print(f"[schedule_unmute] Task for {member.id} cancelled.")
    except Exception as e:
        print(f"[schedule_unmute error] {e}")
    
async def check_and_use_food_item(user_id, guild_id, item_id):
    user_data = await get_user(None, guild_id, user_id)
    inventory = user_data.get("inventory", [])
    
    item_found = False
    for i, item in enumerate(inventory):
        if isinstance(item, str) and item == item_id:
            inventory.pop(i)
            item_found = True
            break
    
    if item_found:
        await economy_col.update_one(
            {"_id": f"{guild_id}-{user_id}"},
            {"$set": {"inventory": inventory}},
            upsert=True
        )
        return True
    return False

async def get_work_cooldown_reduction(user_id, guild_id):
    if await check_and_use_food_item(user_id, guild_id, "energy_drink"):
        return 0.5
    return 1.0

async def get_earnings_multiplier(user_id, guild_id):
    if await check_and_use_food_item(user_id, guild_id, "lucky_cookie"):
        return 2.0
    return 1.0

async def get_crime_bonus(user_id, guild_id):
    if await check_and_use_food_item(user_id, guild_id, "coffee_cup"):
        return 0.25
    return 0.0

async def ensure_shop_items():
    initial_items = [
        {
            "_id": "fishing rod",
            "name": "Fishing Rod",
            "name_lower": "fishing rod",
            "price": 150,
            "description": "🎣 Needed to catch fish to earn coins."
        },
        {
            "_id": "laptop",
            "name": "Laptop",
            "name_lower": "laptop",
            "price": 500,
            "description": "💻 Needed to work the developer job."
        },
        {
            "_id": "pickaxe",
            "name": "Pickaxe",
            "name_lower": "pickaxe",
            "price": 500,
            "description": "⛏️ Needed to go mining."
        },
        {
            "_id": "rifle",
            "name": "Rifle",
            "name_lower": "rifle",
            "price": 500,
            "description": "🔫 Needed to go hunting."
        },
        {
            "_id": "pet_duck",
            "name": "Pet Duck",
            "name_lower": "pet duck",
            "price": 1000,
            "description": "🦆 Cool pet duck! Gives 30% luck for 3 uses on certain activities.",
            "uses_left": 3
        },
        {
            "_id": "energy_drink",
            "name": "Energy Drink",
            "name_lower": "energy drink",
            "price": 200,
            "description": "⚡ Reduces work cooldown by 50% for your next work session. One-time use.",
            "uses_left": 1
        },
        {
            "_id": "lucky_cookie",
            "name": "Lucky Cookie",
            "name_lower": "lucky cookie",
            "price": 150,
            "description": "🍪 Doubles your next work/beg earnings. One-time use.",
            "uses_left": 1
        },
        {
            "_id": "coffee_cup",
            "name": "Coffee Cup",
            "name_lower": "coffee cup",
            "price": 100,
            "description": "☕ Gives 25% bonus on your next crime success chance. One-time use.",
            "uses_left": 1
        },
    ]

    for item in initial_items:
        await shop_col.update_one(
            {"_id": item["_id"]},
            {"$set": item},
            upsert=True
        )
    print("✅ Shop synced with initial items.")

@tasks.loop(hours=1)
async def check_expired_drops():
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    query = {
        "claimed": False,
        "created_at": {"$lt": three_days_ago.isoformat()}
    }
    
    async for drop in drop_instances_col.find(query):
        try:
            guild = bot.get_guild(int(drop["guild_id"]))
            if not guild:
                continue

            channel = guild.get_channel(int(drop["channel_id"]))
            if not channel:
                continue

            message = await channel.fetch_message(int(drop["message_id"]))
            await message.delete()

        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error deleting drop message {drop['message_id']}: {e}")

        if not drop.get("staff_drop"):
            try:
                await add_balance(int(drop["author_id"]), int(drop["guild_id"]), int(drop["amount"]))
            except Exception as e:
                print(f"Error refunding drop {drop['_id']} to {drop['author_id']}: {e}")
        
        await drop_instances_col.delete_one({"_id": drop["_id"]})

@tasks.loop(hours=24)
async def periodic_cleanup():
    deleted = await cleanup_old_conversations()
    print(f"[DuckGPT] Cleanup complete: {deleted} old conversations removed.")

class DropClaimView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="drop_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = str(interaction.message.id)
        doc = await drop_instances_col.find_one({"message_id": msg_id})
        if not doc:
            await interaction.response.send_message("⚠️ This drop is no longer valid.", ephemeral=True)
            return
        if str(doc.get("author_id")) == str(interaction.user.id):
            await interaction.response.send_message("❌ You can't claim your own drop.", ephemeral=True)
            return
        if doc.get("claimed"):
            await interaction.response.send_message("⚠️ This drop has already been claimed.", ephemeral=True)
            return
        amount = int(doc.get("amount", 0))
        await drop_instances_col.update_one(
            {"message_id": msg_id},
            {"$set": {
                "claimed": True,
                "claimer_id": str(interaction.user.id),
                "claimed_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        await add_balance(interaction.user.id, interaction.guild.id, amount)
        button.disabled = True
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.description = f"Claimed by {interaction.user.mention} for 🪙 {amount:,}"
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"✅ You claimed 🪙 {amount:,}", ephemeral=True)

invite_cache = {}

session = None
@tasks.loop(hours=1)
async def cleanup_invite_cache():
    current_time = time.time()
    expired_keys = []
    
    for guild_id, cached_data in invite_cache.items():
        if isinstance(cached_data, tuple) and len(cached_data) == 2:
            cached_time, _ = cached_data
            if current_time - cached_time > INVITE_CACHE_DURATION * 2:
                expired_keys.append(guild_id)
        elif isinstance(cached_data, list):
            expired_keys.append(guild_id)
    
    for key in expired_keys:
        del invite_cache[key]
    
    if expired_keys:
        print(f"🧹 Cleaned up {len(expired_keys)} expired invite cache entries")

@tasks.loop(hours=1)
async def update_invite_cache():
    for guild in bot.guilds:
        try:
            await get_guild_invites(guild)
            await asyncio.sleep(10)
        except Exception as e:
            print(f"⚠️ Error updating invite cache for {guild.name}: {e}")

async def load_sticky_notes():
    print("📝 Loading sticky notes...")
    loaded_count = 0
    
    async for doc in sticky_col.find({}):
        try:
            guild = bot.get_guild(int(doc["guild"]))
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
            
            await sticky_col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"message": new_msg.id}}
            )
            
            last_sticky_msg[int(doc["channel"])] = new_msg.id
            
            loaded_count += 1
            
        except Exception as e:
            print(f"❌ Failed to load sticky note for {doc['guild']}-{doc['channel']}: {e}")
    
    print(f"✅ Loaded {loaded_count} sticky notes")

async def repost_sticky_note(channel_id, guild_id):
    doc = await sticky_col.find_one({"guild": str(guild_id), "channel": str(channel_id)})
    if not doc:
        return
    
    try:
        guild = bot.get_guild(int(guild_id))
        channel = guild.get_channel(int(channel_id))
        
        try:
            old_msg = await channel.fetch_message(doc["message"])
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass
        
        new_msg = await channel.send(doc["text"])
        
        await sticky_col.update_one(
            {"guild": str(guild_id), "channel": str(channel_id)},
            {"$set": {"message": new_msg.id}}
        )
        
    except Exception as e:
        print(f"❌ Failed to repost sticky note: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    await bot.process_commands(message)
    
    if not message.guild:
        return
    
    # Detect Discord's system boost messages and thank immediately per boost event
    try:
        if message.type in {
            discord.MessageType.premium_guild_subscription,
            discord.MessageType.premium_guild_tier_1,
            discord.MessageType.premium_guild_tier_2,
            discord.MessageType.premium_guild_tier_3,
        }:
            guild = message.guild
            config = await config_col.find_one({"guild": str(guild.id)})
            if config:
                boost_channel_id = config.get("boost_channel")
                boost_message = config.get("boost_message")
                channel = guild.get_channel(boost_channel_id) if boost_channel_id else message.channel
                if channel and boost_message:
                    booster = message.author  # Member who boosted
                    msg_content = (
                        boost_message
                        .replace("{username}", booster.name)
                        .replace("{mention}", booster.mention)
                        .replace("{server}", guild.name)
                        .replace("{boostcount}", str(guild.premium_subscription_count or 0))
                    )

                    embed = discord.Embed(
                        description=msg_content,
                        color=discord.Color.fuchsia(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.set_author(name="Boost Alert!", icon_url=booster.display_avatar.url)
                    embed.set_thumbnail(url=booster.display_avatar.url)

                    try:
                        sent = await channel.send(embed=embed)
                        emoji = config.get("boost_react_emoji")
                        if emoji:
                            try:
                                await sent.add_reaction(emoji)
                            except Exception:
                                pass
                    except Exception as e:
                        print(f"⚠️ Error sending boost thank-you in {guild.name}: {e}")
            # Do not process further for this message
            return
    except Exception as e:
        print(f"[boost message handler error] {e}")
    
    try:
        for user in message.mentions:
            doc = await afk_col.find_one({"_id": f"{message.guild.id}-{user.id}"})
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
        if not (content_lower.startswith('.afk') or content_lower.startswith('/afk')):
            afk_key = f"{message.guild.id}-{message.author.id}"
            doc = await afk_col.find_one({"_id": afk_key})
            if doc:
                await afk_col.delete_one({"_id": afk_key})

                original_nick = doc.get("original_nick")
                current_nick = message.author.display_name

                try:
                    if current_nick.startswith("[AFK]"):
                        await message.author.edit(nick=original_nick)
                except discord.Forbidden:
                    await message.channel.send(
                        "⚠️ I couldn't restore your nickname due to role hierarchy, but AFK is removed.",
                        delete_after=5
                    )
                except discord.HTTPException:
                    await message.channel.send(
                        "⚠️ Something went wrong while restoring your nickname, but AFK is removed."
                    )

                await message.channel.send(
                    f"✅ Welcome back, {message.author.mention}! AFK removed.",
                    delete_after=5
                )
    except Exception as e:
        print(f"[afk error] {e}")
    
    if bot.user in message.mentions:
        prompt = message.clean_content.replace(f"<@{bot.user.id}>", "").strip()
        if not prompt:
            prompt = "Quack!"

        ctx = await bot.get_context(message)

        if not ctx.guild:
            return await message.reply(" I can only assist you in servers, not in DMs!")

        await message.channel.typing()
        reply = await ask_duck_gpt(ctx, prompt)
        await message.reply(reply)
    
    try:
        doc = await sticky_col.find_one({"guild": str(message.guild.id), "channel": str(message.channel.id)})
        if doc:
            old_id = last_sticky_msg.get(message.channel.id)
            if old_id:
                try:
                    old = await message.channel.fetch_message(old_id)
                    await old.delete()
                except discord.NotFound:
                    print(f"[sticky note] Previous message {old_id} not found, creating new one")
                except discord.Forbidden:
                    print(f"[sticky note] No permission to delete message {old_id}")
                except Exception as e:
                    print(f"[sticky note delete error] {e}")

            sent = await message.channel.send(doc["text"])
            last_sticky_msg[message.channel.id] = sent.id
            
            await sticky_col.update_one(
                {"guild": str(message.guild.id), "channel": str(message.channel.id)},
                {"$set": {"message": sent.id}}
            )
    except Exception as e:
        print(f"[sticky note error] {e}")
    
    try:
        ticket_entry = await tickets_col.find_one(
            {"guild": str(message.guild.id), "channel_id": str(message.channel.id), "close_pending": True}
        )
        if ticket_entry:
            opener_id = int(ticket_entry.get("owner_id"))
            print(f"[ticket confirmation] Checking message from {message.author.id}, ticket opener: {opener_id}, pending: {ticket_entry.get('close_pending')}")
            
            if message.author.id == opener_id:
                if message.content.lower() == "cancel":
                    await tickets_col.update_one(
                        {"_id": ticket_entry["_id"]},
                        {"$set": {"close_pending": False}}
                    )
                    await message.channel.send("❌ Ticket close request canceled.")
                    return

                if message.content.lower() == "confirm":
                    opener = message.guild.get_member(opener_id)
                    if opener:
                        class DummyCtx:
                            def __init__(self, channel, author):
                                self.channel = channel
                                self.author = message.author
                                self.guild = message.guild
                        ctx = DummyCtx(message.channel, message.author)
                        await actually_close_ticket(ctx, opener, forced=False)
                        await tickets_col.delete_one({"_id": ticket_entry["_id"]})
                        await message.channel.send("✅ Ticket has been closed.")
                        return
                    else:
                        await message.channel.send("⚠️ Could not find ticket opener in server.")
                        print(f"[ticket confirmation] Could not find opener {opener_id} in guild")
            else:
                print(f"[ticket confirmation] Non-opener {message.author.id} tried to confirm ticket {ticket_entry['_id']}")

    except Exception as e:
        print(f"[ticket confirmation error] {e}")

@bot.event
async def on_ready():
    global invite_cache
    global session
    if getattr(bot, "views_loaded", False):
        return
    bot.views_loaded = True

    print(f"Logging in as {bot.user}...")
    
    print("⚠️ Automatic sync disabled - use ?sync manually")

    cleanup_invite_cache.start()
    update_invite_cache.start()
    print("🔄 Started invite cache management tasks")

    periodic_cleanup.start()
    check_expired_drops.start()

    # DuckParadise Booster Check Loop disabled (we now thank on system boost messages only)
    # if not check_boosters_loop.is_running():
    #     check_boosters_loop.start()
    #     print("🔄 Started booster check loop")

    if not check_reminders.is_running():
        check_reminders.start()
        print("🔄 Started reminders check loop")

    if not check_expired_mutes.is_running():
        check_expired_mutes.start()

    await load_sticky_notes()

    mute_role_name = "Muted"
    mute_role = None
    for guild in bot.guilds:
        mute_role = discord.utils.get(guild.roles, name=mute_role_name)
        if not mute_role:
            mute_role = await guild.create_role(name=mute_role_name)
            for ch in guild.channels:
                await ch.set_permissions(mute_role, speak=False, send_messages=False)

        async for doc in mutes_col.find({"guild_id": guild.id}):
            member = guild.get_member(doc["user_id"])
            if not member:
                continue
            mute_end = doc.get("mute_end")

            if mute_end:
                if isinstance(mute_end, str):
                    try:
                        mute_end = datetime.fromisoformat(mute_end)
                    except ValueError:
                        mute_end = datetime.strptime(mute_end, "%Y-%m-%d %H:%M:%S")

                if mute_end.tzinfo is None:
                    mute_end = mute_end.replace(tzinfo=timezone.utc)

                if datetime.now(timezone.utc) >= mute_end:
                    await mutes_col.delete_one({"_id": doc["_id"]})
                if mute_role and mute_role not in member.roles:
                    await member.add_roles(mute_role, reason="Reapplying mute after restart")


    async for doc in roles_col.find({}):
        guild_id = doc["_id"]
        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        role_ids = doc.get("roles", [])
        if not role_ids:
            continue

        view = RoleButtons(role_ids, guild_id, guild)
        bot.add_view(view)

    print("✅ Persistent role buttons loaded.")
    bot.add_view(DropClaimView())

    await load_sticky_messages()
    await load_onetime_channels()
    check_and_repost_stickies.start()

    asyncio.create_task(resume_giveaways(bot))
    now = datetime.now(timezone.utc)
    async for poll in polls_col.find({"end_time": {"$gt": now}}):
        try:
            channel = bot.get_channel(int(poll["channel_id"]))
            if not channel:
                continue
            msg = await channel.fetch_message(int(poll["message_id"]))
            view = PollView(poll["poll_id"], poll["options"])
            await msg.edit(view=view)
            print(f"🔄 Restored poll {poll['poll_id']}")
        except Exception as e:
            print(f"Failed to restore poll {poll['poll_id']}: {e}")

    if not check_polls.is_running():
        check_polls.start()

    await bot.wait_until_ready()

    for guild in bot.guilds:
        if not isinstance(guild, discord.Guild):
            continue

        try:
            current_invites = await get_guild_invites(guild)
            invite_cache[guild.id] = current_invites

            for invite in current_invites:
                await invites_col.update_one(
                    {"guild_id": str(guild.id), "code": invite.code},
                    {"$set": {
                        "inviter_id": str(invite.inviter.id) if invite.inviter else None,
                        "uses": invite.uses
                    }},
                    upsert=True
                )

        except Exception as e:
            invite_cache[guild.id] = []
            print(f"❌ Failed to fetch invites for guild {guild}: {e}")

    print("✅ Invite cache synced with MongoDB.")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="thetruck"
        )
    )

    if session is None:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    panels = await ticket_panels_col.find({}).to_list(length=None)
    for panel in panels:
        try:
            view = TicketPanelView(panel)
            if panel.get("message_id") and panel.get("channel_id"):
                try:
                    channel = bot.get_channel(int(panel["channel_id"]))
                    if channel:
                        message = await channel.fetch_message(panel["message_id"])
                        await message.edit(view=view)
                        print(f"✅ Reattached view to panel message {panel['message_id']}")
                        continue
                except Exception as e:
                    print(f"Could not reattach view to message {panel.get('message_id')}: {e}")
            
            bot.add_view(view)
            print(f"✅ Registered global view for panel {panel.get('panel_name')}")
        except Exception as e:
            print(f"Failed to register view for {panel.get('panel_name')}: {e}")

    await ensure_shop_items()
    print("✅ Shop synced with initial items.")

    guilds = await settings_col.distinct("guild")
    for guild_id in guilds:
        panels = await ticket_panels_col.find({"guild": str(guild_id)}).to_list(length=50)
        for panel_data in panels:
            try:
                view = TicketPanelView(panel_data)
                if panel_data.get("message_id") and panel_data.get("channel_id"):
                    try:
                        channel = bot.get_channel(int(panel_data["channel_id"]))
                        if channel:
                            message = await channel.fetch_message(panel_data["message_id"])
                            await message.edit(view=view)
                            continue
                    except Exception as e:
                        print(f"Could not reattach guild view to message {panel_data.get('message_id')}: {e}")
                
                bot.add_view(view)
            except Exception as e:
                print(f"Failed to register guild view for {panel_data.get('panel_name')}: {e}")

    print("✅ Persistent ticket panel views loaded.")

    print(f"🎉 Bot ready! Logged in as {bot.user}")

    async def sync_hybrid_commands():
        await asyncio.sleep(15)
        
        for guild in bot.guilds:
            if not isinstance(guild, discord.Guild):
                continue
            try:
                await bot.tree.sync(guild=guild)
                print(f"✅ Commands synced for guild {guild.name}")
                await asyncio.sleep(15)
            except discord.HTTPException as e:
                if e.status == 429:
                    retry_after = e.retry_after if hasattr(e, 'retry_after') else 120
                    print(f"⚠️ Guild sync rate limited for {guild.name}, waiting {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    try:
                        await bot.tree.sync(guild=guild)
                        print(f"✅ Commands synced for {guild.name} after retry")
                    except Exception as retry_e:
                        print(f"❌ Guild sync retry failed for {guild.name}: {retry_e}")
                else:
                    print(f"❌ Failed to sync commands for guild {guild.name}: {e}")
            except Exception as e:
                print(f"❌ Failed to sync commands for guild {guild.name}: {e}")

        await asyncio.sleep(30)
        try:
            await bot.tree.sync()
            print("✅ Global commands synced!")
            print(f"🎉 Bot ready! Logged in as {bot.user}")
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = e.retry_after if hasattr(e, 'retry_after') else 300
                print(f"⚠️ Global sync rate limited, waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                try:
                    await bot.tree.sync()
                    print("✅ Global commands synced after retry!")
                    print(f"🎉 Bot ready! Logged in as {bot.user}")
                except Exception as retry_e:
                    print(f"❌ Global sync retry failed: {retry_e}")
            else:
                print(f"❌ Failed to sync global commands: {e}")
        except Exception as e:
            print(f"❌ Failed to sync global commands: {e}")

        for cmd in bot.tree.walk_commands():
            print(f"📌 Registered command: {cmd.name}, guilds: {cmd.guild_ids}")
        
        
# 3. COMMANDS ==================================================
@bot.hybrid_command(name="staffset", description="Set the staff role. Owner-only.")
async def staffset(ctx, role: discord.Role):
    if ctx.author != ctx.guild.owner:
        return await ctx.send("❌ Only the server owner can set the staff role.")

    await settings_col.update_one(
        {"guild": str(ctx.guild.id)},
        {"$set": {"staff_role": role.id}},
        upsert=True
    )

    await ctx.send(f"✅ Staff role set to {role.mention}")

@bot.hybrid_command(name="staffget", description="Show the configured staff role. Staff-only.")
@staff_only()
async def staffget(ctx):
    doc = await settings_col.find_one({"guild": str(ctx.guild.id)})
    role = ctx.guild.get_role(doc.get("staff_role")) if doc else None
    if role:
        await ctx.send(f"ℹ️ Staff role is {role.mention}.")
    else:
        await ctx.send("⚠️ No staff role is currently set.")

async def run_flake8_lint(base_dir):
    try:
        config_path = os.path.join(base_dir, "flake8_config.txt")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "flake8", base_dir,
            "--config", config_path,
            "--exclude=.venv,__pycache__,build,dist",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return ["⚠️ `flake8` lint check timed out."]

        if process.returncode != 0 and stdout:
            return [f"❗ {line}" for line in stdout.decode().strip().splitlines()]
        return []
    except FileNotFoundError:
        return ["⚠️ `flake8` module not found; make sure it's in your `requirements.txt`."]

async def get_category_support_members(guild: discord.Guild, category_name: str):
    category_key = f"tickets:{category_name.lower()}"
    all_key = "tickets:all"

    docs = await staffperms_col.find({"guild": str(guild.id)}).to_list(None)

    member_ids = []
    for entry in docs:
        raw_perms = entry.get("permissions", [])

        perms = [p.lower() for p in raw_perms]

        if category_key in perms or all_key in perms or "all" in perms:
            member_ids.append(int(entry["user"]))

    members = []
    for mid in member_ids:
        m = guild.get_member(mid)
        if m:
            members.append(m)

    return members

async def has_staff_role(member: discord.Member, guild: discord.Guild) -> bool:
    doc = await settings_col.find_one({"guild": str(guild.id)})
    rid = doc.get("staff_role") if doc else None
    if not rid:
        return False
    role = guild.get_role(int(rid))
    return bool(role and role in member.roles)

async def get_ticket_button_permissions(guild_id: int):
    cursor = ticket_panels_col.find({"guild": str(guild_id)})
    categories = {}

    async for panel in cursor:
        buttons = panel.get("buttons", [])
        for btn in buttons:
            cat = btn.get("category_name")
            label = btn.get("label")
            emoji = btn.get("emoji")

            if cat:
                categories[cat] = {
                    "label": label or cat,
                    "emoji": emoji
                }

    options = []

    if categories:
        options.append(
            SelectOption(
                label="All Ticket Types",
                value="tickets:all",
                description="Access to ALL ticket types"
            )
        )

    for cat, info in categories.items():
        display = info["label"]
        emoji = info["emoji"]

        options.append(
            SelectOption(
                label=display,
                value=f"tickets:{cat}",
                description=f"Access to ticket type: {display}",
                emoji=emoji
            )
        )

    return options

class StaffPermissionSelect(ui.Select):
    def __init__(self, member: discord.Member, staffperms_col, guild_id: int, author_id: int, parent_view: ui.View):
        self.member = member
        self.staffperms_col = staffperms_col
        self.guild_id = guild_id
        self.author_id = author_id
        self.parent_view = parent_view

        super().__init__(
            placeholder="Loading ticket types...",
            min_values=1,
            max_values=1,
            options=[SelectOption(label="Loading...", value="loading")]
        )

        asyncio.create_task(self.load_options())

    async def load_options(self, message: discord.Message = None):
        base_options = [
            SelectOption(label="Kick", value="kick", description="Use the kick command"),
            SelectOption(label="Ban", value="ban", description="Use the ban command"),
            SelectOption(label="Mute", value="mute", description="Use the mute/unmute commands"),
            SelectOption(label="Stop Bot", value="stopbot", description="Lock the bot from responding"),
            SelectOption(label="Money Drop", value="money_drop", description="Use the drop command"),
            SelectOption(label="Other Moderation", value="other_moderation", description="warn / purge / slowmode / fine etc."),
        ]

        ticket_options = [
            SelectOption(
                label="Ticket Admin",
                value="tickets:admin",
                description="Manage ticket panels and admin actions"
            )
        ]
        categories = {}

        cursor = ticket_panels_col.find({"guild": str(self.guild_id)})
        async for panel in cursor:
            for btn in panel.get("buttons", []):
                cat = btn.get("category_name")
                label = btn.get("label")
                emoji = btn.get("emoji")
                if cat:
                    categories[cat] = {"label": label or cat, "emoji": emoji}

        if categories:
            ticket_options.append(
                SelectOption(
                    label="All Ticket Types",
                    value="tickets:all",
                    description="Access to ALL ticket categories"
                )
            )
            for cat, info in categories.items():
                ticket_options.append(
                    SelectOption(
                        label=info["label"],
                        value=f"tickets:{cat}",
                        description=f"Access to ticket type: {info['label']}",
                        emoji=info["emoji"]
                    )
                )

        base_options += ticket_options + [
            SelectOption(label="StickyNotes", value="stickynotes", description="stickynote / unstickynote"),
            SelectOption(label="Economy", value="economy", description="shop, addmoney, drop, etc."),
            SelectOption(label="Vanity", value="vanity", description="vanityroles, promoters"),
            SelectOption(label="Roles", value="roles", description="roleadd / claimable roles"),
            SelectOption(label="Config Changes", value="config", description="configure / editconfig / viewconfig"),
            SelectOption(label="Invites", value="invites", description="invitechannel / invites / invite removal"),
            SelectOption(label="Enable/Disable", value="toggle_commands", description="enable/disable/listdisabled"),
            SelectOption(label="Reaction Roles", value="reactionroles", description="reactionrole management"),
            SelectOption(label="Giveaways", value="giveaways", description="giveaway / reroll"),
            SelectOption(label="Give All Permissions", value="all", description="Grant everything")
        ]

        self.options = base_options
        self.max_values = len(base_options)
        self.placeholder = "Select staff permissions/categories to grant"

        if message is None and hasattr(self.parent_view, "message"):
            message = self.parent_view.message

        if message:
            try:
                await message.edit(view=self.parent_view)
            except Exception:
                pass

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who ran the command can use this menu.",
                ephemeral=True
            )
            return

        selected = self.values

        if "all" in [s.lower() for s in selected]:
            permissions = ["all"]
            perms_text = "✅ All permissions granted!"
        else:
            permissions = [p.lower() for p in selected]
            perms_text = f"✅ Granted permissions: `{', '.join(selected)}`"

        await self.staffperms_col.update_one(
            {"guild": str(self.guild_id), "user": str(self.member.id)},
            {"$set": {"permissions": permissions}},
            upsert=True
        )

        embed = discord.Embed(
            title="Permissions Updated",
            description=(
                f"{self.member.mention} has been updated:\n"
                f"{perms_text}\n\n"
                "You can change selections at any time; this menu does not expire."
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Configured by {interaction.user} • User ID: {self.member.id}")

        try:
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
        except Exception:
            await interaction.response.send_message(embed=embed, ephemeral=True)

class StaffPermissionView(ui.View):
    def __init__(self, member, staffperms_col, guild_id, author_id):
        super().__init__(timeout=None)
        self.select = StaffPermissionSelect(member, staffperms_col, guild_id, author_id, self)
        self.add_item(self.select)

    async def initialize(self, message):
        await self.select.load_options(message)

@bot.hybrid_command(name="staff", description="Give the staff role to a user (owner-only).")
async def staff(ctx, member: discord.Member):
    data = await settings_col.find_one({"guild": str(ctx.guild.id)})
    if not data or "staff_role" not in data:
        return await ctx.send("❌ No staff role has been set. Use `/staffset` first.")

    staff_role_id = data["staff_role"]
    staff_role = ctx.guild.get_role(staff_role_id)

    if not staff_role:
        return await ctx.send("⚠️ The saved staff role no longer exists on this server.")

    if ctx.author != ctx.guild.owner and ctx.author.id != 1059882387590365314:
        return await ctx.send("❌ Only the server owner and thetruck (for debugging purposes) can assign the staff role.")

    try:
        await member.add_roles(staff_role)
        await ctx.send(
            f"✅ {member.mention} has been given the {staff_role.mention} role!",
            allowed_mentions=AllowedMentions.none()
        )

        embed = discord.Embed(
            title="Configure Staff Permissions",
            description=(
                f"{ctx.author.mention}, use the dropdown below to configure which permission categories or commands\n"
                f"{member.mention} should have access to. You may select multiple. Choosing **Give All Permissions** will grant everything.\n\n"
                "Only the person who ran this command can use the dropdown. This menu will not expire."
            ),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Target: {member} • User ID: {member.id}")

        view = StaffPermissionView(member, staffperms_col, ctx.guild.id, ctx.author.id)
        msg = await ctx.send(embed=embed, view=view)

        await view.initialize(msg)

    except discord.Forbidden:
        await ctx.send("❌ I don’t have permission to assign that role.")
    except Exception as e:
        await ctx.send(f"⚠️ An error occurred: {e}")

@bot.hybrid_command(name="unstaff", description="Remove the staff role and permissions from a user.")
async def unstaff(ctx, member: discord.Member):
    data = await settings_col.find_one({"guild": str(ctx.guild.id)})
    if not data or "staff_role" not in data:
        return await ctx.send("❌ No staff role has been set. Use `/staffset` first.")

    staff_role_id = data["staff_role"]
    staff_role = ctx.guild.get_role(staff_role_id)

    if not staff_role:
        return await ctx.send("⚠️ The saved staff role no longer exists on this server.")

    if ctx.author != ctx.guild.owner and ctx.author.id != 1059882387590365314:
        return await ctx.send("❌ Only the server owner and thetruck (for debugging purposes) can remove the staff role.")

    try:
        if staff_role in member.roles:
            await member.remove_roles(staff_role)
            await ctx.send(f"✅ **{member.display_name}** no longer has the **{staff_role.name}** role.")
        else:
            await ctx.send(f"⚠️ **{member.display_name}** does not currently have the **{staff_role.name}** role.")

        result = await staffperms_col.delete_one({"guild": str(ctx.guild.id), "user": str(member.id)})
        if result.deleted_count > 0:
            await ctx.send(f"🗑️ Removed **{member.display_name}**’s saved staff permissions from the database.")
        else:
            await ctx.send(f"ℹ️ No saved staff permissions were found for **{member.display_name}**.")

    except discord.Forbidden:
        await ctx.send("❌ I don’t have permission to remove that role.")
    except Exception as e:
        await ctx.send(f"⚠️ An error occurred: {e}")

PERMISSION_COMMAND_MAP = {
    "kick": ["kick"],
    "ban": ["ban"],
    "mute": ["mute", "unmute"],
    "money_drop": ["drop"],
    "other_moderation": ["warn", "purge", "slowmode", "fine"],
    "stickynotes": ["stickynote", "unstickynote"],
    "economy": ["shop", "addmoney", "drop"],
    "vanity": ["vanityroles", "promoters"],
    "roles": ["roleadd", "claimableroles"],
    "config": ["configure", "editconfig", "viewconfig"],
    "invites": ["invitechannel", "invites", "removeinvite"],
    "toggle_commands": ["enable", "disable", "listdisabled"],
    "reactionroles": ["reactionrole"],
    "giveaways": ["giveaway", "reroll"],
    "tickets:admin": [
        "ticketsetup",
        "ticketpanel",
        "ticketaddbutton",
        "ticketeditbutton",
        "ticketdeletepanel",
        "ticketlist",
        "transcript",
        "transcriptsearch",
        "transcriptlist",
        "ticketadduser",
        "ticketremoveuser",
    ],
    "all": ["ALL COMMANDS"],
}

def format_permission_details(permissions: list[str]):
    if not permissions:
        return "No permissions"

    final = ""
    for p in permissions:
        cmds = PERMISSION_COMMAND_MAP.get(p, ["Unknown"])
        cmds_text = ", ".join(cmds)
        final += f"**• {p}** — `{cmds_text}`\n"

    return final

class ViewPermsView(discord.ui.View):
    def __init__(self, pages, author_id):
        super().__init__(timeout=120)
        self.pages = pages
        self.author_id = author_id
        self.index = 0

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the command user can use this menu.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction, button):
        self.index = 0
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction, button):
        if self.index > 0:
            self.index -= 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction, button):
        if self.index < len(self.pages) - 1:
            self.index += 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction, button):
        self.index = len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.primary)
    async def search(self, interaction, button):
        modal = ViewPermsSearchModal(self)
        await interaction.response.send_modal(modal)

class ViewPermsSearchModal(discord.ui.Modal, title="Search User"):
    def __init__(self, view_ref):
        super().__init__()
        self.view_ref = view_ref

        self.username = discord.ui.TextInput(
            label="Enter user ID",
            placeholder="Example: 1234567890123",
            required=True
        )
        self.add_item(self.username)

    async def on_submit(self, interaction):
        query = self.username.value.lower()

        for i, embed in enumerate(self.view_ref.pages):
            user_field = embed.fields[0].value
            if query in user_field.lower():
                self.view_ref.index = i
                await interaction.response.edit_message(embed=self.view_ref.pages[i], view=self.view_ref)
                return

        await interaction.response.send_message("❌ Could not find that user.", ephemeral=True)

@bot.hybrid_command(name="viewperms", description="View staff permissions for the server or a specific user.")
async def viewperms(ctx, member: discord.Member = None):
    guild_id = str(ctx.guild.id)

    if member:
        data = await staffperms_col.find_one({"guild": guild_id, "user": str(member.id)})
        perms = data.get("permissions", []) if data else []
        perms_lower = [p.lower() for p in perms]

        embed = discord.Embed(
            title=f"Permissions for {member.display_name}",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=False)
        embed.add_field(name="Permissions", value=format_permission_details(perms_lower), inline=False)

        return await ctx.send(embed=embed)

    docs = await staffperms_col.find({"guild": guild_id}).to_list(None)

    if not docs:
        return await ctx.send("ℹ️ No staff permissions found in this server.")

    docs.sort(key=lambda x: x["user"])

    pages = []

    for entry in docs:
        user_id = int(entry["user"])
        member_obj = ctx.guild.get_member(user_id)

        if not member_obj:
            continue

        perms = entry.get("permissions", [])
        perms_lower = [p.lower() for p in perms]

        embed = discord.Embed(
            title=f"Staff Permissions — {member_obj.display_name}",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=member_obj.display_avatar.url)
        embed.add_field(name="User", value=f"{member_obj.mention}\n`{member_obj.id}`", inline=False)
        embed.add_field(name="Permissions", value=format_permission_details(perms_lower), inline=False)
        embed.set_footer(text=f"{ctx.guild.name} • {len(pages) + 1}/{len(docs)}")

        pages.append(embed)

    view = ViewPermsView(pages, ctx.author.id)
    await ctx.send(embed=pages[0], view=view)

@bot.command()
@staff_only()
async def debug(ctx):
    await ctx.send("🧪 Scanning bot code for issues... This may take a moment.")

    async def run_debug_checks():
        errors = []
        base_dir = os.path.dirname(os.path.abspath(__file__))

        def syntax_check():
            syntax_errors = []
            for root, _, files in os.walk(base_dir):
                for file in files:
                    if file.endswith(".py"):
                        file_path = os.path.join(root, file)
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                source = f.read()
                            ast.parse(source, filename=file_path)
                            compile(source, file_path, 'exec')
                        except SyntaxError as e:
                            syntax_errors.append(f"❌ `{file_path}`: SyntaxError at line {e.lineno} - {e.msg}")
                        except Exception as e:
                            syntax_errors.append(f"⚠️ `{file_path}`: {type(e).__name__} - {e}")
            return syntax_errors

        syntax_errors = await asyncio.to_thread(syntax_check)
        lint_errors = await run_flake8_lint(base_dir)
        return syntax_errors + lint_errors

    errors = await run_debug_checks()

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

async def get_or_create_blacklist_role(guild: discord.Guild, settings: dict):
    role = None

    if "blacklist_role" in settings:
        role = discord.utils.get(guild.roles, id=settings["blacklist_role"])
    
    if role is None:
        role = discord.utils.get(guild.roles, name="Blacklist")

    if role is None:
        role = await guild.create_role(
            name="Blacklist",
            colour=discord.Colour(0x000000),
            reason="Blacklist role created automatically by bot"
        )

    await settings_col.update_one(
        {"guild": str(guild.id)},
        {"$set": {"blacklist_role": role.id}},
        upsert=True
    )
    return role


async def resolve_member(ctx: commands.Context, member_str: str) -> discord.Member | None:
    try:
        member_id = int(member_str)
        member = ctx.guild.get_member(member_id)
        if member:
            return member
    except ValueError:
        pass

    if member_str.startswith("<@") and member_str.endswith(">"):
        member_id = int(member_str.replace("<@", "").replace("!", "").replace(">", ""))
        member = ctx.guild.get_member(member_id)
        if member:
            return member

    member = discord.utils.get(ctx.guild.members, name=member_str)
    return member

@bot.hybrid_command(name="blacklist", description="Blacklist a user from bot commands. Staff-only.")
@staffperm("other_moderation")
@staff_only()
async def blacklist(ctx, member: discord.Member):
    guild_id = str(ctx.guild.id)
    settings = await settings_col.find_one({"guild": guild_id})
    if not settings:
        settings = {"guild": guild_id}
        await settings_col.insert_one(settings)

    role = await get_or_create_blacklist_role(ctx.guild, settings)

    try:
        await member.add_roles(role, reason=f"Blacklisted by {ctx.author}")

        await log_action(ctx, f"{member.mention} has been blacklisted from using bot commands.", user_id=member.id, action_type="Blacklist")

        await ctx.send(f"🚫 {member.mention} has been blacklisted from using bot commands.")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to add that role.")
    except Exception as e:
        await ctx.send(f"❌ Failed to add blacklist role: {e}")


@bot.hybrid_command(name="whitelist", description="Remove a user from the blacklist. Staff-only.")
@staffperm("other_moderation")
@staff_only()
async def whitelist(ctx, member: discord.Member):
    guild_id = str(ctx.guild.id)
    settings = await settings_col.find_one({"guild": guild_id})
    if not settings:
        await ctx.send("⚠️ No settings found for this server.")
        return

    role = await get_or_create_blacklist_role(ctx.guild, settings)

    try:
        if role in member.roles:
            await member.remove_roles(role, reason=f"Unblacklisted by {ctx.author}")

        await log_action(ctx, f"{member.mention} has been removed from the blacklist.", user_id=member.id, action_type="Whitelist")

        await ctx.send(f"✅ {member.mention} has been whitelisted.")
    except discord.Forbidden:
        await ctx.send("❌ I don’t have permission to remove that role.")
    except Exception as e:
        await ctx.send(f"❌ Failed to remove blacklist role: {e}")

@blacklist.error
async def blacklist_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need **Manage Roles** permission to use this command.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid user specified.")
    else:
        await ctx.send(f"⚠️ An error occurred: {error}")

@whitelist.error
async def whitelist_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need **Manage Roles** permission to use this command.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid user specified.")
    else:
        await ctx.send(f"⚠️ An error occurred: {error}")

@bot.hybrid_command(name="vanityroles", description="Track users with keyword in status. Staff-only.")
@app_commands.describe(role="Role to assign", log_channel="Channel to log changes", keyword="Keyword to track in status")
@staffperm("vanity")
@staff_only()
async def vanityroles(ctx, role: discord.Role, log_channel: discord.TextChannel, keyword: str):
    guild = str(ctx.guild.id)
    await vanity_col.update_one(
        {"guild": guild},
        {"$set": {"role": role.id, "log": log_channel.id, "keyword": keyword, "users": []}},
        upsert=True
    )
    await ctx.send(f"✅ Vanity role set for '{keyword}' → {role.mention}")

class PromotersView(View):
    def __init__(self, ctx, mentions, per_page=10):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.mentions = mentions
        self.per_page = per_page
        self.page = 0

        self.update_buttons()

    def get_page_data(self):
        start = self.page * self.per_page
        end = start + self.per_page
        return self.mentions[start:end]

    def make_embed(self):
        total_pages = max(1, (len(self.mentions) + self.per_page - 1) // self.per_page)
        desc = "\n".join(self.get_page_data()) or "None"
        embed = discord.Embed(
            title="📢 Current Promoters",
            description=desc,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages}")
        return embed

    def update_buttons(self):
        total_pages = max(1, (len(self.mentions) + self.per_page - 1) // self.per_page)
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= total_pages - 1

    async def disable_all(self, interaction=None, message=None):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if interaction:
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
        elif message:
            await message.edit(embed=self.make_embed(), view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ You can't control this menu.", ephemeral=True)

        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ You can't control this menu.", ephemeral=True)

        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    async def on_timeout(self):
        await self.disable_all(message=self.message)


@bot.hybrid_command(name="promoters", description="View users with the vanity role. Staff-only.")
@staffperm("vanity")
@staff_only()
async def promoters(ctx):
    data = await vanity_col.find_one({"guild": str(ctx.guild.id)})
    users = data.get("users", []) if data else []
    mentions = []
    for uid in users:
        member = ctx.guild.get_member(uid)
        if member:
            mentions.append(member.mention)

    view = PromotersView(ctx, mentions)
    msg = await ctx.send(embed=view.make_embed(), view=view)
    view.message = msg

@bot.hybrid_command(name="resetpromoters", description="Clear all users from the vanity role. Staff-only.")
@staffperm("vanity")
@staff_only()
async def resetpromoters(ctx):
    guild = str(ctx.guild.id)
    data = await vanity_col.find_one({"guild": guild})
    if not data:
        return await ctx.send("❌ No vanity config set.")

    await ctx.send("⚠️ Type exactly:\n`I confirm I want to reset all the promoters.`")
    try:
        msg = await bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30)
    except asyncio.TimeoutError:
        return await ctx.send("❌ Timeout - cancelled.")
    
    if msg.content.strip() != "I confirm I want to reset all the promoters.":
        return await ctx.send("❌ Confirmation failed - cancelled.")

    r = ctx.guild.get_role(data["role"])
    removed = 0
    for uid in data["users"]:
        m = ctx.guild.get_member(uid)
        if m and r in m.roles:
            await m.remove_roles(r, reason="reset promoters")
            removed += 1

    await vanity_col.update_one({"guild": guild}, {"$set": {"users": []}})
    await ctx.send(embed=discord.Embed(
        title="🔁 Promoters Reset",
        description=f"{removed} users removed. List cleared.",
        color=discord.Color.red()
    ))

@bot.event
async def on_presence_update(before, after):
    if not check_all_statuses.is_running():
        check_all_statuses.start()
    if after.bot or not after.guild:
        return

    if after.status == discord.Status.offline:
        return

    data = await vanity_col.find_one({"guild": str(after.guild.id)})
    if not data:
        return

    keyword = data["keyword"].lower()
    status = before.activity.name.lower() if before.activity and before.activity.name else ""
    new_status = after.activity.name.lower() if after.activity and after.activity.name else ""
    role = after.guild.get_role(data["role"])
    log_ch = after.guild.get_channel(data["log"])
    has_role = role in after.roles

    if keyword not in status and keyword in new_status and not has_role:
        await after.add_roles(role, reason="vanity match")
        await vanity_col.update_one({"guild": str(after.guild.id)}, {"$addToSet": {"users": after.id}})
        if log_ch:
            await log_ch.send(embed=discord.Embed(
                title="Vanity Added ✨",
                description=(
                    f"{after.mention} has been awarded **{role.name}** "
                    f"for proudly displaying our vanity `{keyword}` in their status!"
                ),
                color=discord.Color.magenta(),
                timestamp=datetime.now(timezone.utc)
            ).set_thumbnail(url=after.display_avatar.url))

    elif keyword in status and keyword not in new_status and has_role:
        await after.remove_roles(role, reason="vanity lost")
        await vanity_col.update_one({"guild": str(after.guild.id)}, {"$pull": {"users": after.id}})
        if log_ch:
            await log_ch.send(embed=discord.Embed(
                title="Vanity Removed",
                description=(
                    f"{after.mention} has lost **{role.name}** for no longer "
                    f"displaying our vanity `{keyword}`."
                ),
                color=discord.Color.light_gray(),
                timestamp=datetime.now(timezone.utc)
            ).set_thumbnail(url=after.display_avatar.url))
            
@tasks.loop(seconds=0.01)
async def check_all_statuses():
    for guild in bot.guilds:
        data = await vanity_col.find_one({"guild": str(guild.id)})
        if not data:
            continue

        keyword = data["keyword"].lower()
        role = guild.get_role(data["role"])
        log_ch = guild.get_channel(data["log"])

        if not role:
            continue

        for member in guild.members:
            if member.bot or member.status == discord.Status.offline:
                continue

            status = (member.activity.name.lower() if member.activity and member.activity.name else "")
            has_role = role in member.roles

            if keyword in status and not has_role:
                await member.add_roles(role, reason="Vanity match (auto-check)")
                await vanity_col.update_one({"guild": str(guild.id)}, {"$addToSet": {"users": member.id}})
                if log_ch:
                    await log_ch.send(embed=discord.Embed(
                        title="Vanity Added ✨",
                        description=(
                            f"{member.mention} has been awarded **{role.name}**\n"
                            f"For displaying `{keyword}` in their status!"
                        ),
                        color=discord.Color.magenta(),
                        timestamp=datetime.now(UTC)
                    ).set_thumbnail(url=member.display_avatar.url))
                    await repost_sticky_note(log_ch.id, guild.id)

            elif keyword not in status and has_role:
                await member.remove_roles(role, reason="Vanity removed (auto-check)")
                await vanity_col.update_one({"guild": str(guild.id)}, {"$pull": {"users": member.id}})
                if log_ch:
                    await log_ch.send(embed=discord.Embed(
                        title="Vanity Removed",
                        description=(
                            f"{member.mention} lost **{role.name}** for no longer "
                            f"displaying `{keyword}` in their status."
                        ),
                        color=discord.Color.light_gray(),
                        timestamp=datetime.now(UTC)
                    ).set_thumbnail(url=member.display_avatar.url))
                    await repost_sticky_note(log_ch.id, guild.id)

@bot.hybrid_command(name="invitechannel", description="Set the channel where invite joins are announced.")
@staffperm("invites")
@staff_only()
async def invitechannel(ctx, channel: discord.TextChannel):
    await invite_config_col.update_one(
        {"guild_id": str(ctx.guild.id)},
        {"$set": {"channel_id": str(channel.id)}},
        upsert=True
    )
    await ctx.send(f"✅ Invite announcements will now be sent in {channel.mention}.")

@bot.hybrid_command(name="invites", description="Check how many invites a user has.")
@app_commands.describe(member="The user to check (optional - shows your invites if not provided)")
@staffperm("invites")
@blacklist_barrier()
async def invites(ctx, member: discord.Member = None):
    member = member or ctx.author
    stats = await invites_col.find_one({"guild_id": str(ctx.guild.id), "user_id": str(member.id)}) or {}
    regular = stats.get("regular", 0)
    fake = stats.get("fake", 0)
    leaves = stats.get("leaves", stats.get("left", 0))
    total_display = regular + leaves

    embed = discord.Embed(
        title=f"📨 Invite Stats for {member.display_name}",
        color=discord.Color.blurple()
    )
    embed.add_field(name="✨ Total Invites", value=total_display, inline=False)
    embed.add_field(name="✅ Regular", value=regular, inline=True)
    embed.add_field(name="❌ Leaves", value=leaves, inline=True)
    embed.add_field(name="⚠️ Fake", value=fake, inline=True)

    await ctx.send(embed=embed)

@bot.hybrid_command(name="removeinvites", aliases=["delinvites"], description="Remove a certain number of invites from a user.")
@app_commands.describe(member="The user to remove invites from", amount="Number of invites to remove")
@staffperm("invites")
@staff_only()
async def removeinvites(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        return await ctx.send("❌ Please provide a **positive number** of invites to remove.")

    guild_id = str(ctx.guild.id)
    user_id = str(member.id)

    stats = await invites_col.find_one({"guild_id": guild_id, "user_id": user_id})

    if not stats:
        return await ctx.send(f"❌ {member.mention} has no invite records.")

    total = stats.get("total", 0)
    regular = stats.get("regular", 0)
    fake = stats.get("fake", 0)
    leaves = stats.get("leaves", stats.get("left", 0))

    if total <= 0:
        return await ctx.send(f"❌ {member.mention} already has **0 invites**.")

    to_remove = amount

    if regular > 0:
        removed = min(regular, to_remove)
        regular -= removed
        to_remove -= removed

    if to_remove > 0 and fake > 0:
        removed = min(fake, to_remove)
        fake -= removed
        to_remove -= removed

    if to_remove > 0 and leaves > 0:
        removed = min(leaves, to_remove)
        leaves -= removed
        to_remove -= removed

    new_total = max(regular - leaves, 0)
    new_total = max(new_total, 0)

    await invites_col.update_one(
        {"guild_id": guild_id, "user_id": user_id},
        {"$set": {
            "regular": regular,
            "fake": fake,
            "leaves": leaves,
            "total": new_total
        }}
    )

    await ctx.send(f"✅ Removed **{amount} invites** from {member.mention}. New total: **{new_total}**")

@bot.hybrid_command(name="inviteleaderboard", aliases=["invitelb"], description="Show the top inviters in the server.")
@blacklist_barrier()
async def inviteleaderboard(ctx, limit: int = 10):
    guild_id = str(ctx.guild.id)
    totals = {}
    async for code_doc in invites_col.find({"guild_id": guild_id, "inviter_id": {"$ne": None}}):
        inviter_id = code_doc.get("inviter_id")
        if not inviter_id:
            continue
        try:
            totals[inviter_id] = totals.get(inviter_id, 0) + int(code_doc.get("uses", 0))
        except Exception:
            pass
    if not totals:
        return await ctx.send("❌ No invite data found yet.")
    leaves_map = {}
    async for stats_doc in invites_col.find({"guild_id": guild_id, "user_id": {"$in": list(totals.keys())}}):
        inviter = stats_doc.get("user_id")
        leaves_map[inviter] = stats_doc.get("leaves", stats_doc.get("left", 0))
    sorted_inv = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    embed = discord.Embed(
        title=f"🏆 Top {limit} Inviters in {ctx.guild.name}",
        color=discord.Color.gold()
    )
    rank = 1
    for inviter_id, joins in sorted_inv:
        uid_int = int(inviter_id)
        member = ctx.guild.get_member(uid_int)
        if member:
            username = member.display_name
        else:
            fetched = await ctx.bot.fetch_user(uid_int)
            username = getattr(fetched, "name", f"Unknown ({uid_int})") if fetched else f"Unknown ({uid_int})"
        leaves = leaves_map.get(inviter_id, 0)
        total = max(joins - leaves, 0)
        embed.add_field(
            name=f"#{rank} {username}",
            value=f"✅ {joins} joins | ❌ {leaves} leaves → **{total} net**",
            inline=False
        )
        rank += 1
    await ctx.send(embed=embed)

button_cooldowns = {}

@bot.command()
@staffperm("invites")
@staff_only()
async def resetinvites(ctx):
    guild_id = str(ctx.guild.id)
    stats_res = await invites_col.delete_many({"guild_id": guild_id, "user_id": {"$exists": True}})
    upd_res = await invites_col.update_many({"guild_id": guild_id, "code": {"$exists": True}}, {"$set": {"joined_users": []}})
    try:
        current_invites = await get_guild_invites(ctx.guild)
        for invite in current_invites:
            await invites_col.update_one(
                {"guild_id": guild_id, "code": invite.code},
                {"$set": {
                    "inviter_id": str(invite.inviter.id) if invite.inviter else None,
                    "uses": invite.uses
                }},
                upsert=True
            )
    except Exception:
        pass
    await ctx.send(f"✅ Reset invites for this server.\nCleared {stats_res.deleted_count} inviter records and refreshed {upd_res.modified_count} invite codes.")

class DoorCountSelect(discord.ui.Select):
    def __init__(self, ctx, bet):
        self.ctx = ctx
        self.bet = bet
        self.bet_start_balance = 0
        options = [
            discord.SelectOption(label=str(i), description=f"Go through {i} doors") 
            for i in range(1, 6)
        ]
        super().__init__(
            placeholder="Select how many doors to go through...",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ This isn’t your game!", ephemeral=True
            )

        doors = int(self.values[0])
        await interaction.response.defer()

        for child in self.view.children:
            child.disabled = True

        embed = discord.Embed(
            title=f"🚪 Door Game — {doors} Doors",
            description=(
                f"You’ve bet **{add_suffix(self.bet)} coins** and will go through **{doors} doors.**\n\n"
                "Each door gets harder — more risk, less reward. Good luck!"
            ),
            color=0xffa500
        )
        embed.set_footer(text="Click a door to begin your journey!")

        self.view.stop()

        await interaction.edit_original_response(
            embed=embed,
            view=DoorGameButton(
                self.ctx,
                str(self.ctx.author.id),
                str(self.ctx.guild.id),
                self.bet,
                doors,
                1,
                self.bet_start_balance
            )
        )

    async def set_start_balance(self, balance: int):
        self.bet_start_balance = balance

class DoorGameButton(discord.ui.View):
    def __init__(self, ctx, uid, guild_id, bet, total_doors, current_door, current_balance):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.uid = uid
        self.guild_id = guild_id
        self.bet = bet
        self.total_doors = total_doors
        self.current_door = current_door
        self.current_balance = current_balance
        self.add_buttons()

    def add_buttons(self):
        for i in range(1, 4):
            button = discord.ui.Button(
                label=f"🚪 Door {i}",
                style=discord.ButtonStyle.blurple,
                custom_id=str(i)
            )
            button.callback = self.door_clicked
            self.add_item(button)

    async def door_clicked(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.uid:
            return await interaction.response.send_message("❌ This isn’t your game!", ephemeral=True)

        current_time = time.time()
        key = (self.guild_id, self.uid)
        last_click_time = button_cooldowns.get(key, 0)
        if current_time - last_click_time < 2:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="⏳ Cooldown",
                    description="Please wait **2 seconds** before clicking again!",
                    color=0xff0000
                ),
                ephemeral=True
            )
        button_cooldowns[key] = current_time

        await interaction.response.defer()

        chosen_door = int(interaction.data["custom_id"])
        stage = self.current_door

        lose_chance = min(30 + (stage * 10), 80)
        half_chance = min(50 + (stage * 5), 90)
        win_chance = max(100 - (lose_chance + half_chance), 5)

        weighted_outcomes = (
            ["x3"] * win_chance +
            ["0.5x"] * half_chance +
            ["0x"] * lose_chance
        )
        outcome = random.choice(weighted_outcomes)

        if outcome == "x3":
            await add_balance(int(self.uid), int(self.guild_id), self.bet * 3)
            self.current_balance += self.bet * 3
            result_text = (
                f"🎉 **Door {chosen_door} tripled your bet!**\n"
                f"You now have `{add_suffix(self.current_balance)}` coins!"
            )
            color = 0x4dff58
        elif outcome == "0.5x":
            await add_balance(int(self.uid), int(self.guild_id), int(self.bet * 0.5))
            self.current_balance += int(self.bet * 0.5)
            result_text = (
                f"😅 **Door {chosen_door} gave half back.**\n"
                f"You now have `{add_suffix(self.current_balance)}` coins."
            )
            color = 0xfff93d
        else:
            result_text = (
                f"💀 **Door {chosen_door} took your bet!**\n"
                f"You now have `{add_suffix(max(self.current_balance - self.bet, 0))}` coins."
            )
            color = 0xff6b6b

            new_balance = max(self.current_balance - self.bet, 0)
            embed = discord.Embed(
                title=f"🚪 Door {self.current_door}/{self.total_doors} Result",
                description=result_text,
                color=color
            )
            embed.add_field(name="Final Result", value="💀 You lost your bet! Game over!", inline=False)
            embed.set_footer(text=f"Played by {interaction.user.name}")

            for child in self.children:
                child.disabled = True

            self.stop()
            await subtract_balance(int(self.uid), int(self.guild_id), self.bet)

            await interaction.edit_original_response(embed=embed, view=self)
            return

        for child in self.children:
            child.disabled = True

        game_over = self.current_door >= self.total_doors
        if game_over:
            final_msg = f"🏁 **Game Over!** You finished with `{add_suffix(self.current_balance)}` coins!"
            embed = discord.Embed(
                title=f"🚪 Door {self.current_door}/{self.total_doors} Result",
                description=result_text,
                color=color
            )
            embed.add_field(name="Final Result", value=final_msg, inline=False)
            embed.set_footer(text=f"Played by {interaction.user.name}")

            self.stop()

            await interaction.edit_original_response(embed=embed, view=self)
            return

        next_door = self.current_door + 1
        next_view = DoorGameButton(
            self.ctx, self.uid, self.guild_id, self.bet,
            self.total_doors, next_door, self.current_balance
        )

        next_embed = discord.Embed(
            title=f"🚪 Door {next_door}/{self.total_doors}",
            description="Choose your next door wisely...",
            color=0xffa500
        )
        next_embed.add_field(
            name="Current Balance",
            value=f"🪙 `{add_suffix(self.current_balance)}`",
            inline=True
        )
        next_embed.set_footer(text="It gets harder each door...")

        await interaction.edit_original_response(embed=next_embed, view=next_view)

@bot.hybrid_command(name="doorgame", description="Try your luck through multiple doors!")
@commands.cooldown(1, 5, commands.BucketType.member)
@blacklist_barrier()
async def doorgame(ctx):
    try:
        await ctx.send("💰 Please type your **bet amount** (e.g. `100`, `1k`, `1.5m`):")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            return await ctx.send("⌛ You took too long to respond. The game has been cancelled.")

        bet_str = msg.content.strip()

        try:
            bet = suffix_to_int(bet_str)
        except ValueError:
            return await ctx.send("❌ Invalid bet amount! Please enter a number like `100`, `1k`, or `1.5m`.")

        uid = str(ctx.author.id)
        guild_id = str(ctx.guild.id)

        user_doc = await economy_col.find_one({"_id": f"{guild_id}-{uid}"})
        wallet = user_doc.get("wallet", 0) if user_doc else 0

        if wallet < bet:
            return await ctx.send("❌ You don’t have enough coins for that bet!")

        await economy_col.update_one(
            {"_id": f"{guild_id}-{uid}"},
            {"$inc": {"wallet": -bet}},
            upsert=True
        )

        user_doc = await economy_col.find_one({"_id": f"{guild_id}-{uid}"})
        current_balance = user_doc.get("wallet", 0)

        select = DoorCountSelect(ctx, bet)
        await select.set_start_balance(current_balance)

        class DoorCountView(discord.ui.View):
            def __init__(self, ctx, select):
                super().__init__(timeout=30.0)
                self.ctx = ctx
                self.add_item(select)
                self.message = None

            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                try:
                    await self.message.edit(
                        content="⌛ You didn’t select a door count in time. Game cancelled.",
                        embed=None,
                        view=self
                    )
                except discord.Forbidden:
                    await self.ctx.send("⌛ You didn’t select a door count in time. Game cancelled.")

        embed = discord.Embed(
            title="🚪 Door Game Setup",
            description=(
                f"Your bet: **{add_suffix(bet)} coins**\n"
                f"Current Balance: `{add_suffix(current_balance)}`\n\n"
                "Now choose how many doors you want to go through:"
            ),
            color=0xffa500
        )

        view = DoorCountView(ctx, select)
        bot_msg = await ctx.send(embed=embed, view=view)
        view.message = bot_msg

    except Exception as e:
        await ctx.send("⚠️ Something went wrong while setting up the game. Contact thetruck.")
        print(f"[ERROR] doorgame setup: {type(e).__name__} - {e}")
        traceback.print_exc()

def calculate_mines_multiplier(minesamount: int, diamonds: int, houseedge: float) -> float:
    def nCr(n: int, r: int) -> int:
        if r > n or r < 0:
            return 0
        f = math.factorial
        return f(n) // f(r) // f(n - r)
    if minesamount >= 25:
        return 1.0
    denominator = nCr(25 - minesamount, diamonds)
    if denominator == 0:
        return 1.0
    return (1 - houseedge) * nCr(25, diamonds) / denominator

def format_with_suffix(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"{round(amount / 1_000_000_000, 1)}B"
    elif amount >= 1_000_000:
        return f"{round(amount / 1_000_000, 1)}M"
    elif amount >= 1_000:
        return f"{round(amount / 1_000, 1)}K"
    else:
        return str(round(amount, 1))

def generate_board(minesa: int) -> list[list[str]]:
    board = [["s" for _ in range(5)] for _ in range(5)]
    for _ in range(minesa):
        placed = False
        while not placed:
            row = random.randint(0, 4)
            col = random.randint(0, 4)
            if board[row][col] == "s":
                board[row][col] = "m"
                placed = True
    return board

async def get_player(uid: str):
    player = await minigameplayerdata_col.find_one({"_id": uid})
    if not player:
        player = {"_id": uid, "wallet": 0, "games_played": 0, "games_won": 0, "games_lost": 0}
        await minigameplayerdata_col.insert_one(player)
    return player

async def update_player(uid: str, update: dict):
    await minigameplayerdata_col.update_one({"_id": uid}, {"$set": update}, upsert=True)

async def inc_player(uid: str, update: dict):
    await minigameplayerdata_col.update_one({"_id": uid}, {"$inc": update}, upsert=True)

class MinesButtons(ui.View):
    def __init__(self, board, bombs, bet, userboard, usersafes, interaction, exploded, house_edge, message=None):
        super().__init__(timeout=None)
        self.board = board
        self.bombs = bombs
        self.bet = bet
        self.userboard = userboard
        self.usersafes = usersafes
        self.interaction = interaction
        self.exploded = exploded
        self.has_cashed_out = False
        self.max_safe_tiles = 25 - bombs
        self.house_edge = house_edge
        self.message = message
        self.setup_buttons()

    def setup_buttons(self):
        self.clear_items()
        for row in range(5):
            for col in range(5):
                square = self.userboard[row][col] if not self.exploded else self.board[row][col]
                custom_id = f"{row} {col}"

                if not self.exploded:
                    if square == "":
                        btn = ui.Button(label="\u200b", custom_id=custom_id, style=ButtonStyle.gray)
                        btn.callback = self.button_callback
                    elif square == "s":
                        btn = ui.Button(label="", custom_id=custom_id, style=ButtonStyle.green, emoji="<:Mines:1432423463141900319>")
                        btn.callback = self.button_cashout
                    elif square == "m":
                        btn = ui.Button(label="", custom_id=custom_id, style=ButtonStyle.red, emoji="<:bomb:1432424251574587503>")
                        btn.callback = self.button_cashout
                else:
                    if self.board[row][col] == "s":
                        btn = ui.Button(label="", custom_id=custom_id, style=ButtonStyle.green, emoji="<:Mines:1432423463141900319>")
                    elif self.board[row][col] == "m":
                        btn = ui.Button(label="", custom_id=custom_id, style=ButtonStyle.red, emoji="<:bomb:1432424251574587503>")
                    else:
                        btn = ui.Button(label="\u200b", custom_id=custom_id, style=ButtonStyle.gray)
                    btn.disabled = True

                self.add_item(btn)

    async def button_cashout(self, interaction: discord.Interaction):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer()

        row, col = map(int, interaction.data["custom_id"].split())

        if self.has_cashed_out:
            await interaction.followup.send("❌ You already cashed out!", ephemeral=True)
            return

        self.has_cashed_out = True

        multi = round(calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge), 2)
        winnings = round(self.bet * multi)
        await update_user_balance(interaction.user.id, interaction.guild.id, winnings)
        await inc_player(str(interaction.user.id), {"games_played": 1, "games_won": 1})

        embed = Embed(color=0x57ff5a, title=f":bomb: {self.bombs} Mines Cashed Out")
        next_multi = round(calculate_mines_multiplier(self.bombs, self.usersafes + 1, self.house_edge), 2)
        next_winnings = round(self.bet * next_multi)
        embed.add_field(
            name="Stats",
            value=f"💎 Bet: {format_with_suffix(self.bet)}\n"
                f"💰 Winnings: {format_with_suffix(winnings)}\n"
                f"📈 Multiplier: {multi}x\n"
                f"⏱ Next Click: {format_with_suffix(next_winnings)}"
        )

        self.exploded = True
        self.userboard[row][col] = "s"
        self.setup_buttons()
        await self.message.edit(embed=embed, view=self)

    async def button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return

        await interaction.response.defer()
        row, col = map(int, interaction.data["custom_id"].split())

        if self.userboard[row][col] != "":
            return

        if self.board[row][col] == "s":
            self.userboard[row][col] = "s"
            self.usersafes += 1

            multi = round(calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge), 2)
            next_multi = round(calculate_mines_multiplier(self.bombs, self.usersafes + 1, self.house_edge), 2)
            next_winnings = round(self.bet * next_multi)

            embed = Embed(color=0xffa500, title=f":bomb: {self.bombs} Mines")
            embed.add_field(
                name="Stats",
                value=f"💎 Bet: {format_with_suffix(self.bet)}\n"
                    f"💰 Winnings: {format_with_suffix(round(self.bet * multi))}\n"
                    f"📈 Multiplier: {multi}x\n"
                    f"⏱ Next Click: {format_with_suffix(next_winnings)}"
            )

            self.setup_buttons()
            await self.message.edit(embed=embed, view=self)

            if self.usersafes >= self.max_safe_tiles:
                await self.button_cashout(interaction)

        elif self.board[row][col] == "m":
            self.userboard[row][col] = "m"
            self.exploded = True
            await inc_player(str(interaction.user.id), {"games_played": 1, "games_lost": 1})

            embed = Embed(color=0xf53232, title=f":bomb: {self.bombs} Mines Exploded!")
            multi = round(calculate_mines_multiplier(self.bombs, self.usersafes, self.house_edge), 2)
            embed.add_field(
                name="Stats",
                value=f"💎 Bet: {format_with_suffix(self.bet)}\n"
                    f"💰 Lost: {format_with_suffix(round(self.bet * multi))}\n"
                    f"📉 Multiplier: {multi}x"
            )

            self.setup_buttons()
            await self.message.edit(embed=embed, view=self)

class MinesBombSelect(ui.Select):
    def __init__(self, ctx, bet, house_edge):
        self.ctx = ctx
        self.bet = bet
        self.house_edge = house_edge
        options = [SelectOption(label=str(i), description=f"{i} bombs") for i in range(1, 25)]
        super().__init__(placeholder="Select number of bombs", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return

        bombs = int(self.values[0])
        board = generate_board(bombs)
        userboard = [["" for _ in range(5)] for _ in range(5)]

        embed = Embed(color=0xffa500, title=f":bomb: {bombs} Mines")
        embed.add_field(
            name="Stats",
            value=f"💎 Bet: {format_with_suffix(self.bet)}\n"
                  f"💰 Winnings: {format_with_suffix(self.bet)}\n"
                  f"📈 Multiplier: 1.00x\n"
                  f"⏱ Next Click: {format_with_suffix(self.bet)}"
        )

        view = MinesButtons(board, bombs, self.bet, userboard, 0, interaction, False, self.house_edge)
        await interaction.response.defer()
        game_message = await interaction.followup.send(embed=embed, view=view)

        view.message = game_message

@bot.hybrid_command(name="mines", description="Play Mines and test your luck!")
@blacklist_barrier()
async def mines(ctx):
    await ctx.send("💎 How much would you like to bet? (Type a number or 'all')")

    def check_bet(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        bet_msg = await bot.wait_for("message", check=check_bet, timeout=60.0)
    except asyncio.TimeoutError:
        await ctx.send("⏱ You took too long to respond. Command cancelled.")
        return

    bet_input = bet_msg.content.strip()
    uid = ctx.author.id
    guild_id = ctx.guild.id

    user_balance = await get_balance(uid, guild_id)

    if bet_input.lower() == "all":
        bet = user_balance
    else:
        cleaned_bet = bet_input.replace(",", "").replace("$", "").strip().lower()

        if not any(ch.isdigit() for ch in cleaned_bet):
            await ctx.send("❌ Please enter a valid number (like `100`, `1k`, or `all`).")
            return

        try:
            bet = suffix_to_int(cleaned_bet)
        except ValueError:
            await ctx.send("❌ Invalid bet format! Try something like `500`, `1k`, or `2m`.")
            return

    if bet <= 0:
        await ctx.send("❌ Bet must be greater than 0.")
        return

    if bet > user_balance:
        await ctx.send("💎 You don’t have enough balance for that bet!")
        return

    await update_user_balance(uid, guild_id, -bet)

    house_edge = 0.15
    select = MinesBombSelect(ctx, bet, house_edge)
    view = ui.View()
    view.add_item(select)

    await ctx.send("🧨 Choose the number of bombs:", view=view)

async def ensure_user(uid: str):
    if not await is_registered(uid):
        await minigameplayerdata_col.insert_one({
            "_id": uid,
            "wins": 0,
            "losses": 0,
            "bets": []
        })

async def is_registered(uid: str) -> bool:
    user = await minigameplayerdata_col.find_one({"_id": uid})
    return user is not None

async def add_bet(uid: str, bet: int, win: int):
    await minigameplayerdata_col.update_one(
        {"_id": uid},
        {"$push": {"bets": {"bet": bet, "win": win}}},
        upsert=True
    )

async def update_game_stats(uid: str, result: str):
    if result == "win":
        await minigameplayerdata_col.update_one(
            {"_id": uid},
            {"$inc": {"wins": 1}},
            upsert=True
        )
    elif result == "loss":
        await minigameplayerdata_col.update_one(
            {"_id": uid},
            {"$inc": {"losses": 1}},
            upsert=True
        )

def get_towers_stake_multi(layer, difficulty):
    multipliers = {
        "Easy": [1.10, 1.25, 1.45, 1.70, 2.00],
        "Medium": [1.30, 1.60, 2.00, 2.50, 3.20],
        "Hard": [1.50, 2.00, 2.80, 4.00, 6.00]
    }
    base = multipliers.get(difficulty.capitalize(), multipliers["Easy"])
    return base[layer] if layer < len(base) else base[-1]

class DifficultySelect(discord.ui.Select):
    def __init__(self, ctx, bet):
        self.ctx = ctx
        self.bet = bet
        options = [
            discord.SelectOption(label="Easy", description="Low risk, low reward 🟢"),
            discord.SelectOption(label="Medium", description="Balanced challenge 🟡"),
            discord.SelectOption(label="Hard", description="High risk, high reward 🔴"),
        ]
        super().__init__(
            placeholder="🦆 Choose your difficulty...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This isn’t your game!", ephemeral=True)
            return

        difficulty = self.values[0].capitalize()

        await interaction.response.defer()
        await subtract_balance(self.ctx.author.id, self.ctx.guild.id, self.bet)

        embed = discord.Embed(
            title="🦆 **Duck Towers**",
            description=f"**Difficulty:** {difficulty}\n"
                        f"**Bet:** {add_suffix(self.bet)}\n"
                        f"**Multiplier:** 1.00x → {get_towers_stake_multi(0, difficulty)}x\n"
                        f"**Potential:** {add_suffix(round(self.bet * get_towers_stake_multi(0, difficulty)))}",
            color=0x3471eb,
        )
        embed.set_footer(text="Click a tile to begin!")

        view = DuckTowersView(self.ctx, self.bet, difficulty)
        view.message = await interaction.followup.send(embed=embed, view=view)

class DuckTowersView(discord.ui.View):
    def __init__(self, ctx, bet, difficulty):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bet = bet
        self.difficulty = difficulty.capitalize()
        self.layer = 0
        self.multi = 1
        self.safe_towers = []
        self.has_cashed_out = False
        self.buttons = []
        self.message = None
        self.setup_buttons()

    def setup_buttons(self):
        difficulty_settings = {
            "Easy": (4, 3),
            "Medium": (3, 2),
            "Hard": (3, 1)
        }
        towers_per_layer, safe_towers_per_layer = difficulty_settings[self.difficulty]
        for layer in range(5):
            safe_positions = random.sample(range(towers_per_layer), safe_towers_per_layer)
            self.safe_towers.append(safe_positions)
            row = 4 - layer
            layer_buttons = []
            for tower in range(towers_per_layer):
                btn = discord.ui.Button(
                    label="‎",
                    custom_id=f"{layer} {tower}",
                    style=discord.ButtonStyle.gray,
                    row=row
                )
                btn.callback = self.tower_clicked
                if layer != 0:
                    btn.disabled = True
                    btn.style = discord.ButtonStyle.blurple
                layer_buttons.append(btn)
                self.add_item(btn)
            self.buttons.append(layer_buttons)

    async def update_embed(self):
        next_multi = get_towers_stake_multi(self.layer, self.difficulty)
        potential = round(self.bet * next_multi)
        embed = discord.Embed(
            title="🦆 **Duck Towers**",
            description=f"**Difficulty:** {self.difficulty}\n"
                        f"**Bet:** {add_suffix(self.bet)}\n"
                        f"**Multiplier:** {self.multi}x → {next_multi}x\n"
                        f"**Potential:** {add_suffix(potential)}",
            color=0x3471eb
        )
        embed.set_footer(text="Click a tile to continue!")
        await self.message.edit(embed=embed, view=self)

    async def tower_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
            return
        await interaction.response.defer()
        layer, tower = map(int, interaction.data["custom_id"].split())
        if layer != self.layer:
            return
        is_safe = tower in self.safe_towers[layer]
        if not is_safe:
            self.buttons[layer][tower].emoji = "🥚"
            self.buttons[layer][tower].style = discord.ButtonStyle.red
            for group in self.buttons:
                for b in group:
                    b.disabled = True
            await self.message.edit(view=self)
            await asyncio.sleep(1.5)
            await add_bet(str(interaction.user.id), self.bet, 0)
            await update_game_stats(str(interaction.user.id), "loss")
            lose_embed = discord.Embed(
                title="💥 Game Over",
                description=f"**Bet:** {add_suffix(self.bet)}\n"
                            f"**Multiplier:** {self.multi}x\n"
                            f"**Winnings:** 0",
                color=0xff0000
            )
            lose_embed.set_footer(text="Try again!")
            await self.message.edit(embed=lose_embed, view=self)
            return
        self.buttons[layer][tower].emoji = "🦆"
        self.buttons[layer][tower].style = discord.ButtonStyle.green
        self.multi = get_towers_stake_multi(layer, self.difficulty)
        self.buttons[layer][tower].callback = self.cash_out
        if layer < 4:
            for b in self.buttons[layer + 1]:
                b.disabled = False
                b.style = discord.ButtonStyle.gray
        self.layer += 1
        if self.layer == 5:
            await self.cash_out(interaction)
            return
        await self.update_embed()

    async def cash_out(self, interaction: discord.Interaction):
        if self.has_cashed_out:
            return
        self.has_cashed_out = True
        winnings = round(self.bet * self.multi)
        await add_balance(interaction.user.id, self.ctx.guild.id, winnings)
        await add_bet(str(interaction.user.id), self.bet, winnings)
        await update_game_stats(str(interaction.user.id), "win")
        for row in self.buttons:
            for b in row:
                b.disabled = True
        embed = discord.Embed(
            title="💰 Cashed Out!",
            description=f"**Bet:** {add_suffix(self.bet)}\n"
                        f"**Winnings:** {add_suffix(winnings)}\n"
                        f"**Multiplier:** {self.multi}x",
            color=0x00ff00
        )
        embed.set_footer(text="Thanks for playing!")
        await self.message.edit(embed=embed, view=self)

@bot.hybrid_command(name="ducktowers", description="Play a game of Duck Towers!")
@commands.cooldown(1, 15, commands.BucketType.member)
@blacklist_barrier()
async def ducktowers(ctx):
    try:
        uid = ctx.author.id
        guild_id = ctx.guild.id
        await ensure_user(str(uid))

        await ctx.send("💎 How much would you like to bet? (Type a number, or 'all')")

        def check_bet(m):
            return m.author == ctx.author and m.channel == ctx.channel

        bet_msg = await bot.wait_for("message", check=check_bet, timeout=60.0)
        bet_input = bet_msg.content.strip()

        user_balance = await get_balance(uid, guild_id)
        bet = user_balance if bet_input.lower() == "all" else suffix_to_int(bet_input)

        if bet <= 0:
            return await ctx.send("❌ Bet must be greater than zero.")
        if bet > user_balance:
            return await ctx.send(f"💎 You only have `{add_suffix(user_balance)}`, not enough for that bet!")

        select = DifficultySelect(ctx, bet)
        view = discord.ui.View()
        view.add_item(select)
        await ctx.send("🦆 Choose your difficulty:", view=view)

    except asyncio.TimeoutError:
        await ctx.send("⌛ You took too long to respond — game canceled.")
    except ValueError:
        await ctx.send("⚠️ Invalid bet amount. Try again using a number or 'all'.")
    except Exception as e:
        await ctx.send("⚠️ Something went wrong while starting your Duck Towers game.")
        print(f"[ERROR] ducktowers command: {type(e).__name__} - {e}")

@bot.hybrid_command(name="balance", description="Check your balance.", aliases=["bal"])
@app_commands.describe(member_name="The member whose balance to check (optional - shows your balance if not provided)")
@blacklist_barrier()
async def balance(ctx, member_name: str = None):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        if not member_name:
            member = ctx.author
        else:
            member = None

            if member_name.isdigit():
                try:
                    member = await ctx.guild.fetch_member(int(member_name))
                except (discord.NotFound, discord.HTTPException):
                    pass

            if not member:
                mention_match = re.match(r"<@!?(\d+)>", member_name)
                if mention_match:
                    user_id = int(mention_match.group(1))
                    try:
                        member = await ctx.guild.fetch_member(user_id)
                    except (discord.NotFound, discord.HTTPException):
                        pass

            if not member:
                search_term = member_name.lower()
                matches = [
                    m for m in ctx.guild.members
                    if m.display_name.lower().startswith(search_term) or m.name.lower().startswith(search_term)
                ]
                if len(matches) == 0:
                    await ctx.send(f"⚠️ No members found matching `{member_name}`.")
                    return
                elif len(matches) > 1:
                    names = ", ".join([m.display_name for m in matches[:10]])
                    await ctx.send(f"⚠️ Multiple members found: {names}\nPlease be more specific.")
                    return
                else:
                    member = matches[0]

        data = await get_user(ctx, ctx.guild.id, member.id)
        wallet = data.get("wallet", 0)
        bank = data.get("bank", 0)

        wallet_display = f"🪙 {wallet}" if wallet >= 0 else f"🪙 -{abs(wallet)} ❌ (debt)"
        bank_display = f"🏦 {bank}"

        embed = discord.Embed(
            title=f"{member.display_name}'s Balance",
            color=discord.Color.gold()
        )
        embed.add_field(name="Wallet", value=wallet_display, inline=True)
        embed.add_field(name="Bank", value=bank_display, inline=True)

        user_id = f"{ctx.guild.id}-{member.id}"
        user_data = await economy_col.find_one({"_id": user_id}) or {}
        passive_until = user_data.get("passive_until")

        if passive_until:
            dt = datetime.fromisoformat(passive_until)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if dt > now:
                rem = dt - now
                hours = rem.seconds // 3600
                mins = (rem.seconds % 3600) // 60
                embed.add_field(
                    name="🛡️ Passive Mode",
                    value=f"Active for {rem.days}d {hours}h {mins}m",
                    inline=False
                )

        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send("⚠️ Something went wrong while fetching balance. Contact thetruck.")
        print(f"[ERROR] balance command: {type(e).__name__} - {e}")
        traceback.print_exc()
    
@bot.hybrid_command(name="daily", description="Claim your daily reward.", aliases=["collect"])
@blacklist_barrier()
async def daily(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        now = datetime.now(timezone.utc)

        last_daily = data.get("last_daily")
        if last_daily:
            try:
                last_time = datetime.fromisoformat(last_daily)
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
                
                time_since_last = now - last_time
                
                if time_since_last < timedelta(hours=24):
                    remaining = timedelta(hours=24) - time_since_last
                    hours = remaining.seconds // 3600
                    minutes = (remaining.seconds // 60) % 60
                    return await ctx.send(f"🕒 Claim again in {hours}h {minutes}m")
                
                current_streak = data.get("daily_streak", 0) + 1

            except Exception as e:
                print(f"[DAILY] Failed to parse timestamp: {e}")
                current_streak = 0
        else:
            current_streak = 0

        current_streak = min(current_streak, 30)
        
        if current_streak == 0:
            reward = 300
        else:
            reward = 50 * current_streak
        
        await add_balance(ctx.author.id, ctx.guild.id, reward)
        
        saved_streak = current_streak + 1 if current_streak == 0 else current_streak
        await economy_col.update_one(
            {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
            {"$set": {
                "last_daily": now.isoformat(),
                "daily_streak": saved_streak
            }}
        )
        
        embed = discord.Embed(
            title="🎁 Daily Reward Claimed!",
            description=f"💰 You earned **{reward} coins**!",
            color=discord.Color.gold(),
            timestamp=now
        )
        
        embed.add_field(
            name="🔥 Current Streak",
            value=f"Day **{current_streak}** of 30" if current_streak > 0 else "First time claiming!",
            inline=True
        )
        
        next_streak = current_streak + 1 if current_streak > 0 else 1
        embed.add_field(
            name="📈 Next Reward",
            value=f"**{50 * min(next_streak, 30)}** coins",
            inline=True
        )
        
        progress = current_streak / 30
        progress_bar = "🟦" * int(progress * 10) + "⬜" * (10 - int(progress * 10))
        embed.add_field(
            name="📊 Monthly Progress",
            value=f"{progress_bar} ({current_streak}/30 days)",
            inline=False
        )
        
        if current_streak == 0:
            embed.set_footer(text="🌟 Welcome bonus! Keep claiming daily for bigger rewards!")
        elif current_streak == 1:
            embed.set_footer(text="🔥 Streak started! Keep claiming daily for bigger rewards!")
        elif current_streak == 7:
            embed.set_footer(text="🔥 Week streak! You're on fire!")
        elif current_streak == 14:
            embed.set_footer(text="💎 Two weeks! Amazing consistency!")
        elif current_streak == 30:
            embed.set_footer(text="👑 Perfect month! Maximum reward achieved!")
        else:
            embed.set_footer(text=f"💪 Keep it up! {(30 - current_streak)} days to next reward!")
        
        await ctx.send(embed=embed)

    except Exception as e:
        print(f"[ERROR] daily command: {type(e).__name__} - {e}")
        traceback.print_exc()
        await ctx.send("⚠️ Something went wrong while collecting your daily. Contact thetruck.")
    
@bot.hybrid_command(name="beg", description="Beg for coins.")
@blacklist_barrier()
async def beg(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        now = datetime.now(timezone.utc)

        last_beg = data.get("last_beg")
        if last_beg:
            try:
                last_time = datetime.fromisoformat(last_beg)
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)

                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)

                if now - last_time < timedelta(minutes=15):
                    remaining = timedelta(minutes=15) - (now - last_time)
                    minutes = remaining.seconds // 60
                    return await ctx.send(f"🕒 You can beg again in {minutes} minutes.")
            except Exception as e:
                print(f"[BEG] Failed to parse timestamp: {e}")

        amount = random.randint(50, 200)
        
        earnings_multiplier = await get_earnings_multiplier(ctx.author.id, ctx.guild.id)
        
        inventory = data.get("inventory", [])
        duck_used = False
        for i, item in enumerate(inventory):
            if isinstance(item, dict) and item.get("_id") == "pet_duck":
                earnings_multiplier *= 1.3
                item["uses_left"] -= 1
                await ctx.send("🦆 Your Pet Duck boosted your begging earnings by 30%!")
                duck_used = True
                break
        
        amount = int(amount * earnings_multiplier)
        
        if duck_used:
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"inventory": inventory}},
                upsert=True
            )
        
        donor = random.choice(["thetruck", "CuteBatak"])
        await add_balance(ctx.author.id, ctx.guild.id, amount)
        await economy_col.update_one(
            {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
            {"$set": {"last_beg": now.isoformat(timespec="seconds")}}
        )
        msg = f"🙇 {donor} was kind enough to donate **{amount} coins** to you!"
        
        if earnings_multiplier > 1.0:
            msg += "\n🍪 **Lucky Cookie consumed!** Earnings doubled!"
            
        await ctx.send(msg)

    except Exception as e:
        print(f"[ERROR] beg command: {type(e).__name__} - {e}")
        traceback.print_exc()
        await ctx.send("⚠️ Something went wrong while begging. Contact thetruck.")
    
@bot.hybrid_command(name="deposit", description="Deposit to bank.", aliases=["dep"])
@app_commands.describe(amount="Amount to deposit (supports k, m, b suffixes or 'all')")
@blacklist_barrier()
async def deposit(ctx, amount: str):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        wallet = data["wallet"]

        if amount.lower() == "all":
            if wallet <= 0:
                return await ctx.send("❌ You have no coins to deposit.")
            deposit_amount = wallet
        elif amount.isdigit():
            deposit_amount = int(amount)
            if deposit_amount <= 0:
                return await ctx.send("❌ Invalid deposit amount.")
            if deposit_amount > wallet:
                return await ctx.send("❌ You can't afford that!")
        else:
            return await ctx.send("❌ Please enter a valid number or `all`.")

        taxed_amount = int(deposit_amount * 0.95)

        await economy_col.update_one(
            {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
            {"$set": {
                "wallet": wallet - deposit_amount,
                "bank": data["bank"] + taxed_amount
            }}
        )

        await ctx.send(
            f"🏦 You deposited {deposit_amount} coins.\n"
            f"💸 After 5% tax, you received {taxed_amount} coins in your bank."
        )

    except Exception as e:
        await ctx.send("⚠️ Something went wrong while processing your deposit. Contact thetruck.")
        print(f"[ERROR] deposit command: {type(e).__name__} - {e}")
        traceback.print_exc()

@bot.hybrid_command(name="withdraw", description="Withdraw from bank.", aliases=["with"])
@app_commands.describe(amount="Amount to withdraw (supports k, m, b suffixes or 'all')")
@blacklist_barrier()
async def withdraw(ctx, amount: str):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        bank = data["bank"]

        if amount.lower() == "all":
            if bank <= 0:
                return await ctx.send("❌ You have no coins to withdraw.")
            withdraw_amount = bank
        elif amount.isdigit():
            withdraw_amount = int(amount)
            if withdraw_amount <= 0:
                return await ctx.send("❌ Invalid withdrawal amount.")
            if withdraw_amount > bank:
                return await ctx.send("❌ You can't afford that")
        else:
            return await ctx.send("❌ Please enter a valid number or `all`.")

        await economy_col.update_one(
            {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
            {"$set": {
                "wallet": data["wallet"] + withdraw_amount,
                "bank": bank - withdraw_amount
            }}
        )
        await ctx.send(f"💰 You withdrew {withdraw_amount} coins.")

    except Exception as e:
        await ctx.send("⚠️ Something went wrong while processing your withdrawal. Contact thetruck.")
        print(f"[ERROR] withdraw command: {type(e).__name__} - {e}")
        traceback.print_exc()

async def process_shop_purchase(member, guild, store_item: dict, user_data: dict):
    item_name = store_item.get("name") or store_item.get("name_lower") or "Unknown Item"

    try:
        price = int(store_item.get("price", 0))
    except (TypeError, ValueError):
        return {"ok": False, "message": "❌ Invalid item price! Ask staff to fix this shop item."}

    if price <= 0:
        return {"ok": False, "message": "❌ Invalid item price! Ask staff to fix this shop item."}

    wallet = int(user_data.get("wallet", 0) or 0)
    inventory = list(user_data.get("inventory", []))
    user_key = f"{guild.id}-{member.id}"
    role_id = store_item.get("role_id")

    if role_id is not None:
        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            role = None

        if not role:
            return {"ok": False, "message": "❌ This item's linked role is invalid or was deleted. Ask staff to update it."}

        if role in getattr(member, "roles", []):
            return {"ok": False, "message": f"✅ You already have the role for **{item_name}**."}

        if wallet < price:
            return {"ok": False, "message": f"❌ You don’t have enough coins. **{item_name}** costs {price} coins."}

        new_wallet = wallet - price
        await economy_col.update_one({"_id": user_key}, {"$set": {"wallet": new_wallet}})

        try:
            await member.add_roles(role, reason=f"Purchased shop role item: {item_name}")
        except (discord.Forbidden, discord.HTTPException) as role_error:
            await economy_col.update_one({"_id": user_key}, {"$set": {"wallet": wallet}})
            return {"ok": False, "message": f"❌ Couldn't assign the role (`{role_error}`). You were refunded."}

        return {
            "ok": True,
            "message": f"✅ You bought **{item_name}** for {price} coins and got {role.mention}!",
            "item_name": item_name,
            "price": price,
            "old_wallet": wallet,
            "new_wallet": new_wallet,
            "purchase_type": "role",
            "role_mention": role.mention,
        }

    if wallet < price:
        return {"ok": False, "message": f"❌ You don’t have enough coins. **{item_name}** costs {price} coins."}

    new_wallet = wallet - price
    item_id = str(store_item.get("_id", ""))
    is_pet_duck = store_item.get("name_lower") == "pet_duck" or item_id == "pet_duck" or item_id.endswith("-pet_duck")

    if is_pet_duck:
        inventory.append({"_id": "pet_duck", "uses_left": 3})
        success_message = "🦆 You bought a Pet Duck! It has 3 uses. You can stack multiple ducks."
        purchase_type = "pet_duck"
    else:
        inventory.append(store_item.get("name_lower", item_name.lower()))
        success_message = f"✅ You bought **{item_name}** for {price} coins!"
        purchase_type = "inventory"

    await economy_col.update_one(
        {"_id": user_key},
        {"$set": {"wallet": new_wallet, "inventory": inventory}}
    )

    return {
        "ok": True,
        "message": success_message,
        "item_name": item_name,
        "price": price,
        "old_wallet": wallet,
        "new_wallet": new_wallet,
        "purchase_type": purchase_type,
    }

class ShopView(discord.ui.View):
    def __init__(self, user_id, guild_id, items, user_balance):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.guild_id = guild_id
        self.items = items
        self.balance = user_balance

    @discord.ui.button(label="🛒 Buy Items", style=discord.ButtonStyle.green, custom_id="buy_items_button")
    async def buy_items(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can't use this button!", ephemeral=True)
            return

        if not self.items:
            await interaction.response.send_message("❌ The shop is empty!", ephemeral=True)
            return

        options = []
        for item in self.items:
            display_name = item.get("name") or item.get("_id", "Unnamed Item")
            price = item.get("price", "Unknown")
            description = item.get("description", "No description available.")
            
            can_afford = "✅" if isinstance(price, (int, float)) and self.balance >= price else "❌"
            
            options.append(
                discord.SelectOption(
                    label=f"{display_name} - 🪙 {price}",
                    description=f"{description[:50]}..." if len(description) > 50 else description,
                    value=item["_id"],
                    emoji=can_afford
                )
            )

        view = ShopDropdown(self.user_id, self.guild_id, self.items, self.balance, options)
        embed = discord.Embed(
            title="🛒 Select Item to Buy",
            description=f"Your wallet: 🪙 {self.balance:,}\n\nChoose an item from the dropdown below:",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ShopDropdown(discord.ui.View):
    def __init__(self, user_id, guild_id, items, user_balance, options):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.guild_id = guild_id
        self.items = items
        self.balance = user_balance
        
        self.dropdown = discord.ui.Select(
            placeholder="Choose an item to buy...",
            options=options[:25]
        )
        self.dropdown.callback = self.dropdown_callback
        self.add_item(self.dropdown)

    async def dropdown_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can't use this dropdown!", ephemeral=True)
            return

        selected_item_id = self.dropdown.values[0]
        selected_item = next((item for item in self.items if item["_id"] == selected_item_id), None)

        if not selected_item:
            await interaction.response.send_message("❌ Item not found!", ephemeral=True)
            return

        try:
            guild_id = str(interaction.guild.id)
            user_data = await get_user(None, guild_id, interaction.user.id)
            result = await process_shop_purchase(interaction.user, interaction.guild, selected_item, user_data)

            if not result["ok"]:
                await interaction.response.send_message(result["message"], ephemeral=True)
                return

            self.balance = result["new_wallet"]

            embed = discord.Embed(
                title="✅ Purchase Successful!",
                description=(
                    f"You bought **{result['item_name']}**!\n\n"
                    f"Price: 🪙 {result['price']:,}\n"
                    f"Old Wallet: 🪙 {result['old_wallet']:,}\n"
                    f"New Wallet: 🪙 {result['new_wallet']:,}"
                    + (f"\nRole Granted: {result['role_mention']}" if result["purchase_type"] == "role" else "\n\nUse `.inventory` to view your items!")
                ),
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            self.dropdown.disabled = True
            self.dropdown.placeholder = "Purchase completed!"
            
            try:
                await interaction.followup.edit_message(interaction.message.id, view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

        except Exception as e:
            await interaction.response.send_message(
                f"❌ An error occurred during purchase: `{type(e).__name__}: {e}`",
                ephemeral=True
            )

@bot.command(name="maintenance", description="Toggle maintenance mode (staff only access).")
@staffperm("config")
@staff_only()
@maintenance_bypass()
async def maintenance(ctx, action: str = None):
    guild_id = str(ctx.guild.id)
    
    if action is None:
        settings = await settings_col.find_one({"guild": guild_id})
        is_maintenance = settings.get("maintenance_mode", False) if settings else False
        
        embed = discord.Embed(
            title="🔧 Maintenance Status",
            description=f"Maintenance mode is currently: {'**ON**' if is_maintenance else '**OFF**'}",
            color=discord.Color.orange() if is_maintenance else discord.Color.green()
        )
        
        if is_maintenance:
            embed.add_field(
                name="⚠️ Current Status",
                value="• Only staff can use bot commands\n"
                      "• Channel restrictions are bypassed for staff\n"
                      "• Regular users cannot use any commands",
                inline=False
            )
        else:
            embed.add_field(
                name="✅ Current Status", 
                value="• All users can use bot commands\n"
                      "• Channel restrictions are enforced\n"
                      "• Normal operation mode",
                inline=False
            )
        
        embed.add_field(
            name="📝 Usage",
            value="`.maintenance on` - Enable maintenance mode\n"
                  "`.maintenance off` - Disable maintenance mode",
            inline=False
        )
        
        await ctx.send(embed=embed)
        return
    
    action = action.lower()
    if action not in ["on", "off"]:
        await ctx.send("❌ Invalid action. Use `on`, `off`, or no argument to check status.")
        return
    
    await settings_col.update_one(
        {"guild": guild_id},
        {"$set": {"maintenance_mode": action == "on"}},
        upsert=True
    )
    
    if action == "on":
        embed = discord.Embed(
            title="🔧 Maintenance Mode Enabled",
            description="**Bot is now in maintenance mode!**",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="⚠️ What Changed",
            value="• Only staff members can use bot commands\n"
                  "• Channel restrictions are ignored for staff\n"
                  "• Regular users see maintenance messages",
            inline=False
        )
        embed.add_field(
            name="👤 Who Can Use Commands",
            value="• Server Owner\n"
                  "• Staff members (with configured staff role)\n"
                  "• Users with admin permissions",
            inline=False
        )
        embed.set_footer(text="Use `.maintenance off` to disable maintenance mode")
        
    else:  # action == "off"
        embed = discord.Embed(
            title="✅ Maintenance Mode Disabled",
            description="**Bot is back to normal operation!**",
            color=discord.Color.green()
        )
        embed.add_field(
            name="🔄 What Changed",
            value="• All users can use bot commands again\n"
                  "• Channel restrictions are enforced\n"
                  "• Normal operation resumed",
            inline=False
        )
        embed.set_footer(text="Use `.maintenance on` to enable maintenance mode")
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name="shop", description="View the shop.", aliases=["store"])
@blacklist_barrier()
async def shop(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    try:
        guild_id = str(ctx.guild.id)
        user_id = str(ctx.author.id)
        
        user_data = await economy_col.find_one({"_id": f"{guild_id}-{user_id}"})
        wallet_balance = user_data.get("wallet", 0) if user_data else 0
        bank_balance = user_data.get("bank", 0) if user_data else 0
        total_balance = wallet_balance + bank_balance
        
        shop_items = guild_shop_col.find({"guild": guild_id}).sort("price", 1)
        exists = False
        items_list = []
        
        async for item in shop_items:
            exists = True
            items_list.append(item)
        
        if not exists:
            defaults = shop_col.find()
            async for item in defaults:
                doc = dict(item)
                doc["_id"] = f"{guild_id}-{item['_id']}"
                doc["guild"] = guild_id
                await guild_shop_col.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
            
            shop_items = guild_shop_col.find({"guild": guild_id}).sort("price", 1)
            async for item in shop_items:
                items_list.append(item)

        embed = discord.Embed(
            title="🛍️ Shop",
            description=f"💰 Wallet: 🪙 {wallet_balance:,}\n🏦 Bank: 🪙 {bank_balance:,}\n💳 **Total: 🪙 {total_balance:,}**\n\nClick the button below to purchase items! Purchases use wallet coins only.",
            color=discord.Color.green()
        )

        if items_list:
            for item in items_list:
                display_name = item.get("name") or item.get("_id", "Unnamed Item")
                price = item.get("price", "Unknown")
                description = item.get("description", "No description available.")

                if item["_id"] == "pet_duck":
                    description += "\n🦆 Stackable: Yes (3 uses per duck)"

                embed.add_field(
                    name=f"{display_name} - 🪙 {price}",
                    value=description,
                    inline=False
                )
                
                if len(embed.fields) >= 24:
                    embed.add_field(
                        name="🛍️ More Items",
                        value=f"... and {len(items_list) - len(embed.fields) + 1} more items! Use the shop view to see all items.",
                        inline=False
                    )
                    break
        else:
            embed.description += "\n\n❌ The shop is empty. Ask a staff member to refill it."

        view = ShopView(ctx.author.id, guild_id, items_list, wallet_balance) if items_list else None
        
        await ctx.send(embed=embed, view=view)

    except Exception as e:
        await ctx.send(f"❌ An error occurred while loading the shop: `{type(e).__name__}: {e}`")

async def prompt_for_role(ctx):
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    while True:
        await ctx.send("📌 Please enter the **role ID** (or type `cancel` to skip):")
        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            await ctx.send("⌛ Cancelled due to timeout.")
            return None

        if msg.content.lower() == "cancel":
            return None

        try:
            role_id = int(msg.content)
        except ValueError:
            await ctx.send("❌ Invalid format. Role ID must be numbers only.")
            continue

        role = ctx.guild.get_role(role_id)
        if not role:
            await ctx.send("❌ No role found with that ID. Please try again.")
            continue

        await ctx.send(f"✅ Linked role: {role.mention}")
        return role_id

@bot.hybrid_command(name="additem", description="Add a new item to the shop.")
@app_commands.describe(name="Item name", price="Item price in coins")
@staffperm("economy")
@staff_only()
async def additem(ctx, name: str, price: int):
    name = name.strip()
    if not name:
        return await ctx.send("❌ Usage: `.additem \"item name\" <price>` or `/additem <name> <price>`")
    if price <= 0:
        return await ctx.send("❌ Price must be greater than 0.")

    name_lower = name.lower()

    await ctx.send(f"📝 Enter the description for **{name}**:")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        desc_msg = await bot.wait_for("message", check=check, timeout=120)
        description = desc_msg.content.strip()
    except asyncio.TimeoutError:
        return await ctx.send("⌛ Item creation cancelled due to timeout.")

    await ctx.send(f"🔗 Do you want to link a role to **{name}**? (yes/no)")
    try:
        choice_msg = await bot.wait_for("message", check=check, timeout=60)
        choice = choice_msg.content.lower()
    except asyncio.TimeoutError:
        return await ctx.send("⌛ Item creation cancelled due to timeout.")

    role_id = None
    if choice in ["yes", "y"]:
        role_id = await prompt_for_role(ctx)

    item_data = {
        "_id": name_lower,
        "name": name,
        "name_lower": name_lower,
        "price": price,
        "description": description
    }
    if role_id:
        item_data["role_id"] = role_id

    guild_id = str(ctx.guild.id)
    item_data["_id"] = f"{guild_id}-{name_lower}"
    item_data["guild"] = guild_id
    await guild_shop_col.replace_one({"_id": item_data["_id"]}, item_data, upsert=True)

    confirmation_msg = f"✅ Added **{name}** to the shop!\n**Price:** {price}\n**Description:** {description}"
    if role_id:
        confirmation_msg += f"\n**Linked Role:** <@&{role_id}>"

    await ctx.send(confirmation_msg)

@bot.hybrid_command(name="edititem", description="Edit an existing shop item.")
@staffperm("economy")
@staff_only()
async def edititem(ctx, *, name: str):
    guild_id = str(ctx.guild.id)
    item = await guild_shop_col.find_one({"guild": guild_id, "name_lower": name.lower()})
    if not item:
        return await ctx.send(f"❌ No item found with name `{name}`.")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    await ctx.send(f"✏️ Enter a new name for **{item['name']}** (or type `skip` to keep the same):")
    try:
        name_msg = await bot.wait_for("message", check=check, timeout=60)
        new_name = name_msg.content.strip()
        if new_name.lower() == "skip":
            new_name = item["name"]
    except asyncio.TimeoutError:
        return await ctx.send("⌛ Edit cancelled due to timeout.")

    await ctx.send(f"💰 Enter a new price for **{new_name}** (or type `skip`):")
    try:
        price_msg = await bot.wait_for("message", check=check, timeout=60)
        if price_msg.content.lower() == "skip":
            new_price = item["price"]
        else:
            new_price = int(price_msg.content)
    except (asyncio.TimeoutError, ValueError):
        return await ctx.send("❌ Invalid price or timeout. Edit cancelled.")

    await ctx.send(f"📝 Enter a new description for **{new_name}** (or type `skip`):")
    try:
        desc_msg = await bot.wait_for("message", check=check, timeout=120)
        if desc_msg.content.lower() == "skip":
            new_desc = item["description"]
        else:
            new_desc = desc_msg.content.strip()
    except asyncio.TimeoutError:
        return await ctx.send("⌛ Edit cancelled due to timeout.")

    await ctx.send(f"🔗 Do you want to change the linked role? (yes/no)")
    try:
        choice_msg = await bot.wait_for("message", check=check, timeout=60)
        choice = choice_msg.content.lower()
    except asyncio.TimeoutError:
        return await ctx.send("⌛ Edit cancelled due to timeout.")

    role_id = item.get("role_id")
    if choice in ["yes", "y"]:
        role_id = await prompt_for_role(ctx)

    await guild_shop_col.update_one(
        {"guild": guild_id, "name_lower": name.lower()},
        {"$set": {
            "name": new_name,
            "name_lower": new_name.lower(),
            "price": new_price,
            "description": new_desc,
            "role_id": role_id
        }}
    )

    confirmation_msg = f"✅ Updated **{new_name}**!\n**Price:** {new_price}\n**Description:** {new_desc}"
    if role_id:
        confirmation_msg += f"\n**Linked Role:** <@&{role_id}>"

    await ctx.send(confirmation_msg)


@bot.hybrid_command(name="delitem", description="Remove an item from the shop.")
@staffperm("economy")
@staff_only()
async def delitem(ctx, *, name: str):
    guild_id = str(ctx.guild.id)
    result = await guild_shop_col.delete_one({"guild": guild_id, "name_lower": name.lower()})
    if result.deleted_count:
        await ctx.send(f"🗑️ `{name}` removed from the shop.")
    else:
        await ctx.send("❌ Item not found.")
    
@bot.hybrid_command(name="buy", description="Buy an item from the shop.", aliases=["purchase"])
@app_commands.describe(item="The item to buy (e.g., 'fishing rod', 'rifle', 'laptop'). Use '/shop' to see available items.")
@blacklist_barrier()
async def buy(ctx, item: str = None):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    if not item:
        return await ctx.send("❌ You must specify an item to buy.")

    item_name = item.strip().lower()
    store_item = await guild_shop_col.find_one({"guild": str(ctx.guild.id), "name_lower": item_name})
    if not store_item:
        return await ctx.send(f"❌ Item **{item}** not found in the shop.")

    data = await get_user(ctx, ctx.guild.id, ctx.author.id)
    result = await process_shop_purchase(ctx.author, ctx.guild, store_item, data)
    await ctx.send(result["message"])
    
@bot.hybrid_command(name="use", description="Use an item from your inventory.")
@app_commands.describe(item_name="The item to use (e.g., 'fishing rod', 'energy drink', 'laptop'). Use '/inventory' to see your items.")
@blacklist_barrier()
async def use(ctx, item_name: str):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    data = await get_user(ctx, ctx.guild.id, ctx.author.id)
    inventory = data.get("inventory", [])

    item_name = item_name.strip().lower()

    matched_item = next((i for i in inventory if i.lower() == item_name), None)
    if not matched_item:
        return await ctx.send("❌ You don’t have that item in your inventory.")

    if matched_item.lower() == "luck potion":
        await economy_col.update_one(
            {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
            {
                "$pull": {"inventory": matched_item},
                "$set": {"luck_buff": True}
            }
        )
        return await ctx.send("🍀 You used a **Luck Potion**! You’ll have better odds in your next activities for 1 use.")

    await ctx.send("❌ That item can’t be used yet.")

@bot.hybrid_command(name="inventory", description="View your items.", aliases=["inv"])
@blacklist_barrier()
async def inventory(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    data = await get_user(ctx, ctx.guild.id, ctx.author.id)
    inv = data.get("inventory", [])
    if not inv:
        return await ctx.send("🎒 Your inventory is empty.")

    counts = {}
    duck_total = 0
    duck_uses = 0

    for item in inv:
        if isinstance(item, dict) and item.get("_id") == "pet_duck":
            duck_total += 1
            duck_uses += item.get("uses_left", 0)
        else:
            counts[item] = counts.get(item, 0) + 1

    embed = discord.Embed(
        title=f"🎒 {ctx.author.display_name}'s Inventory",
        color=discord.Color.purple()
    )

    if duck_total > 0:
        shop_item = await shop_col.find_one({"_id": "pet_duck"})
        embed.add_field(
            name=f"{shop_item['name']} x{duck_total}",
            value=f"{shop_item.get('description', '')} ({duck_uses} uses left total)",
            inline=False
        )

    for key, count in counts.items():
        shop_item = await shop_col.find_one({"name_lower": key})
        if shop_item:
            embed.add_field(
                name=f"{shop_item['name']} x{count}",
                value=f"{shop_item.get('description', 'No description.')}",
                inline=False
            )
        else:
            clean_name = key.split("-", 1)[-1] if "-" in key else key
            emoji = "📦"
            
            embed.add_field(
                name=f"{emoji} {clean_name.replace('_', ' ').title()} x{count}",
                value="*Item no longer sold in shop*",
                inline=False
            )
        
        if len(embed.fields) >= 24:
            embed.add_field(
                name="📦 More Items",
                value=f"... and {len(counts) - len(embed.fields) + 1} more items! Use `.inventory` again to see details.",
                inline=False
            )
            break

    await ctx.send(embed=embed)

@bot.hybrid_command(name="give", description="Give coins to another user.", aliases=["pay"])
@app_commands.describe(member_name="The user to give coins to (name or mention)", amount="Amount to give (number or 'all')")
@blacklist_barrier()
async def give(ctx, member_name: str, amount: str):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    
    if member_name.lower() == "duckparadise":
        return await ctx.send("🦆 I don't need coins, but thanks for the thought! Quack!")
    
    member = None
    
    if member_name.startswith("<@") and member_name.endswith(">"):
        try:
            member_id = int(member_name.strip("<@!>"))
            member = ctx.guild.get_member(member_id)
        except (ValueError, AttributeError):
            pass
    
    if member is None:
        member = discord.utils.get(ctx.guild.members, name=member_name)
    
    if member is None:
        member = discord.utils.get(ctx.guild.members, display_name=member_name)
    
    if member is None:
        return await ctx.send(f"❌ Could not find user '{member_name}'. Make sure they're in this server.")
    
    if member == ctx.author:
        return await ctx.send("❌ You cannot give coins to yourself.")

    sender = await get_user(ctx, ctx.guild.id, ctx.author.id)
    receiver = await get_user(ctx, ctx.guild.id, member.id)
    
    if amount.lower() == "all":
        amount = sender["wallet"]
        if amount <= 0:
            return await ctx.send("❌ You don't have any coins to give.")
    else:
        try:
            amount = int(amount)
        except ValueError:
            return await ctx.send("❌ Invalid amount. Use a number or 'all'.")
        
        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than 0.")

    if sender["wallet"] < amount:
        return await ctx.send("❌ You don't have enough coins.")

    await subtract_balance(ctx.author.id, ctx.guild.id, amount)
    await add_balance(member.id, ctx.guild.id, amount)
    
    if amount == sender["wallet"] and amount > 0:
        await ctx.send(f"🤝 You gave all **{amount}** coins to {member.mention}!")
    else:
        await ctx.send(f"🤝 You gave **{amount}** coins to {member.mention}!")
    
@bot.hybrid_command(name="leaderboard", description="View the top users.", aliases=["lb"])
@blacklist_barrier()
async def leaderboard(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    cursor = economy_col.find({"guild": str(ctx.guild.id)})
    users = []
    async for doc in cursor:
        total = doc.get("wallet", 0) + doc.get("bank", 0)
        uid = int(doc["user"])

        member = ctx.guild.get_member(uid)
        if member:
            users.append((uid, total))

    users.sort(key=lambda x: x[1], reverse=True)

    embed = discord.Embed(
        title="🏆 Leaderboard - Richest Users",
        color=discord.Color.teal()
    )

    for i, (uid, total) in enumerate(users[:10], start=1):
        member = ctx.guild.get_member(uid)
        if member:
            name = member.display_name
        else:
            name = f"Unknown User ({uid})"

        embed.add_field(
            name=f"#{i} {name}",
            value=f"🪙 {total} coins",
            inline=False
        )

    user_id = ctx.author.id
    rank = next((i + 1 for i, (uid, _) in enumerate(users) if uid == user_id), None)
    user_total = next((total for uid, total in users if uid == user_id), 0)

    if rank:
        embed.set_footer(text=f"Your Rank: #{rank} • 🪙 {user_total} coins")

    await ctx.send(embed=embed)

@bot.hybrid_command(name="coinflip", description="Coin flip for coins.", aliases=["cf"])
@app_commands.describe(amount="Amount to bet (number or 'all')")
@blacklist_barrier()
async def coinflip(ctx, amount: str):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    data = await get_user(ctx, ctx.guild.id, ctx.author.id)
    wallet = data.get("wallet", 0)

    if amount.lower() == "all":
        amount = wallet
    else:
        try:
            amount = int(amount)
        except ValueError:
            return await ctx.send("❌ Please enter a valid number or `all`.")

    if amount <= 0:
        return await ctx.send("❌ Invalid amount to coin flip.")
    if amount > wallet:
        return await ctx.send("❌ You can't afford that!")

    luck_buff = data.get("luck_buff", False)
    base_chance = 0.5
    
    adjusted_chance = base_chance

    if luck_buff:
        await economy_col.update_one(
            {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
            {"$unset": {"luck_buff": ""}}
        )

    won = random.random() < adjusted_chance
    if won:
        await add_balance(ctx.author.id, ctx.guild.id, amount)
        msg = f"🎉 You won {amount} coins from flipping a coin!"
        await ctx.send(msg)
    else:
        await subtract_balance(ctx.author.id, ctx.guild.id, amount)
        await ctx.send(f"💸 You lost {amount} coins from flipping a coin.")

@coinflip.error
async def coinflip_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ You must specify an amount (number or `all`).")
    else:
        await ctx.send(f"⚠️ Error, contact thetruck: {type(error).__name__}: {error}")

@bot.hybrid_command(name="duckroll", description="Guess if the ducks are higher or lower than 50!")
@blacklist_barrier()
async def duckroll(ctx, guess: str):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        wallet = data.get("wallet", 0)

        guess = guess.lower()
        if guess not in ["high", "low"]:
            return await ctx.send("❌ Invalid choice! Use `.duckroll high` or `.duckroll low`.")

        bet_amount = 150
        if wallet < bet_amount:
            return await ctx.send("❌ You don’t have enough coins to play duckroll! (Need at least 150)")

        roll = random.randint(1, 100)

        if (roll > 50 and guess == "high") or (roll < 50 and guess == "low"):
            await add_balance(ctx.author.id, ctx.guild.id, bet_amount)
            msg = (
                f"🦆 You rolled **{roll} ducks**!\n"
                f"✅ Correct guess! You won **{bet_amount} coins** 🎉"
            )
            await ctx.send(msg)
        elif roll == 50:
            await ctx.send(
                f"🦆 You rolled exactly **50 ducks**!\n"
                f"🤷 It’s a draw. No win, no loss."
            )
        else:
            await subtract_balance(ctx.author.id, ctx.guild.id, bet_amount)
            await ctx.send(
                f"🦆 You rolled **{roll} ducks**!\n"
                f"❌ Wrong guess! You lost **{bet_amount} coins** 💸"
            )

    except Exception as e:
        await ctx.send("⚠️ Something went wrong while processing your duckroll. Contact thetruck.")
        print(f"[ERROR] duckroll command: {type(e).__name__} - {e}")
        traceback.print_exc()

@bot.hybrid_command(name="lottery", description="Join the lottery.")
@blacklist_barrier()
async def lottery(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    user_id = f"{ctx.guild.id}-{ctx.author.id}"
    data = await get_user(ctx, ctx.guild.id, ctx.author.id)
    now = datetime.now(timezone.utc)

    last_time = data.get("last_lottery")
    if last_time:
        last_time = datetime.fromisoformat(last_time)
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        if now - last_time < timedelta(hours=1):
            rem = timedelta(hours=1) - (now - last_time)
            return await ctx.send(f"🕒 You can try the lottery again in {rem.seconds // 60}m {rem.seconds % 60}s.")

    ticket_price = 300
    jackpot = random.randint(15000, 20000)
    base_chance = 0.05

    if data["wallet"] < ticket_price:
        return await ctx.send("🎟️ You need at least 300 coins to buy a lottery ticket.")

    inventory = data.get("inventory", [])

    luck_boost = 1.0
    for i, item in enumerate(inventory):
        if isinstance(item, dict) and item.get("_id") == "pet_duck":
            luck_boost = 1.3
            item["uses_left"] -= 1
            await ctx.send("🦆 Your Pet Duck boosted your lottery luck by 30%!")

            if item["uses_left"] <= 0:
                inventory.pop(i)
                await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
            break
            

    chance = base_chance * luck_boost
    data["wallet"] -= ticket_price
    await economy_col.update_one(
        {"_id": user_id},
        {"$set": {"wallet": data["wallet"]}}
    )
    win = random.random() <= chance
    if win:
        await add_balance(ctx.author.id, ctx.guild.id, jackpot)
        msg = f"🎉 You hit the jackpot and won **{jackpot} coins**!"
        await ctx.send(msg)
    else:
        await ctx.send("😢 No luck this time. Better luck next draw!")

    data["inventory"] = inventory
    await economy_col.update_one(
        {"_id": user_id},
        {"$set": {"inventory": inventory, "last_lottery": now.isoformat()}}
    )
    
@lottery.error
async def lottery_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        rem = timedelta(seconds=error.retry_after)
        mins = rem.seconds // 60
        secs = rem.seconds % 60
        return await ctx.send(f"🕒 Try again in {mins}m {secs}s.")

class JobPicker(ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=30)
        self.ctx = ctx

    async def interaction_check(self, interaction):
        return interaction.user == self.ctx.author

    @ui.button(label="Developer 🧑‍💻", style=ButtonStyle.blurple)
    async def dev_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.set_job(interaction, "developer")

    @ui.button(label="Duck 🦆", style=discord.ButtonStyle.green)
    async def duck_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.set_job(interaction, "duck")

    async def set_job(self, interaction, job_name):
        await economy_col.update_one(
            {"_id": f"{self.ctx.guild.id}-{self.ctx.author.id}"},
            {"$set": {
                "job": job_name,
                "job_start": datetime.now(timezone.utc).isoformat(),
                "promotion_level": 0,
                "promotion_chance": 20.0,
                "last_promo_check": None
            }},
            upsert=True
        )
        await interaction.response.edit_message(
            content=f"✅ You are now working as a **{job_name.capitalize()}**!",
            view=None
        )

@bot.hybrid_command(name="choosejob", description="Choose your dream job")
@blacklist_barrier()
async def choosejob(ctx):
    view = JobPicker(ctx)
    await ctx.send(
        "💼 Choose your job by clicking one of the buttons below:",
        view=view
    )

@bot.hybrid_command(name="work", description="Work to earn coins.")
@blacklist_barrier()
async def work(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        job = data.get("job")

        if not job:
            doc = await settings_col.find_one({"guild": str(ctx.guild.id)})
            prefix = doc.get("prefix", "?") if doc else "?"
            return await ctx.send(f"❌ You don't have a job yet! Use `{prefix}choosejob` to get one.")

        inventory = data.get("inventory", [])
        
        has_laptop = False
        for item in inventory:
            if isinstance(item, str) and item == "laptop":
                has_laptop = True
                break
            elif isinstance(item, dict) and item.get("_id") == "laptop":
                has_laptop = True
                break
        
        if job == "developer" and not has_laptop:
            return await ctx.send("💻 You need a **laptop** to work as a developer!")

        if job not in ["developer", "duck"]:
            return await ctx.send("⚠️ You have an invalid job. Please use `?choosejob` to pick a valid one.")

        cooldown_key = f"work_cooldown_{ctx.guild.id}-{ctx.author.id}"
        cooldown_data = await economy_col.find_one({"_id": cooldown_key})
        if cooldown_data:
            last_work = cooldown_data.get("timestamp")
            if last_work:
                time_since = datetime.now(timezone.utc) - parser.isoparse(last_work)
                cooldown_duration = 43200
                
                cooldown_reduction = await get_work_cooldown_reduction(ctx.author.id, ctx.guild.id)
                if cooldown_reduction < 1.0:
                    cooldown_duration = int(cooldown_duration * cooldown_reduction)
                
                if time_since.total_seconds() < cooldown_duration:
                    remaining = int(cooldown_duration - time_since.total_seconds())
                    hours, remainder = divmod(remaining, 3600)
                    minutes, _ = divmod(remainder, 60)
                    if hours > 0:
                        return await ctx.send(f"⏰ You're on cooldown! Try again in {hours}h {minutes}m.")
                    else:
                        return await ctx.send(f"⏰ You're on cooldown! Try again in {minutes}m {int((remainder % 60))}s.")

        promo_level = data.get("promotion_level", 0)

        cooldown_reduction = await get_work_cooldown_reduction(ctx.author.id, ctx.guild.id)
        earnings_multiplier = await get_earnings_multiplier(ctx.author.id, ctx.guild.id)
        
        inventory = data.get("inventory", [])
        duck_used = False
        for i, item in enumerate(inventory):
            if isinstance(item, dict) and item.get("_id") == "pet_duck":
                earnings_multiplier *= 1.3
                item["uses_left"] -= 1
                await ctx.send("🦆 Your Pet Duck boosted your work earnings by 30%!")
                duck_used = True
                break
        
        if duck_used:
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"inventory": inventory}},
                upsert=True
            )
        
        if cooldown_reduction < 1.0:
            ctx.command.reset_cooldown(ctx)
            new_cooldown = 43200 * cooldown_reduction
            ctx.command._buckets._cache[ctx.author].cooldown = new_cooldown
            await ctx.send("⚡ **Energy Drink consumed!** Work cooldown reduced by 50%!")

        base_payouts = {
            "developer": (300, 600),
            "duck": (200, 500)
        }
        descriptions = {
            "developer": "You wrote some killer code 💻",
            "duck": "You danced and quacked around the duck pond 🦆"
        }

        low, high = base_payouts[job]
        multiplier = 1 + (0.2 * promo_level)
        multiplier *= earnings_multiplier
        low = int(low * multiplier)
        high = int(high * multiplier)

        earned = random.randint(low, high)
        await add_balance(ctx.author.id, ctx.guild.id, earned)
        
        await economy_col.update_one(
            {"_id": cooldown_key},
            {"$set": {"timestamp": datetime.now(timezone.utc).isoformat()}},
            upsert=True
        )

        msg = (
            f"🧾 {descriptions.get(job, 'You worked hard!')}\n"
            f"💰 You earned **{earned} coins** as a level `{promo_level}` {job}!"
        )
        
        if earnings_multiplier > 1.0:
            msg += "\n🍪 **Lucky Cookie consumed!** Earnings doubled!"
            
        await ctx.send(msg)

    except Exception as e:
        await ctx.send("⚠️ Something went wrong while processing your work. Contact thetruck.")
        print(f"[ERROR] work command: {type(e).__name__} - {e}")
        traceback.print_exc()

@work.error
async def work_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        total_seconds = int(error.retry_after)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return await ctx.send(f"⏰ You're on cooldown! Try again in {hours}h {minutes}m {seconds}s.")
        elif minutes > 0:
            return await ctx.send(f"⏰ You're on cooldown! Try again in {minutes}m {seconds}s.")
        else:
            return await ctx.send(f"⏰ You're on cooldown! Try again in {seconds}s.")
    elif isinstance(error, commands.CommandError):
        await ctx.send("⚠️ Something went wrong while processing your work. Contact thetruck.")
        print(f"[ERROR] work command: {type(error).__name__} - {error}")

@bot.command()
@staff_only()
async def reseteconomy(ctx):
    await ctx.defer()
    try:
        result = await economy_col.delete_many({"_id": {"$regex": f"^{ctx.guild.id}-"}})

        await settings_col.update_one(
            {"guild": str(ctx.guild.id)},
            {"$set": {"season_reset_time": datetime.now(UTC)}},
            upsert=True
        )

        await ctx.send(f"🧹 Economy has been reset for this server!\nDeleted **{result.deleted_count}** player records.")
        print(f"[RESET ECONOMY] {ctx.guild.name} ({ctx.guild.id}) — Deleted {result.deleted_count} entries.")
    except Exception as e:
        await ctx.send("⚠️ Something went wrong while resetting the economy.")
        print(f"[ERROR] reseteconomy command: {type(e).__name__} - {e}")
        traceback.print_exc()

@bot.hybrid_command(name="jobstatus", description="Check your next promotion.")
@blacklist_barrier()
async def jobstatus(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        user_data = await economy_col.find_one({"_id": user_id}) or {}

        job = user_data.get("job")
        job_start_str = user_data.get("job_start")
        promo_level = user_data.get("promotion_level", 0)
        promo_chance = user_data.get("promotion_chance", 20.0)
        last_check_str = user_data.get("last_promo_check")
        last_roll_str = user_data.get("last_promo_roll")

        if not job or not job_start_str:
            return await ctx.send("💼 You don't currently have a job. Choose one with your server's prefix like `?choosejob`.")

        try:
            job_start = datetime.fromisoformat(job_start_str)
            if job_start.tzinfo is None:
                job_start = job_start.replace(tzinfo=timezone.utc)
        except Exception as e:
            return await ctx.send(f"⚠️ An unexpected error occurred, please contact thetruck: `{type(e).__name__} - {e}`")

        now = datetime.now(timezone.utc)
        delta = now - job_start
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60

        promoted = False
        
        if days >= 7:
            if last_check_str:
                try:
                    last_check = datetime.fromisoformat(last_check_str).replace(tzinfo=timezone.utc)
                except:
                    last_check = now
            else:
                last_check = now

            elapsed_days = (now - last_check).days
            allow_roll = True
            if last_roll_str:
                try:
                    last_roll = datetime.fromisoformat(last_roll_str)
                    if last_roll.tzinfo is None:
                        last_roll = last_roll.replace(tzinfo=timezone.utc)
                except:
                    last_roll = None
                if last_roll:
                    allow_roll = (now - last_roll) >= timedelta(days=1)

            if allow_roll:
                if elapsed_days > 0:
                    promo_chance += elapsed_days * 0.5
                    if promo_chance > 100:
                        promo_chance = 100
                if random.random() <= (promo_chance / 100):
                    promo_level += 1
                    promo_chance = 20.0
                    promoted = True
                    
                    update_fields = {
                        "promotion_level": promo_level,
                        "promotion_chance": promo_chance,
                        "last_promo_check": now.isoformat(),
                        "last_promo_roll": now.isoformat()
                    }
                    await economy_col.update_one({"_id": user_id}, {"$set": update_fields}, upsert=True)

                    embed = discord.Embed(
                        title="🎉 Promotion Achieved!",
                        description=f"Congratulations {ctx.author.mention}, you’ve been **promoted** to level `{promo_level}` in your job as a **{job.capitalize()}**!\n\n💰 You will now earn **even more coins** when you work!",
                        color=discord.Color.gold()
                    )
                    embed.set_thumbnail(url="https://media.tenor.com/I5qPz6wS1jAAAAAC/congratulations-clapping.gif")
                    await ctx.send(embed=embed)

            if not promoted:
                embed = discord.Embed(
                    title=f"📋 Job Status for {ctx.author.display_name}",
                    color=discord.Color.blue()
                )
                embed.add_field(name="Job", value=job.capitalize(), inline=False)
                embed.add_field(name="Promotion Level", value=str(promo_level), inline=False)
                embed.add_field(name="Time on Job", value=f"{days}d {hours}h {minutes}m", inline=False)
                if allow_roll:
                    embed.add_field(name="Promotion Chance", value=f"✅ Eligible ({promo_chance:.2f}%)", inline=False)
                else:
                    next_time = (last_roll + timedelta(days=1)) if last_roll_str else (now + timedelta(days=1))
                    embed.add_field(
                        name="Promotion Chance",
                        value=f"⏳ On cooldown ({promo_chance:.2f}%) — next roll <t:{int(next_time.timestamp())}:f>",
                        inline=False
                    )
                await ctx.send(embed=embed)

        else:
            embed = discord.Embed(
                title=f"📋 Job Status for {ctx.author.display_name}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Job", value=job.capitalize(), inline=False)
            embed.add_field(name="Promotion Level", value=str(promo_level), inline=False)
            embed.add_field(name="Time on Job", value=f"{days}d {hours}h {minutes}m", inline=False)
            embed.add_field(name="Promotion Chance", value=f"❌ Not eligible yet (need {7 - days} more day(s))", inline=False)
            await ctx.send(embed=embed)

        if not promoted:
            update_fields = {
                "promotion_level": promo_level,
                "promotion_chance": promo_chance
            }
            if days >= 7:
                if 'allow_roll' in locals() and allow_roll:
                    update_fields["last_promo_check"] = now.isoformat()
                    update_fields["last_promo_roll"] = now.isoformat()
            await economy_col.update_one({"_id": user_id}, {"$set": update_fields}, upsert=True)

    except Exception as e:
        print(f"[jobstatus command error] {type(e).__name__}: {e}")
        traceback.print_exc()
        await ctx.send(f"⚠️ An unexpected error occurred: `{type(e).__name__} - {e}`\nPlease contact thetruck.")

@bot.hybrid_command(name="fish", description="Go fishing to earn coins.")
@commands.cooldown(1, 10800, commands.BucketType.member)
@blacklist_barrier()
async def fish(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    try:
        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        now = datetime.now(timezone.utc)

        inventory = data.get("inventory", [])
        
        has_fishing_rod = False
        for item in inventory:
            if isinstance(item, str) and item == "fishing rod":
                has_fishing_rod = True
                break
            elif isinstance(item, dict) and item.get("_id") == "fishing rod":
                has_fishing_rod = True
                break
        
        if not has_fishing_rod:
            return await ctx.send("🎣 You need a fishing rod to fish!")

        base_chance = 1.0
        luck_buff = 0.0

        for i, item in enumerate(inventory):
            if isinstance(item, dict) and item.get("_id") == "pet_duck":
                luck_buff = 0.3
                item["uses_left"] -= 1
                await ctx.send("🦆 Your Pet Duck helped you catch more fish!")

                if item["uses_left"] <= 0:
                    inventory.pop(i)
                    await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
                break

        adjusted_chance = min(base_chance + luck_buff, 1.0)

        success = random.random() < adjusted_chance
        if not success:
            return await ctx.send("🐟 You tried fishing, but came up empty-handed!")

        catch = random.choice(fishes)
        coins_earned = int(catch[1] * (1 + luck_buff))
        await add_balance(ctx.author.id, ctx.guild.id, coins_earned)
        await economy_col.update_one(
            {"_id": user_id},
            {"$set": {"inventory": inventory, "last_fished": now.isoformat()}}
        )

        msg = f"🎣 You caught a **{catch[0]}** and earned **{coins_earned} coins**!"
        await ctx.send(msg)

    except Exception as e:
        print(f"[ERROR] fish command: {type(e).__name__} - {e}")
        traceback.print_exc()
        await ctx.send("⚠️ Something went wrong while fishing. Contact thetruck.")

@fish.error
async def fish_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        total_seconds = int(error.retry_after)
        hours, remainder = divmod(total_seconds, 10800)
        minutes, _ = divmod(remainder, 60)
        return await ctx.send(f"🕒 You can fish again in {hours}h {minutes}m.")
    else:
        await ctx.send("⚠️ An unexpected error occurred. Contact thetruck.")

@bot.hybrid_command(name="rob", description="Attempt to rob another user.", aliases=["steal"])
@app_commands.describe(member="The user to rob (mention or name)")
@blacklist_barrier()
async def rob(ctx, member: discord.Member):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    if member == ctx.author:
        return await ctx.send("❌ You can't rob yourself!")

    now = datetime.now(timezone.utc)
    robber_id = f"{ctx.guild.id}-{ctx.author.id}"
    victim_id = f"{ctx.guild.id}-{member.id}"

    r_doc = await economy_col.find_one({"_id": robber_id}) or {}
    v_doc = await economy_col.find_one({"_id": victim_id}) or {}

    cooldown = r_doc.get("rob_cooldown")
    if cooldown:
        cooldown_dt = datetime.fromisoformat(cooldown)
        if cooldown_dt.tzinfo is None:
            cooldown_dt = cooldown_dt.replace(tzinfo=timezone.utc)
        if now < cooldown_dt:
            remaining = cooldown_dt - now
            mins = int(remaining.total_seconds() // 60)
            return await ctx.send(f"🕒 You can rob again in {mins} minute(s).")

    if r_doc.get("passive_until"):
        until = datetime.fromisoformat(r_doc["passive_until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until > now:
            return await ctx.send("🔒 You have passive mode enabled, disable it to rob others.")
    if v_doc.get("passive_until"):
        until = datetime.fromisoformat(v_doc["passive_until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until > now:
            return await ctx.send("🔒 That user has passive mode enabled, you can't rob them.")

    last_robbed = v_doc.get("last_robbed")
    if last_robbed:
        if isinstance(last_robbed, str):
            last_robbed = datetime.fromisoformat(last_robbed)
            if last_robbed.tzinfo is None:
                last_robbed = last_robbed.replace(tzinfo=timezone.utc)
        if now - last_robbed < timedelta(hours=1):
            rem = timedelta(hours=1) - (now - last_robbed)
            minutes = round(rem.total_seconds() / 60)
            return await ctx.send(f"🛡️ {member.display_name} is under protection. Try again in {minutes} minutes.")

    if r_doc.get("wallet", 0) < 500:
        return await ctx.send("❌ You need at least 500 coins to rob.")
    if v_doc.get("wallet", 0) < 300:
        return await ctx.send("❌ They don’t have enough coins to rob.")

    amount = random.randint(100, min(500, v_doc["wallet"], r_doc["wallet"]))
    await add_balance(ctx.author.id, ctx.guild.id, amount)
    await subtract_balance(member.id, ctx.guild.id, amount)
    await economy_col.update_one({"_id": robber_id}, {"$set": {"rob_cooldown": (now + timedelta(hours=3)).isoformat()}})
    await economy_col.update_one({"_id": victim_id}, {"$set": {"last_robbed": now.isoformat()}})
    msg = f"💰 You robbed {member.display_name} and stole {amount} coins!"
    await ctx.send(msg)
    
@rob.error
async def rob_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ You must mention someone to rob. Example: `.rob @User`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ That’s not a valid user.")
    else:
        await ctx.send(f"⚠️ An unexpected error occurred: `{type(error).__name__} - {error}`")

@bot.hybrid_command(name="crime", description="Attempt a risky crime to earn coins.")
@blacklist_barrier()
async def crime(ctx, *, choice: str):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    try:
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        wallet = data.get("wallet", 0)
        inventory = data.get("inventory", [])
        now = datetime.now(timezone.utc)

        last_crime = data.get("last_crime")
        if last_crime:
            last_dt = datetime.fromisoformat(last_crime)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now - last_dt < timedelta(days=1):
                remaining = timedelta(days=1) - (now - last_dt)
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes = remainder // 60
                return await ctx.send(f"🕒 You can commit a crime again in {hours}h {minutes}m.")

        choice = choice.lower().strip()
        valid = ["bank", "shoplift", "payroll"]
        if choice not in valid:
            return await ctx.send("❌ Choose a valid crime: `bank`, `shoplift`, or `payroll`.")

        if choice == "bank":
            if "lockpick" not in inventory:
                return await ctx.send("🔐 You need to buy a **🗝️ Lockpick** to rob the bank!")
            inventory.remove("lockpick")

        config = {
            "bank": {"chance": 0.4, "gain": (1200, 3000), "fine": (600, 1500)},
            "shoplift": {"chance": 0.5, "gain": (300, 600), "fine": (150, 400)},
            "payroll": {"chance": 0.4, "gain": (800, 1500), "fine": (400, 800)},
        }
        conf = config[choice]

        luck_buff = 0.0
        for i, item in enumerate(inventory):
            if isinstance(item, dict) and item.get("_id") == "pet_duck":
                luck_buff = 0.3
                item["uses_left"] -= 1
                await ctx.send("🦆 Your Pet Duck increased your crime success chance!")

                if item["uses_left"] <= 0:
                    inventory.pop(i)
                    await ctx.send("💔 One of your Pet Ducks has left after 3 uses.")
                break

        coffee_bonus = await get_crime_bonus(ctx.author.id, ctx.guild.id)
        if coffee_bonus > 0:
            await ctx.send("☕ **Coffee consumed!** Crime success chance increased by 25%!")

        adjusted_chance = min(conf["chance"] + luck_buff + coffee_bonus, 1.0)
        success = random.random() < adjusted_chance

        if success:
            amount = random.randint(*conf["gain"])
            await add_balance(ctx.author.id, ctx.guild.id, amount)
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"inventory": inventory, "last_crime": now.isoformat()}}
            )
            msg = f"💥 Crime successful! You earned **{amount} coins** via `{choice}` crime."
            await ctx.send(msg)
        else:
            fine = random.randint(*conf["fine"])
            new_wallet = max(0, wallet - fine)
            await economy_col.update_one(
                {"_id": f"{ctx.guild.id}-{ctx.author.id}"},
                {"$set": {"wallet": new_wallet, "inventory": inventory}}
            )
            await ctx.send(f"🚓 You were caught during the `{choice}` attempt. Fined **{fine} coins**.")
    except Exception as e:
        await ctx.send(f"⚠️ An unexpected error occurred, please contact thetruck: `{type(e).__name__} - {e}`")
        
@crime.error
async def crime_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        seconds = int(error.retry_after)
        hours, remainder = divmod(seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        await ctx.send(f"🕒 You can commit a crime again in {hours}h {minutes}m.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ You must specify a crime type. Example: `?crime bank`")
    else:
        await ctx.send(f"⚠️ An unexpected error occurred: `{type(error).__name__} - {error}`\nPlease contact thetruck.")

@bot.hybrid_command(name="passive", description="Toggle passive mode. Staff can manage others.")
@blacklist_barrier()
async def passive(ctx, member: discord.Member = None):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    if member and member != ctx.author:
        if not await staff_only().predicate(ctx):
            return await ctx.send("❌ You don’t have permission to toggle passive mode for others.")
        target = member
    else:
        target = ctx.author

    user_id = f"{ctx.guild.id}-{target.id}"
    now = datetime.now(timezone.utc)

    user_data = await economy_col.find_one({"_id": user_id}) or {}
    passive_until = user_data.get("passive_until")
    last_toggle = user_data.get("last_passive_toggle")

    if last_toggle:
        last_toggle_dt = datetime.fromisoformat(last_toggle)
        if last_toggle_dt.tzinfo is None:
            last_toggle_dt = last_toggle_dt.replace(tzinfo=timezone.utc)
        time_since = (now - last_toggle_dt).total_seconds()
        if time_since < 180:
            remaining = int(180 - time_since)
            return await ctx.send(f"⏳ You must wait **{remaining} seconds** before toggling passive mode again.")

    if passive_until:
        dt = datetime.fromisoformat(passive_until)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt > now:
            await economy_col.update_one(
                {"_id": user_id},
                {"$unset": {"passive_until": ""}, "$set": {"last_passive_toggle": now.isoformat()}}
            )
            if target == ctx.author:
                return await ctx.send("🛡️ Passive mode disabled. You can now rob and be robbed.")
            else:
                return await ctx.send(f"🛡️ Disabled passive mode for {target.display_name}.")

    until_time = now + timedelta(hours=24)
    await economy_col.update_one(
        {"_id": user_id},
        {"$set": {
            "passive_until": until_time.isoformat(),
            "last_passive_toggle": now.isoformat()
        }},
        upsert=True
    )

    if target == ctx.author:
        await ctx.send("🛡️ Passive mode enabled for 24 hours - you can't rob or be robbed.")
    else:
        await ctx.send(f"🛡️ Enabled passive mode for {target.display_name} for 24 hours.")

class ConfirmSellAll(View):
    def __init__(self, ctx, prices, inventory, user_id, wallet):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.value = None
        self.prices = prices
        self.inventory = inventory
        self.user_id = user_id
        self.wallet = wallet

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()

@bot.hybrid_command(name="sell", description="Sell items, investments, or everything at once.")
@app_commands.describe(item="What to sell: item name (e.g., 'rabbit', 'fish'), 'all' to sell everything, or 'inv' to sell inventory")
@blacklist_barrier()
async def sell(ctx, *, item: str = None):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    try:
        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        inventory = data.get("inventory", [])
        wallet = data.get("wallet", 0)

        if not item:
            return await ctx.send("❌ Please specify what to sell (example: `sell rabbit 2` or `sell all`).")

        item_parts = item.lower().strip().split()
        amount = 1

        if item_parts[-1].isdigit():
            amount = int(item_parts[-1])
            item_name = " ".join(item_parts[:-1])
        else:
            item_name = " ".join(item_parts)

        total_gain = 0
        sold_items = []

        prices = {
            "rabbit": 200,
            "deer": 450,
            "bear": 600,
            "fish": 150,
            "iron ore": 200,
            "gold ore": 500,
            "diamond": 1200
        }

        # --- SELL ALL INVENTORY + INVESTMENTS ---
        if item_name == "all":
            confirm_view = ConfirmSellAll(ctx, prices, inventory, user_id, wallet)
            confirm_embed = discord.Embed(
                title="⚠️ Confirm Sell All",
                description=(
                    "You are about to sell **ALL ores, hunted animals, and investments.**\n\n"
                    "This includes:\n"
                    "• Rabbits, deer, bears, fish, ores, diamonds\n"
                    "• All company investments\n\n"
                    "Are you sure you want to continue?"
                ),
                color=discord.Color.red()
            )
            confirm_msg = await ctx.send(embed=confirm_embed, view=confirm_view)
            await confirm_view.wait()

            if confirm_view.value is None:
                return await confirm_msg.edit(content="⌛ Timed out. No items were sold.", embed=None, view=None)
            elif confirm_view.value is False:
                return await confirm_msg.edit(content="❌ Cancelled. No items were sold.", embed=None, view=None)

            total_gain = 0
            sold_items = []

            for inv_item in inventory:
                if isinstance(inv_item, dict):
                    continue

                if inv_item in prices:
                    price = prices[inv_item]
                    total_gain += price
                    sold_items.append(f"1x {inv_item} ({price} each)")

            inventory = [
                i for i in inventory
                if not (isinstance(i, str) and i in prices)
            ]

            investments = await investments_col.find({"user_id": user_id}).to_list(length=None)
            for inv in investments:
                current_value = await calculate_investment_value(inv)
                total_gain += current_value
                sold_items.append(
                    f"Investment in {inv['company']} (ID: {inv['_id']}, {inv['amount']} → {current_value})"
                )
            await investments_col.delete_many({"user_id": user_id})

            if total_gain == 0:
                return await confirm_msg.edit(content="❌ You had nothing to sell.", embed=None, view=None)

            await economy_col.update_one(
                {"_id": user_id},
                {"$set": {"wallet": wallet + total_gain, "inventory": inventory}}
            )

            embed = discord.Embed(
                title="💸 Sell Summary",
                description="\n".join(sold_items),
                color=discord.Color.gold()
            )
            embed.add_field(name="Total Earned", value=f"🪙 {total_gain}", inline=False)
            await confirm_msg.edit(content=None, embed=embed, view=None)
            return

        # --- SELL INVENTORY ONLY ---
        elif item_name in ["inventory", "inv"]:
            for inv_item, price in prices.items():
                count = inventory.count(inv_item)
                if count > 0:
                    total_gain += price * count
                    sold_items.append(f"{count}x {inv_item} ({price} each)")
                    inventory = [i for i in inventory if i != inv_item]

        # --- SELL INVESTMENTS ONLY ---
        elif item_name in ["investments", "all investments"]:
            investments = await investments_col.find({"user_id": user_id}).to_list(length=None)
            for inv in investments:
                current_value = await calculate_investment_value(inv)
                total_gain += current_value
                sold_items.append(f"Investment in {inv['company']} (ID: {inv['_id']}, {inv['amount']} → {current_value})")
            await investments_col.delete_many({"user_id": user_id})

        # --- SELL SPECIFIC ITEM OR INVESTMENT ---
        else:
            investments = await investments_col.find({"user_id": user_id}).to_list(length=None)
            found_investment = False
            for inv in investments:
                if inv["company"].lower() == item_name or str(inv["_id"]) == item_name:
                    current_value = await calculate_investment_value(inv)
                    total_gain += current_value
                    sold_items.append(f"Investment in {inv['company']} (ID: {inv['_id']}, {inv['amount']} → {current_value})")
                    await investments_col.delete_one({"_id": inv["_id"]})
                    found_investment = True
                    break

            if not found_investment:
                if item_name not in prices:
                    return await ctx.send("❌ That item or investment cannot be sold.")
                if inventory.count(item_name) < amount:
                    return await ctx.send(f"❌ You don’t have {amount}x `{item_name}` in your inventory.")
                for _ in range(amount):
                    inventory.remove(item_name)
                gain = prices[item_name] * amount
                total_gain += gain
                sold_items.append(f"{amount}x {item_name} ({prices[item_name]} each)")

        if total_gain == 0:
            return await ctx.send("❌ You have nothing to sell.")

        await economy_col.update_one(
            {"_id": user_id},
            {"$set": {"wallet": wallet + total_gain, "inventory": inventory}}
        )

        desc = "\n".join(sold_items)
        if len(desc) > 4096:
            desc = desc[:4093] + "..."

        embed = discord.Embed(
            title="💸 Sell Summary",
            description="\n".join(sold_items),
            color=discord.Color.gold()
        )
        embed.add_field(name="Total Earned", value=f"🪙 {total_gain}", inline=False)
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send("⚠️ Something went wrong while selling.")
        print(f"[ERROR] sell command: {type(e).__name__} - {e}")
        traceback.print_exc()

async def create_investment(user_id: str, company: str, amount: int):
    inv_id = str(uuid.uuid4())
    await investments_col.insert_one({
        "_id": inv_id,
        "user_id": user_id,
        "company": company,
        "amount": amount,
        "date": datetime.now(timezone.utc).isoformat(),
        "history": []
    })

async def calculate_investment_value(inv: dict) -> int:
    amount = inv["amount"]
    date_obj = datetime.fromisoformat(inv["date"])
    now = datetime.now(timezone.utc)

    days_passed = (now - date_obj).days

    if days_passed < 2:
        return amount

    value = amount
    history = inv.get("history", [])

    for day in range(len(history), days_passed):
        if day == 0:
            change = int(value * random.uniform(0.06, 0.10))
        else:
            if random.random() < 0.30:
                change = -int(value * random.uniform(0.01, 0.10))
            else:
                change = int(value * random.uniform(0.02, 0.10))

        if inv["company"] == "Oceanic":
            change = int(change * 3)

        history.append(change)

    for change in history:
        value += change
        if value < 0:
            value = 0

    await investments_col.update_one(
        {"_id": inv["_id"]},
        {"$set": {"history": history}}
    )

    return int(value)

@bot.hybrid_command(name="invest", description="Invest in fake companies for profit.")
@app_commands.describe(company="Company to invest in (e.g., 'Techify', 'MineCorp', 'Oceanic')", amount="Amount to invest (number or 'all')")
@blacklist_barrier()
async def invest(ctx, company: str = None, amount: str = None):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    companies = {
        "Techify": {"min": 500, "max": 5000},
        "MineCorp": {"min": 300, "max": 3000},
        "Oceanic": {"min": 200, "max": 2500}
    }

    if company and amount:
        company = company.title()
        if company not in companies:
            return await ctx.send(f"❌ Invalid company! Available: {', '.join(companies.keys())}")
        
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)
        wallet = data.get("wallet", 0)
        
        if amount.lower() == "all":
            invest_amount = wallet
        else:
            try:
                invest_amount = int(amount)
            except ValueError:
                return await ctx.send("❌ Invalid amount! Use a number or 'all'.")
        
        stats = companies[company]
        if invest_amount < stats["min"]:
            return await ctx.send(f"❌ Minimum investment for {company} is {stats['min']} coins!")
        if invest_amount > stats["max"]:
            return await ctx.send(f"❌ Maximum investment for {company} is {stats['max']} coins!")
        if invest_amount > wallet:
            return await ctx.send("❌ You don't have enough coins!")
        
        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        await investments_col.insert_one({
            "user_id": user_id,
            "company": company,
            "amount": invest_amount,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        await subtract_balance(ctx.author.id, ctx.guild.id, invest_amount)
        await ctx.send(f"📈 Invested {invest_amount} coins in {company}!")
        return

    embed = discord.Embed(
        title="📈 Investment Opportunities",
        description="Choose a company to invest in!",
        color=discord.Color.green()
    )
    for name, stats in companies.items():
        embed.add_field(
            name=name,
            value=f"Investment Range: {stats['min']} - {stats['max']} coins",
            inline=False
        )
    embed.set_footer(text="Unofficial Analyst Ranking: Techify ⭐⭐⭐ > MineCorp ⭐⭐ > Oceanic ⭐")
    
    view = View()

    for company, stats in companies.items():
        async def button_callback(interaction, company=company, stats=stats):
            if interaction.user != ctx.author:
                return await interaction.response.send_message("❌ Not your investment.", ephemeral=True)

            user_id = f"{ctx.guild.id}-{ctx.author.id}"
            data = await get_user(ctx, ctx.guild.id, ctx.author.id)
            wallet = data.get("wallet", 0)
            user_investments = await investments_col.count_documents({"user_id": user_id})

            if user_investments >= 5:
                return await interaction.response.send_message(
                    "❌ You can only have up to **5 active investments** at a time. Sell some before investing again.",
                    ephemeral=True
                )

            step = 500
            amounts = [x for x in range(stats["min"], stats["max"] + step, step)]
            options = [
                discord.SelectOption(label=f"{amt} coins", value=str(amt))
                for amt in amounts
            ]

            select = Select(
                placeholder=f"Choose amount to invest in {company}",
                options=options
            )

            async def select_callback(inter: discord.Interaction):
                if inter.user != ctx.author:
                    return await inter.response.send_message("❌ Not your selection.", ephemeral=True)

                invest_amount = int(select.values[0])

                if wallet < invest_amount:
                    return await inter.response.send_message(
                        f"❌ You only have {wallet} coins but tried to invest {invest_amount}.",
                        ephemeral=True
                    )

                new_wallet = wallet - invest_amount

                await economy_col.update_one(
                    {"_id": user_id},
                    {"$set": {"wallet": new_wallet}},
                    upsert=True
                )

                await create_investment(user_id, company, invest_amount)

                await inter.response.send_message(
                    f"✅ You invested **{invest_amount} coins** in **{company}**."
                )

            select.callback = select_callback
            await interaction.response.send_message(
                f"💰 Choose how much to invest in **{company}**:",
                view=View().add_item(select),
                ephemeral=True
            )

        view.add_item(Button(label=company, style=discord.ButtonStyle.green, custom_id=f"invest_{company}"))
        view.children[-1].callback = button_callback

    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="investstatus", description="Check your investments.")
@blacklist_barrier()
async def investstatus(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return

    user_id = f"{ctx.guild.id}-{ctx.author.id}"
    investments = await investments_col.find({"user_id": user_id}).to_list(length=None)

    if not investments:
        return await ctx.send("❌ You don’t have any active investments.")

    embed = discord.Embed(
        title=f"📊 {ctx.author.display_name}'s Investments",
        color=discord.Color.blue()
    )

    for inv in investments:
        company = inv["company"]
        amount = inv["amount"]
        current_value = await calculate_investment_value(inv)
        inv_id = inv["_id"]

        date_obj = datetime.fromisoformat(inv["date"])
        unix_timestamp = int(date_obj.timestamp())

        embed.add_field(
            name=f"{company} (ID: {inv_id})",
            value=(
                f"Invested: 🪙 {amount}\n"
                f"Current Value: 🪙 {current_value}\n"
                f"Date: <t:{unix_timestamp}:F>"
            ),
            inline=False
        )

    await ctx.send(embed=embed)

@bot.hybrid_command(name="hunt", description="Go hunting for animals.")
@commands.cooldown(1, 3600, commands.BucketType.member)
@blacklist_barrier()
async def hunt(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)

        inventory = data.get("inventory", [])
        
        has_rifle = False
        for item in inventory:
            if isinstance(item, str) and item == "rifle":
                has_rifle = True
                break
            elif isinstance(item, dict) and item.get("_id") == "rifle":
                has_rifle = True
                break
        
        if not has_rifle:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("🔫 You need a rifle to hunt!")

        animals = [
            ("rabbit", 200),
            ("deer", 450),
            ("bear", 600)
        ]

        catch = random.choice(animals)
        animal, value = catch

        inventory = data.get("inventory", [])
        inventory.append(animal)

        await economy_col.update_one(
            {"_id": user_id},
            {"$set": {"inventory": inventory}}
        )

        await ctx.send(f"🏹 You hunted a **{animal}**! (Sell value: {value} coins)")

    except Exception as e:
        ctx.command.reset_cooldown(ctx)
        await ctx.send("⚠️ Something went wrong while hunting. Contact thetruck.")
        print(f"[ERROR] hunt command: {type(e).__name__} - {e}")
        traceback.print_exc()


@hunt.error
async def hunt_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        total_seconds = int(error.retry_after)
        minutes = total_seconds // 60
        return await ctx.send(f"🕒 You can hunt again in {minutes} minutes.")
    else:
        await ctx.send("⚠️ An unexpected error occurred while hunting.")


@bot.hybrid_command(name="mine", description="Go mining for ores.")
@commands.cooldown(1, 3600, commands.BucketType.member)
@blacklist_barrier()
async def mine(ctx):
    if not await check_channel(ctx, "economy_channel", "Economy"):
        return
    try:
        user_id = f"{ctx.guild.id}-{ctx.author.id}"
        data = await get_user(ctx, ctx.guild.id, ctx.author.id)

        inventory = data.get("inventory", [])
        
        has_pickaxe = False
        for item in inventory:
            if isinstance(item, str) and item == "pickaxe":
                has_pickaxe = True
                break
            elif isinstance(item, dict) and item.get("_id") == "pickaxe":
                has_pickaxe = True
                break
        
        if not has_pickaxe:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("⛏️ You need a pickaxe to mine!")

        ores = [
            ("iron ore", 200),
            ("gold ore", 500),
            ("diamond", 1200)
        ]

        catch = random.choice(ores)
        ore, value = catch

        inventory = data.get("inventory", [])
        inventory.append(ore)

        await economy_col.update_one(
            {"_id": user_id},
            {"$set": {"inventory": inventory}}
        )

        await ctx.send(f"⛏️ You mined **{ore}**! (Sell value: {value} coins)")

    except Exception as e:
        ctx.command.reset_cooldown(ctx)
        await ctx.send("⚠️ Something went wrong while mining. Contact thetruck.")
        print(f"[ERROR] mine command: {type(e).__name__} - {e}")
        traceback.print_exc()

@mine.error
async def mine_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        total_seconds = int(error.retry_after)
        minutes = total_seconds // 60
        return await ctx.send(f"🕒 You can mine again in {minutes} minutes.")
    else:
        await ctx.send("⚠️ An unexpected error occurred while mining.")

class AnswerButton(discord.ui.Button):
    def __init__(self, label: str, value: int, parent_view):
        super().__init__(style=discord.ButtonStyle.primary, label=label, custom_id=str(value))
        self.value = value
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message("This quiz isn't yours.", ephemeral=True)

        idx = view.current_index
        if view.answered_ids.get(idx):
            return await interaction.response.send_message("You already answered this question.", ephemeral=True)

        view.answered_ids[idx] = True
        correct_answer = view.questions[idx]['answer']
        if self.value == correct_answer:
            view.score += 1

        view.disable_all_buttons()
        await interaction.response.edit_message(view=view)

        reply = ("✅ Correct!" if self.value == correct_answer
                 else f"❌ Wrong! Answer was: {view.questions[idx]['options'][correct_answer-1]}")
        await interaction.followup.send(reply, ephemeral=True)

        view.current_index += 1
        await view.show_next(interaction)

class QuizView(discord.ui.View):
    def __init__(self, ctx, quiz_id, questions_list):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.user_id = ctx.author.id
        self.quiz_id = quiz_id
        self.questions = questions_list
        self.current_index = 0
        self.score = 0
        self.answered_ids = {}
        for i in range(1, 5):
            self.add_item(AnswerButton(str(i), i, self))

    def disable_all_buttons(self):
        for item in self.children:
            item.disabled = True

    async def show_next(self, interaction: discord.Interaction = None):
        if self.current_index >= len(self.questions):
            await self.finish_quiz(interaction)
            return

        q = self.questions[self.current_index]
        opts = "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(q["options"]))
        embed = discord.Embed(
            title=f"Question {self.current_index+1}/{len(self.questions)}",
            description=q["q"],
            color=discord.Color.teal()
        )
        embed.add_field(name="Options", value=opts, inline=False)
        embed.set_footer(text="Click a button below to answer.")

        self.clear_items()
        for i in range(1, 5):
            self.add_item(AnswerButton(str(i), i, self))

        if interaction:
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        else:
            await self.ctx.send(embed=embed, view=self, ephemeral=True)

    async def finish_quiz(self, interaction: discord.Interaction = None):
        pct = self.score / len(self.questions) * 100.0
        passed = pct >= PASS_PCT
    
        await quiz_col.update_one(
            {"_id": self.quiz_id},
            {"$set": {
                "score": self.score,
                "completed": datetime.now(timezone.utc),
                "passed": passed
            }}
        )
    
        result = f"📊 You scored **{self.score}/{len(self.questions)}** = **{pct:.1f}%**"
    
        if passed:
            config = await config_col.find_one({"guild": str(self.ctx.guild.id)}) or {}
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except Exception:
                    config = {}
            if not isinstance(config, dict):
                config = {}
            role_ids = config.get("ROLE_ID", [])
    
            if isinstance(role_ids, int):
                role_ids = [role_ids]
            elif isinstance(role_ids, str) and role_ids.isdigit():
                role_ids = [int(role_ids)]
            elif isinstance(role_ids, list):
                role_ids = [int(r) for r in role_ids if str(r).isdigit()]
            else:
                role_ids = []
    
            roles_to_add = [self.ctx.guild.get_role(rid) for rid in role_ids if self.ctx.guild.get_role(rid)]
    
            if roles_to_add:
                await self.ctx.author.add_roles(*roles_to_add, reason="Passed duck quiz")
                role_names = ", ".join([r.name for r in roles_to_add])
                result += f"\n🎉 You passed and earned the **{role_names}** role!"
            else:
                result += "\n⚠️ Role configured, but could not find it on the server."
    
        if interaction:
            await interaction.followup.send(result, ephemeral=True)
        else:
            await self.ctx.send(result)
    
        self.stop()

@bot.hybrid_command(name="duckquiz", description="Standardized Duck Quiz.")
@blacklist_barrier()
async def duckquiz(ctx):
    cfg_raw = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}
    if isinstance(cfg_raw, str):
        try:
            cfg_raw = json.loads(cfg_raw)
        except Exception:
            cfg_raw = {}
    if not isinstance(cfg_raw, dict):
        cfg_raw = {}
    quiz_channels = cfg_raw.get("QUIZ_CHANNEL")

    if isinstance(quiz_channels, str) and quiz_channels.isdigit():
        quiz_channels = [int(quiz_channels)]
    elif isinstance(quiz_channels, list):
        quiz_channels = [int(x) for x in quiz_channels if str(x).isdigit()]
    else:
        quiz_channels = []

    if quiz_channels and ctx.channel.id not in quiz_channels:
        mention = f"<#{quiz_channels[0]}>" if quiz_channels else "`a quiz channel`"
        return await ctx.send(f"❌ Please use this command in {mention}.")

    USER, GUILD = str(ctx.author.id), str(ctx.guild.id)
    now = datetime.now(timezone.utc)

    role_ids = cfg_raw.get("ROLE_ID", [])
    if isinstance(role_ids, int):
        role_ids = [role_ids]
    elif isinstance(role_ids, str) and role_ids.isdigit():
        role_ids = [int(role_ids)]

    user_roles = [r.id for r in ctx.author.roles]
    if any(rid in user_roles for rid in role_ids):
        await ctx.send("ℹ You’ve already passed; type `yes` within 30s to retake.")
        try:
            msg = await bot.wait_for(
                "message",
                timeout=30,
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel
            )
            if msg.content.strip().lower() != "yes":
                return await ctx.send("✅ Quiz cancelled.")
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Timed out - quiz cancelled.")

    user_doc = await quiz_col.find_one({"guild": GUILD, "user": USER})
    last_use = user_doc.get("last_quiz") if user_doc else None
    if last_use:
        last_dt = datetime.fromisoformat(last_use).replace(tzinfo=timezone.utc)
        if now - last_dt < timedelta(hours=1):
            remaining = timedelta(hours=1) - (now - last_dt)
            mins = int(remaining.total_seconds() // 60)
            return await ctx.send(f"🕒 You can take the quiz again in {mins} minute(s).")

    used = await quiz_col.distinct("qid", {"guild": GUILD, "used": True})
    pool = [q for q in questions if isinstance(q.get("id"), (int, str)) and q["id"] not in used]
    if len(pool) < NUM_Q:
        await quiz_col.update_many({"guild": GUILD}, {"$unset": {"used": ""}})
        pool = [q for q in questions if isinstance(q.get("id"), (int, str))]

    selected = random.sample(pool, NUM_Q)

    quiz_doc = {
        "guild": GUILD,
        "user": USER,
        "started": now,
        "questions": [q["id"] for q in selected],
        "answers": {},
        "score": 0,
        "completed": None,
        "passed": False
    }

    res = await quiz_col.insert_one(quiz_doc)

    await quiz_col.update_one(
        {"guild": GUILD, "user": USER},
        {"$set": {"last_quiz": now.isoformat()}},
        upsert=True
    )

    view = QuizView(ctx, res.inserted_id, selected)
    await view.show_next()

@duckquiz.error
async def duckquiz_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        mins = int(error.retry_after // 60)
        await ctx.send(f"🕒 Please wait another **{mins} minute(s)** before taking the quiz again.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing arguments, type the quiz command without additional input (no parameters required).")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You can't use this command right now.")
    else:
        await ctx.send(f"⚠️ An unexpected error occurred, please contact thetruck: `{type(error).__name__} - {error}`")

@bot.hybrid_command(name="quackcount", description="Check the server's total quacks and a user's quacks.")
async def quackcount(ctx, member: discord.Member | None = None):
    guild_id = str(ctx.guild.id)
    config = await config_col.find_one({"guild": guild_id})

    if not config or config.get("quack_count", 0) == 0:
        return await ctx.send("🦆 No quacks have been counted yet!")

    target = member or ctx.author
    user_id = str(target.id)

    user_quacks = config.get("quacks", {}).get(user_id, 0)
    total_quacks = config.get("quack_count", 0)

    label = "Your" if target.id == ctx.author.id else f"{target.display_name}'s"

    await ctx.send(
        f"🦆 **Server Quacks:** {total_quacks}\n"
        f"🦆 **{label} Quacks:** {user_quacks}"
    )

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
        for i, (user_id, count) in enumerate(self.entries[start:end], start=start+1):
            member = self.ctx.guild.get_member(int(user_id))
            name = member.display_name if member else f"User ID {user_id}"
            description += f"**{i}. {name}** — {count} quacks\n"

        embed = discord.Embed(
            title=f"🦆 Quack Leaderboard (Page {self.page+1}/{self.max_page+1})",
            description=description,
            color=discord.Color.green()
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

@bot.hybrid_command(name="quacktop", description="View the top quackers in this server.")
async def quacktop(ctx):
    guild_id = str(ctx.guild.id)
    config = await config_col.find_one({"guild": guild_id})

    if not config or not config.get("quacks"):
        return await ctx.send("🦆 No quacks have been counted yet!")

    top_quackers = sorted(config["quacks"].items(), key=lambda x: x[1], reverse=True)

    view = QuackTopView(ctx, top_quackers)
    await ctx.send(embed=view.get_embed(), view=view)

@bot.hybrid_command(name="slap", description="Slap another user")
@app_commands.describe(member="The user to slap (optional - will slap yourself if not provided)")
@commands.cooldown(1, 5, commands.BucketType.member)
@blacklist_barrier()
async def slap(ctx, member: discord.Member = None):
    if not member:
        await ctx.send("❌ You need to mention someone to slap!")
        return

    try:
        await ctx.defer()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://tenor.googleapis.com/v2/search?q=anime slap&key={TENOR_API_KEY}&limit=20",
                timeout=5
            ) as r:
                if r.status != 200:
                    raise Exception(f"HTTP {r.status}")
                data = await r.json()

        results = data.get("results", [])
        if not results:
            await ctx.send("❌ Couldn't find any slap GIFs right now.")
            return

        gif_url = random.choice(results)["media_formats"]["gif"]["url"]

        embed = discord.Embed(
            title="👋 Slap!",
            description=f"{ctx.author.mention} slapped {member.mention}! Ouch!",
            color=discord.Color.red()
        )
        embed.set_image(url=gif_url)
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"⚠️ Something went wrong while fetching the slap GIF: `{e}`")

@bot.hybrid_command(name="duckfact", description="Get a random duck fact")
@commands.cooldown(1, 5, commands.BucketType.member)
@blacklist_barrier()
async def duckfact(ctx):
    try:
        with open("duckfacts.txt", "r", encoding="utf-8") as f:
            facts = [line.strip() for line in f if line.strip()]
        
        if not facts:
            raise ValueError("Duck facts file is empty.")

        fact = random.choice(facts)

        embed = discord.Embed(
            title="🦆 Duck Fact",
            description=fact,
            color=discord.Color.teal()
        )
        embed.set_thumbnail(url="https://random-d.uk/api/v2/random")
        await ctx.send(embed=embed)

    except FileNotFoundError:
        await ctx.send("❌ Could not find `duckfacts.txt`. Please create it in the bot folder.")
    except Exception as e:
        await ctx.send(f"⚠️ Something went wrong while fetching a duck fact: `{e}`")

@bot.hybrid_command(name="afk", description="Set your AFK status.")
async def afk(ctx, *, reason="AFK"):
    afk_key = f"{ctx.guild.id}-{ctx.author.id}"

    await afk_col.update_one(
        {"_id": afk_key},
        {"$set": {
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_nick": ctx.author.nick
        }},
        upsert=True
    )

    if ctx.author.display_name.startswith("[AFK]"):
        await ctx.send(f"🛌 You are now AFK: {reason}", delete_after=7)
        return

    try:
        new_nick = f"[AFK] {ctx.author.display_name}"
        await ctx.author.edit(nick=new_nick)
    except discord.Forbidden:
        await ctx.send(
            "⚠️ I can't change your nickname (role hierarchy or missing permissions). AFK still set!",
            delete_after=5
            )
    except discord.HTTPException:
        await ctx.send("⚠️ Something went wrong while changing your nickname. AFK still set!")

    await ctx.send(f"🛌 You are now AFK: {reason}", delete_after=7)

async def ticket_error(interaction: discord.Interaction, func):
    try:
        return await func()
    except Exception as e:
        embed = discord.Embed(
            title="⚠️ Error",
            description=f"An unexpected error occurred:\n```{str(e)}```",
            color=discord.Color.red()
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

class TicketSetupModal(discord.ui.Modal, title="Create Ticket Panel"):
    panel_name = discord.ui.TextInput(label="Panel Name", placeholder="Example: SupportPanel1", required=True)
    embed_title = discord.ui.TextInput(label="Embed Title", placeholder="Example: 🎫 Need Help?", required=True)
    embed_desc = discord.ui.TextInput(label="Embed Description", placeholder="Click a button below to create a ticket.", required=True, style=discord.TextStyle.paragraph)
    embed_color = discord.ui.TextInput(label="Embed Color (hex)", placeholder="#5865F2", required=False)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction):
        guild = self.ctx.guild
        data = await settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None

        if not staff_role_id or staff_role_id not in [r.id for r in self.ctx.author.roles]:
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only staff members can use this command.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        color_value = int(self.embed_color.value.replace("#", ""), 16) if self.embed_color.value else 0x5865F2
        await ticket_panels_col.insert_one({
            "guild": str(guild.id),
            "panel_name": self.panel_name.value,
            "ticket_embed_title": self.embed_title.value,
            "ticket_embed_desc": self.embed_desc.value,
            "ticket_embed_color": color_value,
            "buttons": []
        })

        embed = discord.Embed(
            title="✅ Ticket Panel Created",
            description=f"Panel `{self.panel_name.value}` created successfully!\nUse `/ticketaddbutton` to add buttons.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

class TicketAddButtonModal(discord.ui.Modal, title="Add Ticket Panel Button"):
    panel_name = discord.ui.TextInput(label="Panel Name", placeholder="Example: SupportPanel1", required=True)
    category_name = discord.ui.TextInput(label="Ticket Category", placeholder="Example: Support", required=True)
    button_label = discord.ui.TextInput(label="Button Label", placeholder="Example: Open Support Ticket", required=True)
    emoji = discord.ui.TextInput(label="Emoji (optional)", placeholder="Example: 🎫", required=False)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction):
        guild = self.ctx.guild
        data = await settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None

        if not staff_role_id or staff_role_id not in [r.id for r in self.ctx.author.roles]:
            embed = discord.Embed(
                title="❌ Access Denied",
                description="Only staff members can use this command.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        panel_data = await ticket_panels_col.find_one({"guild": str(guild.id), "panel_name": self.panel_name.value})
        if not panel_data:
            await interaction.followup.send(embed=discord.Embed(
                title="❌ Not Found",
                description=f"No panel found with name `{self.panel_name.value}`.",
                color=discord.Color.red()
            ), ephemeral=True)
            return

        new_button = {
            "category_name": self.category_name.value,
            "label": self.button_label.value,
            "emoji": self.emoji.value if self.emoji.value else None
        }
        await ticket_panels_col.update_one(
            {"guild": str(guild.id), "panel_name": self.panel_name.value},
            {"$push": {"buttons": new_button}}
        )

        embed = discord.Embed(
            title="✅ Button Added",
            description=f"Added button to panel `{self.panel_name.value}`:\n{self.emoji.value or ''} **{self.button_label.value}** → Category `{self.category_name.value}`",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

class TicketEditButtonModal(discord.ui.Modal, title="Edit Ticket Panel Button"):
    category_name = discord.ui.TextInput(label="Ticket Category", required=True)
    button_label = discord.ui.TextInput(label="Button Label", required=True)
    emoji = discord.ui.TextInput(label="Emoji (optional)", required=False)

    def __init__(self, ctx, panel_name, btn_data):
        super().__init__()
        self.ctx = ctx
        self.panel_name = panel_name
        self.btn_data = btn_data
        self.category_name.default = btn_data.get("category_name", "")
        self.button_label.default = btn_data.get("label", "")
        self.emoji.default = btn_data.get("emoji", "")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await ticket_error(interaction, lambda: self._handle_submit(interaction))

    async def _handle_submit(self, interaction: discord.Interaction):
        guild = self.ctx.guild
        await ticket_panels_col.update_one(
            {"guild": str(guild.id), "panel_name": self.panel_name, "buttons.label": self.btn_data["label"]},
            {"$set": {
                "buttons.$.category_name": self.category_name.value,
                "buttons.$.label": self.button_label.value,
                "buttons.$.emoji": self.emoji.value if self.emoji.value else None,
            }}
        )

        embed = discord.Embed(
            title="✅ Button Updated",
            description=f"Updated button in panel `{self.panel_name}`:\n"
                        f"{self.emoji.value or ''} **{self.button_label.value}** "
                        f"→ Category `{self.category_name.value}`",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

class TicketButtonActionView(discord.ui.View):
    def __init__(self, ctx, panel_name, btn_data):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.panel_name = panel_name
        self.btn_data = btn_data

    @discord.ui.button(label="✏ Edit", style=discord.ButtonStyle.blurple)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ticket_error(interaction, lambda: self._edit(interaction))

    async def _edit(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can edit.",
                ephemeral=True
            )
        modal = TicketEditButtonModal(self.ctx, self.panel_name, self.btn_data)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑 Delete", style=discord.ButtonStyle.red)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ticket_error(interaction, lambda: self._delete(interaction))

    async def _delete(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can delete.",
                ephemeral=True
            )

        guild = self.ctx.guild
        await ticket_panels_col.update_one(
            {"guild": str(guild.id), "panel_name": self.panel_name},
            {"$pull": {"buttons": {"label": self.btn_data["label"]}}}
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                title="🗑 Button Deleted",
                description=f"Removed **{self.btn_data['label']}** from panel `{self.panel_name}`.",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        self.stop()

class TicketPanelEditView(discord.ui.View):
    def __init__(self, ctx, panel_data):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.panel_data = panel_data
        for btn in panel_data.get("buttons", []):
            self.add_item(TicketEditButton(btn, panel_data, ctx))

class TicketEditButton(discord.ui.Button):
    def __init__(self, btn_data, panel_data, ctx):
        safe_category = btn_data['category_name'].replace(" ", "_")
        safe_label = btn_data['label'].replace(" ", "_")

        super().__init__(
            label=btn_data.get("label", "Unnamed"),
            emoji=btn_data.get("emoji") or None,
            style=discord.ButtonStyle.gray,
            custom_id=f"editbtn_{safe_category}_{safe_label}"
        )
        self.btn_data = btn_data
        self.panel_data = panel_data
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await ticket_error(interaction, lambda: self._callback(interaction))

    async def _callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "❌ Only the staff member who ran the command can manage buttons.",
                ephemeral=True
            )

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"⚙ Manage Button: {self.btn_data.get('label', 'Unnamed')}",
                description="Choose what you want to do with this button.",
                color=discord.Color.orange()
            ),
            view=TicketButtonActionView(self.ctx, self.panel_data["panel_name"], self.btn_data),
            ephemeral=True
        )

class TicketPanelView(discord.ui.View):
    def __init__(self, panel_data):
        super().__init__(timeout=None)
        self.panel_data = panel_data
        for btn in panel_data.get("buttons", []):
            self.add_item(TicketCategoryButton(btn, panel_data))

class TicketCategoryButton(discord.ui.Button):
    def __init__(self, btn_data, panel_data):
        safe_category = btn_data['category_name'].replace(" ", "_")
        safe_label = btn_data['label'].replace(" ", "_")
        guild_id = panel_data.get("guild", "unknown")
        panel_name = panel_data.get("panel_name", "unknown").replace(" ", "_")

        super().__init__(
            label=btn_data.get("label", "Open Ticket"),
            emoji=btn_data.get("emoji") or None,
            style=discord.ButtonStyle.green,
            custom_id=f"ticket_{guild_id}_{panel_name}_{safe_category}_{safe_label}"
        )
        self.btn_data = btn_data
        self.panel_data = panel_data

    async def callback(self, interaction: discord.Interaction):
        await ticket_error(interaction, lambda: self.create_ticket(interaction))

    async def create_ticket(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        author = interaction.user

        counter_doc = await tickets_counter_col.find_one({"guild": str(guild.id)})
        if not counter_doc:
            ticket_number = 1
            await tickets_counter_col.insert_one({"guild": str(guild.id), "counter": ticket_number})
        else:
            ticket_number = counter_doc["counter"] + 1
            await tickets_counter_col.update_one({"guild": str(guild.id)}, {"$set": {"counter": ticket_number}})

        safe_username = re.sub(r'[^a-zA-Z0-9_-]', '', author.name).lower()
        safe_label = re.sub(r'[^a-zA-Z0-9_-]', '', self.btn_data["label"]).replace(" ", "-").lower()
        ticket_name = f"{safe_username}-{safe_label}"

        if len(ticket_name) > 90:
            available = 90 - (len(safe_username) + 1)
            if available < 1:
                safe_username = safe_username[:45]
                safe_label = safe_label[:44]
            else:
                safe_label = safe_label[:available]
            ticket_name = f"{safe_username}-{safe_label}"

        data = await settings_col.find_one({"guild": str(guild.id)})
        staff_role_id = data.get("staff_role") if data else None
        if not staff_role_id:
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Staff Role Not Set",
                    description="Use `/staffset` first.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )

        staff_role = guild.get_role(int(staff_role_id))
        category = discord.utils.get(guild.categories, name="Tickets") or await guild.create_category("Tickets")

        for c in category.channels:
            if c.name.lower() == ticket_name.lower():
                return await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Duplicate Ticket",
                        description=f"A ticket with that name already exists: {c.mention}",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )

        category_name = self.btn_data["category_name"].lower()
        category_support_members = await get_category_support_members(guild, category_name)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, embed_links=True, attach_files=True),
            author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True)
        }

        for member in category_support_members:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True
            )

        channel = await guild.create_text_channel(ticket_name, category=category, overwrites=overwrites)

        await tickets_col.insert_one({
            "guild": str(guild.id),
            "channel_id": str(channel.id),
            "owner_id": str(author.id),
            "category": category_name.lower(),
            "created_at": datetime.now(timezone.utc)
        })

        embed = discord.Embed(
            title="🎟️ Ticket Created",
            description="Please state your concern and the staff team will respond soon.",
            color=discord.Color(self.panel_data.get("ticket_embed_color", 0x5865F2))
        )

        await channel.send(embed=embed)

        await ping_ticket_roles(channel, guild.id)

        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Ticket Created!",
                description=f"Your ticket was successfully created!\nHere it is: {channel.mention}",
                color=discord.Color.green()
            ),
            ephemeral=True
        )

def is_prefix(ctx):
    return not hasattr(ctx, "interaction") or ctx.interaction is None

async def ping_ticket_roles(channel: discord.TextChannel, guild_id: str):
    try:
        allowed_members = []
        staff_role_mention = ""

        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member):
                if overwrite.view_channel:
                    allowed_members.append(target)
            elif isinstance(target, discord.Role):
                if overwrite.view_channel and target.name.lower() != "@everyone":
                    staff_role_mention = target.mention

        if not allowed_members:
            pass

        if not staff_role_mention:
            data = await settings_col.find_one({"guild": str(guild_id)})
            staff_role_id = data.get("staff_role") if data else None
            if staff_role_id:
                staff_role = channel.guild.get_role(int(staff_role_id))
                if staff_role:
                    staff_role_mention = staff_role.mention

        if not allowed_members and not staff_role_mention:
            return

        ping_parts = []
        if staff_role_mention:
            ping_parts.append(staff_role_mention)
        if allowed_members:
            ping_parts.extend(member.mention for member in allowed_members)

        ping_text = " ".join(ping_parts)

        msg = await channel.send(content=ping_text)
        await msg.delete(delay=0)

    except Exception:
        print("ping_ticket_roles ERROR:", traceback.format_exc())

async def actually_close_ticket(ctx, opener, forced=False):
    channel = ctx.channel

    messages = [msg async for msg in channel.history(limit=None, oldest_first=True)]
    transcript_text = "\n".join([f"[{msg.created_at}] {msg.author}: {msg.content}" for msg in messages])
    ticket_id = f"{channel.id}-{int(datetime.now(timezone.utc).timestamp())}"

    await tickets_col.insert_one({
        "ticket_id": ticket_id,
        "guild_id": str(channel.guild.id),
        "channel_id": str(channel.id),
        "opener_id": str(opener.id) if opener else None,
        "closer_id": str(ctx.author.id),
        "closer_name": str(ctx.author),
        "transcript": transcript_text,
        "created_at": str(channel.created_at),
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "forced": forced
    })

    transcript_file = io.StringIO(transcript_text)
    discord_file = discord.File(fp=transcript_file, filename=f"{ticket_id}_transcript.txt")

    if opener:
        try:
            await opener.send(
                embed=discord.Embed(
                    title="📜 Ticket Transcript",
                    description=f"Transcript for `{channel.name}` attached below.",
                    color=discord.Color.blue()
                ),
                file=discord_file
            )
        except:
            pass

    action_type = "forceclose" if forced else "close"
    closer_text = f"{ctx.author} ({ctx.author.mention})"
    opener_text = f"{opener} ({opener.mention})" if opener else "Unknown"
    await log_action(
        ctx,
        f"Ticket `{channel.name}` closed by {closer_text} "
        f"(opener: {opener_text}){' [FORCED]' if forced else ''}",
        user_id=ctx.author.id,
        action_type=action_type
    )

    if forced:
        await channel.send(f"✅ Ticket force-closed by {ctx.author.mention}.")
    else:
        await channel.send("✅ Ticket confirmed and closed.")
    await channel.delete()

@bot.hybrid_command(name="ticketaddbutton", description="Add a button to an existing ticket panel (form). Staff-only.")
@staffperm("tickets:admin")
@staff_only()
async def ticketaddbutton(ctx):
    try:
        if is_prefix(ctx):
            def check(m):
                return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

            await ctx.send("📝 Enter the **panel name**:")
            panel_name_msg = await bot.wait_for("message", check=check)
            panel_name = panel_name_msg.content

            await ctx.send("🗂 Enter the **ticket category name**:")
            category_msg = await bot.wait_for("message", check=check)
            category_name = category_msg.content

            await ctx.send("🔘 Enter the **button label**:")
            label_msg = await bot.wait_for("message", check=check)
            button_label = label_msg.content

            await ctx.send("😎 Enter an **emoji** (optional, type `none` to skip):")
            emoji_msg = await bot.wait_for("message", check=check)
            emoji = None if emoji_msg.content.lower() == "none" else emoji_msg.content

            guild = ctx.guild
            panel_data = await ticket_panels_col.find_one({
                "guild": str(guild.id),
                "panel_name": panel_name
            })

            if not panel_data:
                return await ctx.send(f"❌ No panel found with name `{panel_name}`.")

            new_button = {
                "category_name": category_name,
                "label": button_label,
                "emoji": emoji
            }

            await ticket_panels_col.update_one(
                {"guild": str(guild.id), "panel_name": panel_name},
                {"$push": {"buttons": new_button}}
            )

            return await ctx.send(
                f"✅ Added button to panel `{panel_name}`:\n"
                f"{emoji or ''} **{button_label}** → Category **{category_name}**"
            )
        await ctx.interaction.response.send_modal(TicketAddButtonModal(ctx))
    except Exception as e:
        print("ticketaddbutton ERROR:", traceback.format_exc())
        if ctx.interaction:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(f"❌ Error:\n```{e}```", ephemeral=True)

@bot.hybrid_command(name="ticketsetup", description="Create interactive ticket panel. Staff-only.")
@app_commands.describe(panel_name="Name for the ticket panel (e.g., 'Support', 'Help Desk')")
@staffperm("tickets:admin")
@staff_only()
async def ticketsetup(ctx, panel_name: str = "Support"):
    try:
        data = await settings_col.find_one({"guild": str(ctx.guild.id)})
        staff_role_id = data.get("staff_role") if data else None
        if not staff_role_id or staff_role_id not in [r.id for r in ctx.author.roles]:
            msg = "❌ Only staff members can create a panel."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        if is_prefix(ctx):
            return await ctx.send("⚠ This command requires modal interaction. Please use the command properly.")
        await ctx.interaction.response.send_modal(TicketSetupModal(ctx))
    except Exception as e:
        print("ticketsetup ERROR:", traceback.format_exc())
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.send_message(f"❌ Error:\n```{e}```", ephemeral=True)

@bot.hybrid_command(name="ticketpanel", description="Post a saved ticket panel. Staff-only.")
@staffperm("tickets:admin")
@staff_only()
async def ticketpanel(ctx, panel_name: str):
    try:
        panel_data = await ticket_panels_col.find_one({
            "guild": str(ctx.guild.id),
            "panel_name": panel_name
        })
        if not panel_data:
            msg = f"❌ No ticket panel found with name `{panel_name}`."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        embed = discord.Embed(
            title=panel_data.get("ticket_embed_title", "🎫 Ticket Panel"),
            description=panel_data.get("ticket_embed_desc", "Click a button below to create a ticket."),
            color=discord.Color(int(panel_data.get("ticket_embed_color", 0x5865F2)))
        )
        view = TicketPanelView(panel_data)

        if ctx.interaction:
            msg = await ctx.interaction.response.send_message(embed=embed, view=view)
        else:
            msg = await ctx.send(embed=embed, view=view)
        
        await ticket_panels_col.update_one(
            {"_id": panel_data["_id"]},
            {"$set": {"message_id": msg.id, "channel_id": str(msg.channel.id)}}
        )

    except Exception as e:
        print("ticketpanel ERROR:", traceback.format_exc())
        if ctx.interaction:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(f"❌ Error:\n```{e}```", ephemeral=True)
        else:
            await ctx.send(f"❌ Error:\n```{e}```")

@bot.hybrid_command(name="ticketeditbutton", description="Edit a button in a ticket panel. Staff-only.")
@staffperm("tickets:admin")
@staff_only()
async def ticketeditbutton(ctx, panel_name: str):
    try:
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.defer(ephemeral=True)

        panel_data = await ticket_panels_col.find_one({
            "guild": str(ctx.guild.id),
            "panel_name": panel_name
        })
        if not panel_data:
            msg = f"❌ No ticket panel found with name `{panel_name}`."
            if ctx.interaction:
                await ctx.interaction.followup.send(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        embed = discord.Embed(
            title=f"📝 Edit Mode: {panel_name}",
            description="Click a button below to edit or delete it.",
            color=discord.Color.orange()
        )
        view = TicketPanelEditView(ctx, panel_data)

        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await ctx.send(embed=embed, view=view)

    except Exception as e:
        print("ticketeditbutton ERROR:", traceback.format_exc())
        if ctx.interaction:
            await ctx.interaction.followup.send(f"❌ Error:\n```{e}```", ephemeral=True)
        else:
            await ctx.send(f"❌ Error:\n```{e}```")

@bot.hybrid_command(name="ticketdeletepanel", description="Delete a saved ticket panel. Staff-only.")
@staffperm("tickets:admin")
@staff_only()
async def ticketdeletepanel(ctx, panel_name: str):
    try:
        result = await ticket_panels_col.delete_one({"guild": str(ctx.guild.id), "panel_name": panel_name})
        if result.deleted_count == 0:
            msg = f"❌ No panel found with name `{panel_name}`."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        msg = f"🗑 Panel `{panel_name}` deleted successfully."
        if ctx.interaction:
            await ctx.interaction.response.send_message(msg, ephemeral=True)
        else:
            await ctx.send(msg)
    except Exception as e:
        print("ticketdeletepanel ERROR:", traceback.format_exc())

@bot.hybrid_command(name="ticketlist", description="List all saved ticket panels. Staff-only.")
@staffperm("tickets:admin")
@staff_only()
async def ticketlist(ctx):
    try:
        panels = await ticket_panels_col.find({"guild": str(ctx.guild.id)}).to_list(length=50)
        if not panels:
            msg = "❌ No saved ticket panels."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        embed = discord.Embed(title="📋 Saved Ticket Panels", color=discord.Color.blurple())
        for panel in panels:
            embed.add_field(
                name=panel["panel_name"],
                value=f"{len(panel.get('buttons', []))} button(s)",
                inline=False
            )

        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
    except Exception as e:
        print("ticketlist ERROR:", traceback.format_exc())

@bot.hybrid_command(name="ticketclose", description="Request to close the current ticket.")
async def ticketclose(ctx):
    async def local_error_handler(func):
        try:
            return await func()
        except Exception as e:
            embed = discord.Embed(
                title="⚠️ Error",
                description=f"An unexpected error occurred:\n```{str(e)}```",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    async def inner():
        channel = ctx.channel
        ticket_entry = await tickets_col.find_one(
            {"guild": str(ctx.guild.id), "channel_id": str(channel.id)}
        )
        if not ticket_entry:
            return await ctx.send("❌ This command can only be used inside a ticket channel.")

        opener = channel.guild.get_member(int(ticket_entry.get("owner_id"))) if ticket_entry.get("owner_id") else None
        if not opener:
            return await ctx.send("⚠️ Could not find the ticket opener.")

        await tickets_col.update_one(
            {"_id": ticket_entry["_id"]},
            {"$set": {"close_pending": True}}
        )

        await ctx.send(
            f"{opener.mention}, do you confirm closing this ticket? "
            "Type `confirm` to close or `cancel` to keep it open. "
            "(This will wait until you reply, no time limit.)"
        )

    await local_error_handler(inner)

@bot.hybrid_command(name="ticketforceclose", description="Force close the current ticket.")
@staff_only()
async def ticketforceclose(ctx):
    async def local_error_handler(func):
        try:
            return await func()
        except Exception as e:
            embed = discord.Embed(
                title="⚠️ Error",
                description=f"An unexpected error occurred:\n```{str(e)}```",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    async def inner():
        channel = ctx.channel
        ticket_entry = await tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})
        if not ticket_entry:
            return await ctx.send("❌ This command can only be used inside a ticket channel.")

        opener = channel.guild.get_member(int(ticket_entry.get("owner_id"))) if ticket_entry.get("owner_id") else None

        await actually_close_ticket(ctx, opener, forced=True)
        await tickets_col.delete_one({"_id": ticket_entry["_id"]})

    await local_error_handler(inner)

@bot.hybrid_command(name="transcript", description="Fetch a ticket transcript. Staff-only.")
@staffperm("tickets:admin")
@staff_only()
async def transcript(ctx, ticket_id: str):
    try:
        ticket = await tickets_col.find_one({"ticket_id": ticket_id, "guild_id": str(ctx.guild.id)})
        if not ticket:
            msg = "❌ No ticket found with that ID."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        def format_time(dt, style="both"):
            if not dt:
                return "Unknown"

            if isinstance(dt, str):
                try:
                    dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                except ValueError:
                    return str(dt)

            if not isinstance(dt, datetime):
                return "Unknown"

            ts = int(dt.timestamp())
            if style == "full":
                return f"<t:{ts}:F>"
            elif style == "short":
                return f"<t:{ts}:f>"
            elif style == "relative":
                return f"<t:{ts}:R>"
            else:
                return f"<t:{ts}:f> • <t:{ts}:R>"

        embed = Embed(
            title=f"🎟 Transcript for {ticket_id}",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Opened by", value=f"<@{ticket['opener_id']}>", inline=True)
        embed.add_field(name="Closed by", value=f"<@{ticket['closer_id']}>", inline=True)
        embed.add_field(name="Opened at", value=format_time(ticket.get('created_at')), inline=True)
        embed.add_field(name="Closed at", value=format_time(ticket.get('closed_at')), inline=True)

        transcript_file = io.StringIO(ticket['transcript'])
        discord_file = File(fp=transcript_file, filename=f"{ticket_id}_transcript.txt")

        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, file=discord_file, ephemeral=True)
        else:
            await ctx.send(embed=embed, file=discord_file)
    except Exception as e:
        print("transcript ERROR:", traceback.format_exc())

@bot.hybrid_command(name="transcriptsearch", description="Search tickets by username. Staff-only.")
@staffperm("tickets:admin")
@staff_only()
async def transcriptsearch(ctx, username: str):
    try:
        query = {
            "guild_id": str(ctx.guild.id),
            "$or": [
                {"opener_name": {"$regex": username, "$options": "i"}},
                {"closer_name": {"$regex": username, "$options": "i"}}
            ]
        }

        tickets = await tickets_col.find(query).to_list(length=20)
        if not tickets:
            msg = f"❌ No tickets found for username containing `{username}`."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        embed = discord.Embed(
            title=f"🔍 Tickets matching '{username}'",
            color=0x57F287
        )
        for t in tickets:
            embed.add_field(
                name=t['ticket_id'],
                value=f"Opened by: <@{t.get('opener_id', 'unknown')}> | Closed by: <@{t.get('closer_id', 'unknown')}>",
                inline=False
            )

        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
    except Exception as e:
        print("transcriptsearch ERROR:", traceback.format_exc())

class TranscriptPaginationView(discord.ui.View):
    def __init__(self, ctx, tickets, per_page=25):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.tickets = tickets
        self.per_page = per_page
        self.page = 0
        self.max_page = (len(tickets) - 1) // per_page
        self.message = None

    def format_time(self, dt, style="both"):
        """Format datetime (or string) into Discord's timestamp format."""
        if not dt:
            return "Unknown"

        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError:
                return str(dt)

        if not isinstance(dt, datetime):
            return "Unknown"

        ts = int(dt.timestamp())

        if style == "full":
            return f"<t:{ts}:F>"
        elif style == "short":
            return f"<t:{ts}:f>"
        elif style == "relative":
            return f"<t:{ts}:R>"
        else:
            return f"<t:{ts}:f> • <t:{ts}:R>"

    async def format_user(self, user_id):
        """Return a mention if possible, else username or 'Unknown'."""
        if not user_id or user_id == "unknown":
            return "Unknown"

        try:
            user_id = int(user_id)
            user_id_int = int(user_id)
            user = self.ctx.bot.get_user(user_id_int) or await self.ctx.bot.fetch_user(user_id_int)

            return user.mention if user else "Unknown"
        except Exception:
            return "Unknown"

    async def build_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.tickets[start:end]

        embed = discord.Embed(
            title=f"📜 Ticket Overview ({len(self.tickets)} total) - Page {self.page+1}/{self.max_page+1}",
            color=discord.Color.blurple()
        )

        for t in chunk:
            ticket_id = t.get("ticket_id", "Unknown")

            opener = await self.format_user(t.get("opener_id"))
            opened_at = self.format_time(t.get("created_at"), "both")

            status = f"🟢 Ongoing\nOpened by: {opener}\nOpened at: {opened_at}"

            if t.get("closed_at"):
                closer = await self.format_user(t.get("closer_id"))
                closed_at = self.format_time(t.get("closed_at"), "both")

                status = (
                    f"🔴 Closed\n"
                    f"Opened by: {opener}\nOpened at: {opened_at}\n"
                    f"Closed by: {closer}\nClosed at: {closed_at}"
                )

            embed.add_field(
                name=f"🎟 Ticket {ticket_id}",
                value=status,
                inline=False
            )

        return embed

    @discord.ui.button(label="⬅ Prev", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ Only the staff member who ran the command can use this.", ephemeral=True)

        self.page = max(0, self.page - 1)

        self.children[0].disabled = self.page == 0
        self.children[1].disabled = self.page == self.max_page

        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next ➡", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("❌ Only the staff member who ran the command can use this.", ephemeral=True)

        self.page = min(self.max_page, self.page + 1)

        self.children[0].disabled = self.page == 0
        self.children[1].disabled = self.page == self.max_page

        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.delete()
            except discord.NotFound:
                pass

@bot.hybrid_command(name="transcriptlist", description="List all tickets (open & closed) with details. Staff-only.")
@staffperm("tickets:admin")
@staff_only()
async def transcriptlist(ctx):
    try:
        tickets = await tickets_col.find({"guild_id": str(ctx.guild.id)}).to_list(length=200)

        if not tickets:
            msg = "❌ No tickets found in this server."
            if is_prefix(ctx):
                await ctx.send(msg)
            else:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            return

        view = TranscriptPaginationView(ctx, tickets)
        embed = await view.build_embed()

        view.children[0].disabled = True
        view.children[1].disabled = view.max_page == 0

        if is_prefix(ctx):
            msg = await ctx.send(embed=embed, view=view)
        else:
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            msg = await ctx.interaction.original_response()

        view.message = msg

    except Exception as e:
        print("transcriptlist ERROR:", traceback.format_exc())
        if not is_prefix(ctx) and ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.send_message(f"❌ Error:\n```{e}```", ephemeral=True)
        else:
            await ctx.send(f"❌ Error:\n```{e}```")

@bot.hybrid_command(name="ticketadduser", description="Add a user to the current ticket.")
@staffperm("tickets:admin")
@staff_only()
async def ticketadduser(ctx, member: discord.Member):
    channel = ctx.channel
    ticket_entry = await tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})

    if not ticket_entry:
        return await ctx.send("❌ This command can only be used inside a ticket channel.")

    try:
        overwrite = channel.overwrites_for(member)
        overwrite.view_channel = True
        overwrite.send_messages = True
        overwrite.read_message_history = True
        overwrite.embed_links = True
        overwrite.attach_files = True
        await channel.set_permissions(member, overwrite=overwrite)

        await ctx.send(f"✅ {member.mention} has been added to this ticket.")
    except discord.Forbidden:
        await ctx.send("❌ I don’t have permission to edit channel permissions.")
    except Exception as e:
        await ctx.send(f"⚠️ Failed to add user: `{e}`")

@bot.hybrid_command(name="ticketremoveuser", description="Remove a user from the current ticket.")
@staffperm("tickets:admin")
@staff_only()
async def ticketremoveuser(ctx, member: discord.Member):
    channel = ctx.channel
    ticket_entry = await tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})

    if not ticket_entry:
        return await ctx.send("❌ This command can only be used inside a ticket channel.")

    try:
        await channel.set_permissions(member, overwrite=None)
        await ctx.send(f"✅ {member.mention} has been removed from this ticket.")
    except discord.Forbidden:
        await ctx.send("❌ I don’t have permission to edit channel permissions.")
    except Exception as e:
        await ctx.send(f"⚠️ Failed to remove user: `{e}`")

@bot.command()
@staff_only()
async def ticketsync(ctx, scope: str = None):
    try:
        if scope and scope.lower() == "all":
            updated = 0
            docs = await tickets_col.find({"guild": str(ctx.guild.id)}).to_list(length=200)
            for t in docs:
                channel_id = int(t.get("channel_id")) if t.get("channel_id") else None
                category_name = t.get("category")
                if not channel_id or not category_name:
                    continue
                channel = ctx.guild.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue
                desired = []
                for m in await get_category_support_members(ctx.guild, category_name):
                    if await has_staff_role(m, ctx.guild):
                        desired.append(m)
                desired_ids = {m.id for m in desired}
                for target, overwrite in channel.overwrites.items():
                    if isinstance(target, discord.Member):
                        if await has_staff_role(target, ctx.guild):
                            if overwrite.view_channel and target.id not in desired_ids:
                                await channel.set_permissions(target, overwrite=None)
                for m in desired:
                    ow = channel.overwrites_for(m)
                    ow.view_channel = True
                    ow.send_messages = True
                    ow.read_message_history = True
                    ow.embed_links = True
                    ow.attach_files = True
                    await channel.set_permissions(m, overwrite=ow)
                updated += 1
            return await ctx.send(f"✅ Synced staff access for `{updated}` open tickets.")

        channel = ctx.channel
        t = await tickets_col.find_one({"guild": str(ctx.guild.id), "channel_id": str(channel.id)})
        if not t:
            return await ctx.send("❌ This command can only be used inside an open ticket channel, or use `.ticketsync all`.")
        category_name = t.get("category")
        if not category_name:
            return await ctx.send("⚠️ Could not determine ticket category for this channel.")
        desired = []
        for m in await get_category_support_members(ctx.guild, category_name):
            if await has_staff_role(m, ctx.guild):
                desired.append(m)
        desired_ids = {m.id for m in desired}
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member):
                if await has_staff_role(target, ctx.guild):
                    if overwrite.view_channel and target.id not in desired_ids:
                        await channel.set_permissions(target, overwrite=None)
        for m in desired:
            ow = channel.overwrites_for(m)
            ow.view_channel = True
            ow.send_messages = True
            ow.read_message_history = True
            ow.embed_links = True
            ow.attach_files = True
            await channel.set_permissions(m, overwrite=ow)
        await ctx.send("✅ Staff access synced for this ticket.")
    except discord.Forbidden:
        await ctx.send("❌ I don’t have permission to edit channel permissions.")
    except Exception as e:
        await ctx.send(f"⚠️ Error: `{e}`")

class GiveawayView(discord.ui.View):
    def __init__(self, embed_message, giveaway_id, end_time, winners, prize):
        super().__init__(timeout=None)
        self.participants = defaultdict(int)
        self.embed_message = embed_message
        self.giveaway_id = giveaway_id
        self.end_time = end_time
        self.winners_count = winners
        self.prize = prize

    async def update_db(self):
        await giveaway_col.update_one(
            {"_id": self.giveaway_id},
            {"$set": {"participants": {str(uid): entries for uid, entries in self.participants.items()}}}
        )

    @discord.ui.button(label="🎉 Entry", style=discord.ButtonStyle.blurple)
    async def entry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.participants:
            del self.participants[interaction.user.id]
            await interaction.response.send_message("❌ You left the giveaway.", ephemeral=True)
        else:
            entries = 1

            data = await giveaway_col.find_one({"_id": self.giveaway_id})
            bonus_roles = data.get("bonus_roles", {})

            for role_id, bonus in bonus_roles.items():
                role = interaction.guild.get_role(role_id)
                if role and role in interaction.user.roles:
                    entries += bonus

            self.participants[interaction.user.id] = entries
            await interaction.response.send_message(f"✅ You joined the giveaway with **{entries} ticket(s)**!", ephemeral=True)

        await self.update_db()

        embed = self.embed_message.embeds[0]
        for idx, field in enumerate(embed.fields):
            if field.name == "Participants":
                embed.set_field_at(idx, name="Participants", value=str(len(self.participants)), inline=False)
                break
        await self.embed_message.edit(embed=embed)

    @discord.ui.button(label="👥 Participants", style=discord.ButtonStyle.gray)
    async def participants_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.participants:
            await interaction.response.send_message("👀 Nobody yet! Be the first to join!", ephemeral=True)
            return

        names = []
        for uid, entries in self.participants.items():
            user = interaction.client.get_user(uid) or await interaction.client.fetch_user(uid)
            if user:
                names.append(f"{user} - {entries} ticket(s)")
        names_str = "\n".join(names)
        if len(names) > 1900:
            file = discord.File(io.BytesIO(names.encode()), filename="participants.txt")
            await interaction.response.send_message("📄 Too many participants to display! Here's the list:", file=file, ephemeral=True)
        else:
            await interaction.response.send_message(f"**Participants:**\n{names}", ephemeral=True)

    async def end_giveaway(self):
        embed = self.embed_message.embeds[0]
        for idx, field in enumerate(embed.fields):
            if field.name == "Ends":
                embed.set_field_at(idx, name="Ended", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=False)
                break
        await self.embed_message.edit(embed=embed, view=None)

        channel = self.embed_message.channel

        if isinstance(self.participants, list):
            self.participants = {}

        if not self.participants:
            await channel.send(f"😔 No one joined the giveaway for **{self.prize}**. No winners this time!")
            await giveaway_col.update_one({"_id": self.giveaway_id}, {"$set": {"ended": True, "winners": []}})
            return

        ticket_pool = []
        for uid, entries in self.participants.items():
            ticket_pool.extend([uid] * entries)

        unique_winners = []
        while len(unique_winners) < self.winners_count and ticket_pool:
            pick = random.choice(ticket_pool)
            if pick not in unique_winners:
                unique_winners.append(pick)
        winners_ids = unique_winners

        winners_mentions = ", ".join(f"<@{uid}>" for uid in winners_ids)
        await channel.send(f"🎉 Congratulations {winners_mentions}! You won **{self.prize}**!")

        await giveaway_col.update_one(
            {"_id": self.giveaway_id},
            {"$set": {"ended": True, "winners": winners_ids}}
        )

        for idx, field in enumerate(embed.fields):
            if field.name == "Winners":
                embed.set_field_at(idx, name="Winners", value=winners_mentions, inline=False)
                break
        await self.embed_message.edit(embed=embed)

def build_poll_embed(question, options, counts, closed=False, duration=None):
    embed = discord.Embed(
        title="📊 Poll" + (" (Closed)" if closed else ""),
        color=discord.Color.blue()
    )
    embed.description = f"**{question}**"
    for i, opt in enumerate(options, start=1):
        if opt:
            embed.add_field(name=opt, value=f"Votes: {counts.get(str(i), 0)}", inline=False)
    if duration and not closed:
        embed.set_footer(text=f"Poll duration: {duration}")
    return embed

class PollView(discord.ui.View):
    def __init__(self, poll_id, options):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.options = options

        for i, opt in enumerate(options, start=1):
            if opt:
                self.add_item(PollButton(label=opt, option=i, poll_id=poll_id))

        self.add_item(RemoveVoteButton(poll_id=poll_id))

async def on_submit(self, interaction: discord.Interaction):
    try:
        post_channel = interaction.client.get_channel(int(self.channel.value))
        duration_seconds = parse_time(self.duration.value)
        end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

        options = [self.option1.value, self.option2.value, self.option3.value,
                   self.option4.value, self.option5.value]
        poll_id = str(interaction.id)

        counts = {}
        view = PollView(poll_id, options)
        embed = build_poll_embed(self.question.value, options, counts,
                                 closed=False, duration=self.duration.value)

        msg = await post_channel.send(embed=embed, view=view)

        await polls_col.insert_one({
            "poll_id": poll_id,
            "question": self.question.value,
            "options": options,
            "votes": {},
            "channel_id": str(post_channel.id),
            "message_id": str(msg.id),
            "end_time": end_time,
            "duration_raw": self.duration.value
        })

        await interaction.response.send_message("✅ Poll created!", ephemeral=True)

    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)

class PollButton(discord.ui.Button):
    def __init__(self, label, option, poll_id):
        super().__init__(style=discord.ButtonStyle.primary, label=label)
        self.option = option
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction):
        poll = await polls_col.find_one({"poll_id": self.poll_id})
        if not poll:
            return await interaction.response.send_message("⚠️ Poll not found.", ephemeral=True)

        poll["votes"][str(interaction.user.id)] = str(self.option)
        await polls_col.update_one({"poll_id": self.poll_id}, {"$set": {"votes": poll["votes"]}})

        counts = {}
        for v in poll["votes"].values():
            counts[v] = counts.get(v, 0) + 1

        embed = build_poll_embed(poll["question"], poll["options"], counts, closed=False, duration=poll["duration_raw"])
        channel = interaction.client.get_channel(int(poll["channel_id"]))
        message = await channel.fetch_message(int(poll["message_id"]))
        await message.edit(embed=embed, view=self.view)

        await interaction.response.send_message(f"✅ You voted for **{self.label}**", ephemeral=True)

class RemoveVoteButton(discord.ui.Button):
    def __init__(self, poll_id):
        super().__init__(style=discord.ButtonStyle.danger, label="Remove Vote")
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction):
        poll = await polls_col.find_one({"poll_id": self.poll_id})
        if not poll:
            return await interaction.response.send_message("⚠️ Poll not found.", ephemeral=True)

        if str(interaction.user.id) in poll["votes"]:
            del poll["votes"][str(interaction.user.id)]
            await polls_col.update_one({"poll_id": self.poll_id}, {"$set": {"votes": poll["votes"]}})

            counts = {}
            for v in poll["votes"].values():
                counts[v] = counts.get(v, 0) + 1

            embed = build_poll_embed(poll["question"], poll["options"], counts, closed=False, duration=poll["duration_raw"])
            channel = interaction.client.get_channel(int(poll["channel_id"]))
            message = await channel.fetch_message(int(poll["message_id"]))
            await message.edit(embed=embed, view=self.view)

            await interaction.response.send_message("🗑️ Your vote was removed.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ You haven’t voted yet.", ephemeral=True)

class PollModal(discord.ui.Modal, title="Create a Poll"):
    question = discord.ui.TextInput(label="Question?", required=True)
    option1 = discord.ui.TextInput(label="Option 1", required=True)
    option2 = discord.ui.TextInput(label="Option 2", required=True)
    option3 = discord.ui.TextInput(label="Option 3 (optional)", required=False)
    option4 = discord.ui.TextInput(label="Option 4 (optional)", required=False)
    option5 = discord.ui.TextInput(label="Option 5 (optional)", required=False)
    channel = discord.ui.TextInput(label="Channel ID to post in?", required=True)
    duration = discord.ui.TextInput(label="Duration? (e.g. 10m, 2h)", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            post_channel = interaction.client.get_channel(int(self.channel.value))
            duration_seconds = parse_time(self.duration.value)
            end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

            options = [self.option1.value, self.option2.value, self.option3.value, self.option4.value, self.option5.value]
            poll_id = str(interaction.id)

            counts = {}
            view = PollView(poll_id, options, timeout=duration_seconds)
            embed = build_poll_embed(self.question.value, options, counts, closed=False, duration=self.duration.value)

            msg = await post_channel.send(embed=embed, view=view)

            await polls_col.insert_one({
                "poll_id": poll_id,
                "question": self.question.value,
                "options": options,
                "votes": {},
                "channel_id": str(post_channel.id),
                "message_id": str(msg.id),
                "end_time": end_time,
                "duration_raw": self.duration.value
            })

            await interaction.response.send_message("✅ Poll created!", ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)

@bot.hybrid_command(name="poll", description="Create a poll")
async def poll(ctx):
    if isinstance(ctx, discord.Interaction):
        await ctx.response.send_modal(PollModal())
        return

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        await ctx.send("📝 Let's create a poll! Type `cancel` anytime to stop.")

        await ctx.send("❓ What is the question?")
        msg = await bot.wait_for("message", check=check, timeout=300)
        if msg.content.lower() == "cancel":
            return await ctx.send("❌ Poll creation cancelled.")
        question = msg.content

        await ctx.send("➡️ Enter option 1:")
        msg = await bot.wait_for("message", check=check, timeout=300)
        if msg.content.lower() == "cancel":
            return await ctx.send("❌ Poll creation cancelled.")
        option1 = msg.content

        await ctx.send("➡️ Enter option 2:")
        msg = await bot.wait_for("message", check=check, timeout=300)
        if msg.content.lower() == "cancel":
            return await ctx.send("❌ Poll creation cancelled.")
        option2 = msg.content

        options = [option1, option2]
        for i in range(3, 6):
            await ctx.send(f"➡️ Enter option {i} (or type `skip` to leave blank):")
            msg = await bot.wait_for("message", check=check, timeout=300)
            if msg.content.lower() == "cancel":
                return await ctx.send("❌ Poll creation cancelled.")
            if msg.content.lower() == "skip":
                options.append(None)
                continue
            options.append(msg.content)

        await ctx.send("📺 Mention the channel to post in (e.g., #general):")
        msg = await bot.wait_for("message", check=check, timeout=300)
        if msg.content.lower() == "cancel":
            return await ctx.send("❌ Poll creation cancelled.")
        if not msg.channel_mentions:
            return await ctx.send("⚠️ Invalid channel mention. Cancelled.")
        channel = msg.channel_mentions[0]

        await ctx.send("⏳ Enter poll duration (e.g., `1h`, `30m`, `2d`):")
        msg = await bot.wait_for("message", check=check, timeout=300)
        if msg.content.lower() == "cancel":
            return await ctx.send("❌ Poll creation cancelled.")
        try:
            duration_seconds = parse_time(msg.content)
        except Exception as e:
            return await ctx.send(f"⚠️ Invalid duration format. {e}")

        end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

        final_options = [opt for opt in options if opt]

        poll_id = f"{ctx.guild.id}-{ctx.message.id}"
        counts = {}
        view = PollView(poll_id, final_options)
        embed = build_poll_embed(question, final_options, counts,
                                 closed=False, duration=msg.content)

        poll_msg = await channel.send(embed=embed, view=view)

        await polls_col.insert_one({
            "poll_id": poll_id,
            "question": question,
            "options": final_options,
            "votes": {},
            "channel_id": str(channel.id),
            "message_id": str(poll_msg.id),
            "end_time": end_time,
            "duration_raw": msg.content
        })

        await ctx.send("✅ Poll created successfully!")

    except asyncio.TimeoutError:
        await ctx.send("⌛ Poll creation timed out due to inactivity.")
    except Exception as e:
        await ctx.send(f"⚠️ Error: {e}")

@tasks.loop(seconds=0.01)
async def check_polls():
    now = datetime.now(timezone.utc)
    async for poll in polls_col.find({"end_time": {"$lte": now}}):
        channel = bot.get_channel(int(poll["channel_id"]))
        if not channel:
            continue
        try:
            msg = await channel.fetch_message(int(poll["message_id"]))
            counts = {}
            for v in poll["votes"].values():
                counts[v] = counts.get(v, 0) + 1
            closed_embed = build_poll_embed(poll["question"], poll["options"], counts, closed=True)
            await msg.edit(embed=closed_embed, view=None)
            await channel.send("⏰ Poll closed!", reference=msg)
        except Exception as e:
            print(f"Error closing poll {poll['poll_id']}: {e}")

        await polls_col.delete_one({"poll_id": poll["poll_id"]})

class GiveawayModal(discord.ui.Modal, title="Create Giveaway"):
    prize = discord.ui.TextInput(
        label="Prize",
        placeholder="What’s the giveaway prize?",
        required=True
    )
    winners = discord.ui.TextInput(
        label="Number of winners",
        placeholder="Example: 3",
        required=True
    )
    duration = discord.ui.TextInput(
        label="Duration",
        placeholder="e.g. 30s, 15m, 2h, 3d, 1w, 2mo, 1y, or combinations like 1d12h",
        required=True
    )
    role_requirements = discord.ui.TextInput(
        label="Role Requirements (optional)",
        placeholder="Role IDs separated by commas",
        required=False
    )
    bonus_roles = discord.ui.TextInput(
        label="Bonus Roles (optional)",
        placeholder="Format: role_id|bonus, role_id|bonus",
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            winners_count = int(self.winners.value)
            if winners_count < 1:
                raise ValueError("Number of winners must be at least 1.")

            duration_seconds = parse_time(self.duration.value)

        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

        embed = discord.Embed(title=self.prize.value, color=discord.Color.blue())
        embed.add_field(name="Hosted by", value=interaction.user.mention, inline=False)
        embed.add_field(name="Ends", value=f"<t:{int(end_time.timestamp())}:F> (<t:{int(end_time.timestamp())}:R>)", inline=False)
        embed.add_field(name="Winners", value=str(winners_count), inline=False)
        embed.add_field(name="Participants", value="0", inline=False)

        if self.role_requirements.value:
            embed.add_field(name="Requirements", value=f"Roles required (one of): {self.role_requirements.value}", inline=False)

        if self.bonus_roles.value:
            lines = []
            for entry in self.bonus_roles.value.split(","):
                try:
                    role, bonus = entry.strip().split("|")
                    lines.append(f"{role.strip()} • {bonus.strip()} bonus entries")
                except ValueError:
                    continue
            if lines:
                embed.add_field(name="Roles with bonus entries", value="\n".join(lines), inline=False)

        sent_message = await interaction.channel.send(embed=embed)

        bonus_roles_dict = {}
        if self.bonus_roles.value:
            for entry in self.bonus_roles.value.split(","):
                try:
                    role, bonus = entry.strip().split("|")
                    role_id = int(role.strip().replace("<@&", "").replace(">", ""))
                    bonus_roles_dict[role_id] = int(bonus.strip())
                except ValueError:
                    continue

        giveaway_id = str(sent_message.id)
        giveaway_data = {
            "_id": giveaway_id,
            "channel_id": interaction.channel.id,
            "message_id": sent_message.id,
            "prize": self.prize.value,
            "host_id": interaction.user.id,
            "end_time": end_time.isoformat(),
            "winners_count": winners_count,
            "participants": {},
            "ended": False,
            "bonus_roles": bonus_roles_dict
        }
        giveaway_col.insert_one(giveaway_data)

        view = GiveawayView(embed_message=sent_message, giveaway_id=giveaway_id, end_time=end_time, winners=winners_count, prize=self.prize.value)
        await sent_message.edit(view=view)

        await interaction.response.send_message("✅ Giveaway created!", ephemeral=True)

        asyncio.create_task(end_after_delay(view, duration_seconds))

@bot.hybrid_command(name="giveaway", description="Create a giveaway using a form. Staff-only.")
@staffperm("giveaways")
@staff_only()
async def giveaway(ctx: commands.Context):
    await ctx.interaction.response.send_modal(GiveawayModal())

@bot.hybrid_command(name="reroll", description="Pick new winners for a past giveaway.")
@staffperm("giveaways")
@staff_only()
async def reroll(ctx: commands.Context, message_id: int):
    data = await giveaway_col.find_one({"message_id": message_id, "ended": True})

    if not data:
        await ctx.send("❌ Giveaway not found or hasn't ended yet.", ephemeral=True)
        return

    participants = data.get("participants", {})

    if isinstance(participants, list):
        participants = {}

    if not participants:
        await ctx.send("😔 No participants in that giveaway.", ephemeral=True)
        return

    ticket_pool = []
    for uid, entries in participants.items():
        ticket_pool.extend([int(uid)] * entries)

    if not ticket_pool:
        await ctx.send("😔 No valid tickets found.", ephemeral=True)
        return

    winners = []
    while len(winners) < data["winners_count"] and ticket_pool:
        pick = random.choice(ticket_pool)
        if pick not in winners:
            winners.append(pick)

    winners_mentions = ", ".join(f"<@{wid}>" for wid in winners)

    await giveaway_col.update_one({"_id": data["_id"]}, {"$set": {"winners": winners}})

    try:
        channel = ctx.bot.get_channel(data["channel_id"])
        message = await channel.fetch_message(data["message_id"])
        embed = message.embeds[0]

        for idx, field in enumerate(embed.fields):
            if field.name == "Winners":
                embed.set_field_at(idx, name="Winners", value=winners_mentions, inline=False)
                break

        await message.edit(embed=embed)
        await channel.send(f"🔄 **Reroll!** New winners for **{data['prize']}**: {winners_mentions}")

    except Exception as e:
        await ctx.send(f"⚠️ Winners updated in database, but failed to update message: {e}", ephemeral=True)
        return

    await ctx.send("✅ Reroll complete and announced in the giveaway's channel.", ephemeral=True)

@bot.hybrid_command(name="draw", description="Instantly draw winners from a giveaway using its message ID. Staff-only.")
@staffperm("giveaways")
@staff_only()
async def draw(ctx: commands.Context, message_id: int):
    data = await giveaway_col.find_one({"message_id": message_id})
    if not data:
        await ctx.send("❌ Giveaway not found.", ephemeral=True)
        return

    participants = data.get("participants", {})

    if isinstance(participants, list):
        participants = {}

    if not participants:
        await ctx.send("😔 No participants in that giveaway.", ephemeral=True)
        return

    ticket_pool = []
    for uid, entries in participants.items():
        ticket_pool.extend([int(uid)] * entries)

    if not ticket_pool:
        await ctx.send("😔 No valid tickets found.", ephemeral=True)
        return

    winners = []
    while len(winners) < data["winners_count"] and ticket_pool:
        pick = random.choice(ticket_pool)
        if pick not in winners:
            winners.append(pick)

    winners_mentions = ", ".join(f"<@{wid}>" for wid in winners)

    await giveaway_col.update_one(
        {"_id": data["_id"]},
        {"$set": {"winners": winners, "ended": True}}
    )

    try:
        channel = ctx.bot.get_channel(data["channel_id"])
        message = await channel.fetch_message(data["message_id"])
        embed = message.embeds[0]

        for idx, field in enumerate(embed.fields):
            if field.name == "Winners":
                embed.set_field_at(idx, name="Winners", value=winners_mentions, inline=False)
                break

        await message.edit(embed=embed)
        await channel.send(f"🎉 **Giveaway Ended!** Winners for **{data['prize']}**: {winners_mentions}")

    except Exception as e:
        await ctx.send(f"⚠️ Winners drawn but failed to update message: {e}", ephemeral=True)
        return

    await ctx.send("✅ Winners drawn and giveaway ended.", ephemeral=True)

async def resume_giveaways(bot):
    now = datetime.now(timezone.utc)
    active_giveaways = giveaway_col.find({"ended": False})

    async for data in active_giveaways:
        try:
            end_time = datetime.fromisoformat(data["end_time"])
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            remaining = (end_time - now).total_seconds()
            channel = bot.get_channel(data["channel_id"])
            if not channel:
                print(f"[Giveaway Resume] Channel {data['channel_id']} not found for giveaway {data['_id']}")
                continue

            message = await channel.fetch_message(data["message_id"])

            view = GiveawayView(
                embed_message=message,
                giveaway_id=data["_id"],
                end_time=end_time,
                winners=data["winners_count"],
                prize=data["prize"]
            )

            participants = data.get("participants", {})
            if isinstance(participants, list):
                participants = {}
            for uid, entries in participants.items():
                try:
                    view.participants[int(uid)] = entries
                except Exception as e:
                    print(f"[Giveaway Resume] Could not fetch participant {uid} for giveaway {data['_id']}: {e}")

            await message.edit(view=view)

            if remaining <= 0:
                print(f"[Giveaway Resume] Giveaway {data['_id']} expired while offline. Ending now.")
                await view.end_giveaway()
            else:
                asyncio.create_task(end_after_delay(view, remaining))

        except Exception as e:
            print(f"[Giveaway Resume] Failed to resume giveaway {data['_id']}: {e}")

async def end_after_delay(view: GiveawayView, delay: float):
    await asyncio.sleep(max(0, delay))
    await view.end_giveaway()

@giveaway.error
async def giveaway_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to create giveaways.", ephemeral=True)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid input. Please check your command format.", ephemeral=True)
    else:
        traceback.print_exc()
        await ctx.send("⚠️ An unexpected error occurred while processing the giveaway.", ephemeral=True)

@reroll.error
async def reroll_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to reroll giveaways.", ephemeral=True)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid message ID.", ephemeral=True)
    else:
        traceback.print_exc()
        await ctx.send("⚠️ An unexpected error occurred while rerolling.", ephemeral=True)

async def setup(bot):
    bot.add_command(giveaway)
    bot.add_command(reroll)
    bot.loop.create_task(resume_giveaways(bot))


class RoleButtons(View):
    def __init__(self, roles, guild_id, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        for role_id in roles:
            role = guild.get_role(role_id)
            if not role:
                continue

            role_button = Button(
                label=role.name,
                style=discord.ButtonStyle.primary,
                custom_id=f"claim_{role_id}"
            )
            role_button.callback = self.make_callback(role_id)
            self.add_item(role_button)

    def make_callback(self, role_id):
        async def callback(interaction: discord.Interaction):
            role = interaction.guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message("❌ Role not found.", ephemeral=True)

            await interaction.response.defer(ephemeral=True)

            try:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role)
                    msg = f"❌ Removed {role.mention}"
                else:
                    await interaction.user.add_roles(role)
                    msg = f"✅ You claimed {role.mention}"

                await interaction.followup.send(msg, ephemeral=True)

            except discord.Forbidden:
                await interaction.followup.send("⚠️ I don't have permission to manage roles.", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ Something went wrong: `{e}`", ephemeral=True)

        return callback

async def refresh_roles_embed(ctx, guild_id):
    settings = await roles_col.find_one({"_id": guild_id})
    if not settings:
        return

    role_ids = settings.get("roles", [])
    message_id = settings.get("message_id")

    if not message_id:
        return

    try:
        msg = await ctx.channel.fetch_message(message_id)
    except discord.NotFound:
        return

    roles = [ctx.guild.get_role(r) for r in role_ids if ctx.guild.get_role(r)]
    if not roles:
        embed = discord.Embed(
            title="🎭 Claim Your Roles",
            description="⚠️ No valid roles available.",
            color=discord.Color.red()
        )
        await msg.edit(embed=embed, view=None)
        return

    embed = discord.Embed(
        title="🎭 Claim Your Roles",
        description="\n".join([role.mention for role in roles]),
        color=discord.Color.blurple()
    )
    view = RoleButtons(role_ids, guild_id, ctx.guild)
    await msg.edit(embed=embed, view=view)

@bot.hybrid_command(name="roles", description="Show claimable roles")
async def roles(ctx: commands.Context):
    guild_id = ctx.guild.id
    settings = await roles_col.find_one({"_id": guild_id})

    if not settings or not settings.get("roles"):
        return await ctx.send("⚠️ No claimable roles set yet!", ephemeral=True)

    role_ids = settings["roles"]
    roles = [ctx.guild.get_role(r) for r in role_ids if ctx.guild.get_role(r)]

    if not roles:
        return await ctx.send("⚠️ All stored roles are invalid.", ephemeral=True)

    embed = discord.Embed(
        title="🎭 Claim Your Roles",
        description="\n".join([role.mention for role in roles]),
        color=discord.Color.blurple()
    )

    view = RoleButtons(role_ids, guild_id, ctx.guild)
    msg = await ctx.send(embed=embed, view=view)

    await roles_col.update_one(
        {"_id": guild_id},
        {"$set": {"message_id": msg.id}},
        upsert=True
    )

@bot.hybrid_command(name="roleadd", description="Add a claimable role")
@staffperm("roles")
@staff_only()
async def roleadd(ctx: commands.Context, role: discord.Role):
    guild_id = ctx.guild.id
    settings = await roles_col.find_one({"_id": guild_id})

    if settings:
        if role.id in settings["roles"]:
            return await ctx.send("⚠️ That role is already claimable.", ephemeral=True)

        await roles_col.update_one(
            {"_id": guild_id},
            {"$push": {"roles": role.id}}
        )
    else:
        await roles_col.insert_one({"_id": guild_id, "roles": [role.id]})

    await ctx.send(f"✅ {role.mention} has been added as a claimable role!", ephemeral=True)

    await refresh_roles_embed(ctx, guild_id)

@bot.hybrid_command(name="roleremove", description="Remove a claimable role")
@staffperm("roles")
@staff_only()
async def roleremove(ctx: commands.Context, role: discord.Role):
    guild_id = ctx.guild.id
    settings = await roles_col.find_one({"_id": guild_id})

    if not settings or role.id not in settings.get("roles", []):
        return await ctx.send("⚠️ That role is not claimable.", ephemeral=True)

    await roles_col.update_one(
        {"_id": guild_id},
        {"$pull": {"roles": role.id}}
    )

    await ctx.send(f"❌ {role.mention} has been removed from claimable roles.", ephemeral=True)

    await refresh_roles_embed(ctx, guild_id)

@bot.hybrid_command(name="addmoney", description="Add money to a user (economy admin only).")
@app_commands.describe(amount="Amount to add (supports k, m, b suffixes)", user="User to give money to")
@staffperm("economy")
async def addmoney(ctx, amount: str, user: discord.Member):
    authorized_ids = [1059882387590365314, 903123014420406302, 447235867485143057, ctx.guild.owner_id]
    if ctx.author.id not in authorized_ids:
        return await ctx.send("❌ You are not authorized to use this command.")

    try:
        coins = parse_amount(amount)
        if coins is None or coins <= 0:
            raise ValueError
    except Exception:
        return await ctx.send(
            "❌ Invalid amount.\n"
            "Use formats like: `100`, `4k`, `2m`, `1.5mil`, `123,456`"
        )

    try:
        uid = user.id
        member = user
    except Exception:
        return await ctx.send("❌ Invalid user specified.")

    user_data = await get_user(ctx, ctx.guild.id, uid)
    new_bank = user_data.get("bank", 0) + coins

    await economy_col.update_one(
        {"_id": f"{ctx.guild.id}-{uid}"},
        {"$set": {"bank": new_bank}}
    )

    await log_action(
        ctx,
        f"Added 🪙 {coins:,} to {member.mention}'s bank.",
        user_id=uid,
        action_type="AddMoney"
    )

    await ctx.send(f"✅ Added 🪙 {coins:,} to {member.mention} (New bank: {new_bank:,})")

@addmoney.error
async def addmoney_error(ctx, error):
    try:
        prefix = await get_prefix(bot, ctx.message)
        if isinstance(error, commands.BadArgument):
            return await ctx.send(
                f"❌ Invalid arguments. Usage: `{prefix}addmoney <amount> @user`\n"
                f"Example: `{prefix}addmoney 100 @User`"
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(
                f"❌ Missing arguments. Usage: `{prefix}addmoney <amount> @user`\n"
                f"Example: `{prefix}addmoney 100 @User`"
            )
        else:
            return await ctx.send(f"⚠️ Error running addmoney: `{type(error).__name__}: {error}`")
    except Exception:
        pass

@bot.hybrid_command(name="removemoney", description="Remove money from a user (economy admin only).")
@app_commands.describe(amount="Amount to remove (supports k, m, b suffixes)", user="User to take money from")
@staffperm("economy")
async def removemoney(ctx, amount: str, user: discord.Member):
    authorized_ids = [1059882387590365314, 903123014420406302, 447235867485143057, ctx.guild.owner_id]
    if ctx.author.id not in authorized_ids:
        return await ctx.send("❌ You are not authorized to use this command.")

    try:
        coins = parse_amount(amount)
        if coins is None or coins <= 0:
            raise ValueError
    except Exception:
        return await ctx.send(
            "❌ Invalid amount.\n"
            "Use formats like: `100`, `4k`, `2m`, `1.5mil`, `123,456`"
        )

    try:
        uid = user.id
        member = user
    except Exception:
        return await ctx.send("❌ Invalid user specified.")

    user_data = await get_user(ctx, ctx.guild.id, uid)
    wallet = user_data.get("wallet", 0)
    bank = user_data.get("bank", 0)
    total = wallet + bank

    if total < coins:
        return await ctx.send(f"❌ {member.mention} does not have enough funds.")

    if wallet >= coins:
        new_wallet = wallet - coins
        new_bank = bank
    else:
        new_wallet = 0
        new_bank = bank - (coins - wallet)

    await economy_col.update_one(
        {"_id": f"{ctx.guild.id}-{uid}"},
        {"$set": {"wallet": new_wallet, "bank": new_bank}}
    )

    await log_action(
        ctx,
        f"Removed 🪙 {coins:,} from {member.mention}'s balance.",
        user_id=uid,
        action_type="RemoveMoney"
    )

    await ctx.send(f"✅ Removed 🪙 {coins:,} from {member.mention} — Wallet: {new_wallet:,} | Bank: {new_bank:,}")

@bot.hybrid_command(name="drop", description="Create a money drop (staff spawns money, members pay).")
@app_commands.describe(amount="Amount to drop", message="Optional message to include")
async def drop(ctx, amount: str, *, message: str = None):
    if not ctx.guild:
        return await ctx.send("❌ This command can only be used in a server.")

    guild_id = ctx.guild.id
    user_id = ctx.author.id

    try:
        coins = parse_amount(amount)
        if coins is None or coins <= 0:
            raise ValueError
    except Exception:
        return await ctx.send(
            "❌ Invalid amount.\n"
            "Use formats like: `100`, `4k`, `2m`, `1.5mil`"
        )

    is_staff = False
    try:
        is_staff = await staffperm("money_drop").predicate(ctx)
    except Exception:
        is_staff = False
    
    if not is_staff:
        ok = await check_channel(ctx, "DROP_CHANNELS", "Drop")
        if not ok:
            return

    if not is_staff:
        try:
            data = await get_user(ctx, guild_id, user_id)
            wallet = int(data.get("wallet", 0))
            bank = int(data.get("bank", 0))
            if bank >= coins:
                new_bank = bank - coins
                new_wallet = wallet
            elif bank + wallet >= coins:
                take_from_wallet = coins - bank
                new_bank = 0
                new_wallet = wallet - take_from_wallet
            else:
                total = wallet + bank
                return await ctx.send(
                    f"❌ You don’t have enough money.\n"
                    f"🏦 Bank: **{bank:,}** | 🪙 Wallet: **{wallet:,}**\n"
                    f"🪙 Required: **{coins:,}** (Total: {total:,})"
                )
            await economy_col.update_one(
                {"_id": f"{guild_id}-{user_id}"},
                {"$set": {"wallet": new_wallet, "bank": new_bank}},
                upsert=True
            )

        except Exception as e:
            return await ctx.send(
                "⚠️ Failed to process your balance.\n"
                "Please try again later."
            )

    role_id = None
    if is_staff:
        try:
            settings = await drops_col.find_one({"_id": guild_id})
            role_id = settings.get("role_id") if settings else None
        except Exception:
            role_id = None

    try:
        await ctx.message.delete()
    except Exception:
        pass

    embed = discord.Embed(
        title="💰 Money Drop!",
        description=f"Someone dropped **🪙 {coins:,}**!\n\nClick the button below to claim it!",
        color=discord.Color.gold()
    )

    if message:
        embed.add_field(name="💬 Message", value=message, inline=False)

    embed.set_footer(
        text=f"Dropped by {ctx.author}",
        icon_url=ctx.author.display_avatar.url
    )

    view = DropClaimView()
    role_ping = f"<@&{role_id}>" if (is_staff and role_id) else ""

    try:
        msg = await ctx.send(
            content=role_ping,
            embed=embed,
            view=view
        )
    except Exception:
        if not is_staff:
            await economy_col.update_one(
                {"_id": user_id},
                {"$inc": {"balance": coins}},
                upsert=True
            )

        return await ctx.send("❌ Failed to send the drop message. You have been refunded.")

    try:
        await drop_instances_col.update_one(
            {"message_id": str(msg.id)},
            {"$set": {
                "message_id": str(msg.id),
                "channel_id": str(ctx.channel.id),
                 "guild_id": str(guild_id),
                "amount": int(coins),
                "author_id": str(user_id),
                "claimed": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "staff_drop": is_staff
            }},
            upsert=True
        )
    except Exception:
        pass

@drop.error
async def drop_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            "❌ Missing arguments!\n"
            "**Usage:** `.drop <amount> [message]`\n"
            "**Example:** `.drop 5000 Enjoy the coins!`"
        )

    elif isinstance(error, commands.BadArgument):
        await ctx.send(
            "❌ Invalid argument.\n"
            "Use formats like: `100`, `4k`, `2m`, `1.5mil`"
        )

    elif isinstance(error, commands.CommandInvokeError):
        await ctx.send(
            "⚠️ Something went wrong while running this command.\n"
            "Please try again later."
        )

    else:
        await ctx.send(
            "⚠️ An unexpected error occurred.\n"
            "Please contact an administrator."
        )

@bot.command(name="kick", description="Kick a member. Staff-only.")
@staffperm("kick")
@staff_only()
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    err = check_target_permission(ctx, member)
    if err: return await ctx.send(err)
    
    embed = discord.Embed(
        title="⚠️ Confirm Kick",
        description=f"Are you sure you want to kick {member.mention}?",
        color=discord.Color.orange()
    )
    
    embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="This action will be logged.")
    
    confirm_view = ModerationConfirmView("kick", member, reason, ctx=ctx)
    await ctx.send(embed=embed, view=confirm_view)

@bot.command(name="ban", description="Ban a member. Staff-only.")
@staffperm("ban")
@staff_only()
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    err = check_target_permission(ctx, member)
    if err: return await ctx.send(err)
    
    embed = discord.Embed(
        title="⚠️ Confirm Ban",
        description=f"Are you sure you want to ban {member.mention}?",
        color=discord.Color.orange()
    )
    
    embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="This action will be logged.")
    
    confirm_view = ModerationConfirmView("ban", member, reason, ctx=ctx)
    await ctx.send(embed=embed, view=confirm_view)

@bot.hybrid_command(name="unban", description="Unban a member. Staff-only.")
@staffperm("ban")
@staff_only()
async def unban(ctx, *, user_id: int):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user)
        await ctx.send(f"✅ {user.mention} has been unbanned.")
        await log_action(ctx, f"Unbanned {user}", user_id=user.id, action_type="unban")
    except Exception as e:
        await ctx.send("❌ Failed to unban that user.")

@bot.hybrid_command(name="say", description="Make the bot say a message in a chosen channel.")
@staff_only()
@blacklist_barrier()
async def say(ctx):
    try:
        await ctx.send("📝 Type the message you want me to say, or type `cancel` to cancel.")
        def msg_check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id
        try:
            msg = await bot.wait_for("message", timeout=60.0, check=msg_check)
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Timed out waiting for the message.")
        content = msg.content.strip()
        if content.lower() == "cancel":
            return await ctx.send("❎ Cancelled.")
        if not content:
            return await ctx.send("❌ Message cannot be empty.")
        if len(content) > 2000:
            return await ctx.send("❌ Message is too long. Please keep it under 2000 characters.")
        await ctx.send("📨 Mention the channel (e.g. #general). Type `cancel` to abort.")
        try:
            ch_msg = await bot.wait_for("message", timeout=60.0, check=msg_check)
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Timed out waiting for the channel.")
        ch_text = ch_msg.content.strip()
        if ch_text.lower() == "cancel":
            return await ctx.send("❎ Cancelled.")
        target = None
        if ch_msg.channel_mentions:
            target = ch_msg.channel_mentions[0]
        else:
            try:
                ch_id = int(ch_text)
                target = ctx.guild.get_channel(ch_id)
            except Exception:
                target = None
        if not isinstance(target, discord.TextChannel):
            return await ctx.send("❌ Invalid channel. Mention a text channel or provide a valid channel ID.")
        try:
            await target.send(content)
        except discord.Forbidden:
            return await ctx.send("❌ I do not have permission to send messages in that channel.")
        except discord.HTTPException as e:
            return await ctx.send(f"⚠️ Failed to send the message: {type(e).__name__}")
        await ctx.send(f"✅ Sent your message to {target.mention}.")
    except Exception as e:
        await ctx.send(f"⚠️ An unexpected error occurred: {type(e).__name__}")

@say.error
async def say_error(ctx, error):
    try:
        if isinstance(error, commands.CheckFailure):
            return await ctx.send("❌ Only staff members can use this command.")
        if isinstance(error, commands.CommandInvokeError):
            return await ctx.send(f"⚠️ Error running say: {type(error.__cause__).__name__ if hasattr(error, '__cause__') and error.__cause__ else type(error).__name__}")
        await ctx.send(f"⚠️ Error: {type(error).__name__}")
    except Exception:
        pass

@bot.command(name="mute", description="Mute a member temporarily. Staff-only.")
@staffperm("mute")
@staff_only()
async def mute(ctx, member: discord.Member, duration: str = None, *, reason: str = "No reason provided"):
    err = check_target_permission(ctx, member)
    if err:
        return await ctx.send(err)
    
    embed = discord.Embed(
        title="⚠️ Confirm Mute",
        description=f"Are you sure you want to mute {member.mention}?",
        color=discord.Color.orange()
    )
    
    embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Duration", value=duration or "indefinite", inline=False)
    embed.set_footer(text="This action will be logged.")
    
    confirm_view = ModerationConfirmView("mute", member, reason, duration, ctx=ctx)
    await ctx.send(embed=embed, view=confirm_view)

@bot.hybrid_command(name="unmute", description="Unmute a member. Staff-only.")
@staffperm("mute")
@staff_only()
async def unmute(ctx, member: discord.Member):
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if mute_role and mute_role in member.roles:
        await member.remove_roles(mute_role, reason="Unmute command used")
        await mutes_col.delete_one({"guild_id": ctx.guild.id, "user_id": member.id})
        await ctx.send(f"✅ {member.mention} has been unmuted.")
        await log_action(ctx, f"Unmuted {member}", user_id=member.id, action_type="unmute")
    else:
        await ctx.send("⚠️ That member is not muted.")
        
@bot.hybrid_command(name="warn", description="Warn a user. Staff-only.")
@app_commands.describe(member="The user to warn", reason="Reason for the warning (optional)")
@staffperm("other_moderation")
@staff_only()
async def warn(ctx, member: discord.Member, *, reason="No reason provided"):
    await mod_col.update_one(
        {"guild": str(ctx.guild.id), "user": str(member.id)},
        {"$push": {
            "warnings": {
                "by": str(ctx.author),
                "reason": reason,
                "time": datetime.now(timezone.utc).isoformat()
            }
        }},
        upsert=True
    )

    try:
        await member.send(
            f"⚠️ You have been **warned** in **{ctx.guild.name}**\n"
            f"**Reason:** {reason}\n"
            f"**Warned by:** {ctx.author} ({ctx.author.mention})"
        )
    except discord.Forbidden:
        await ctx.send(f"⚠️ Could not DM {member.mention} - they might have DMs disabled.")

    await ctx.send(f"⚠️ {member.mention} has been warned: {reason}")

    await log_action(ctx, f"Warned {member} for: {reason}", user_id=member.id, action_type="warn")

@bot.hybrid_command(name="clearwarns", description="Clear all warnings. Staff-only.")
@staffperm("other_moderation")
@staff_only()
async def clearwarns(ctx, member: discord.Member):
    await mod_col.update_one({"guild": str(ctx.guild.id), "user": str(member.id)}, {"$set": {"warnings": []}})
    await ctx.send(f"✅ All warnings for {member.mention} have been cleared.")
    await log_action(ctx, f"Cleared warnings for {member}", user_id=member.id, action_type="clearwarns")
    
@bot.hybrid_command(name="purge", description="Bulk delete messages. Staff-only.")
@staffperm("other_moderation")
@staff_only()
async def purge(ctx, count: int, member: discord.Member = None):
    def check(m):
        return m.author == member if member else True
    deleted = await ctx.channel.purge(limit=count+1, check=check)
    await ctx.send(f"🧹 Deleted {len(deleted)-1} messages.", delete_after=5)
    await log_action(ctx, f"Purged {len(deleted)-1} messages{(' from '+member.display_name) if member else ''}", action_type="purge")
    
@bot.hybrid_command(name="slowmode", description="Set slowmode for this channel. Staff-only.")
@staffperm("other_moderation")
@staff_only()
async def slowmode(ctx, seconds: int):
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"✅ Slowmode set to {seconds} seconds.")
    await log_action(ctx, f"Set slowmode to {seconds}s in #{ctx.channel.name}", action_type="slowmode")
    
@bot.hybrid_command(name="disable", description="Disable a command or a category. Staff-only.")
@staffperm("toggle_commands")
@staff_only()
async def disable(ctx, target: str):
    guild_id = str(ctx.guild.id)
    doc = await disabled_col.find_one({"guild": guild_id}) or {"disabled_commands": [], "disabled_categories": []}
    commands_set = set(doc["disabled_commands"])
    categories_set = set(doc["disabled_categories"])

    target = target.lower()
    all_cmds = [c.name for c in bot.commands]
    all_cats = ["economy", "moderation", "duckgpt", "general"]

    if target in all_cmds:
        if target in commands_set:
            return await ctx.send(f"❌ `{target}` is already disabled.")
        commands_set.add(target)
        await ctx.send(f"✅ Disabled command `{target}`.")
    elif target in all_cats:
        if target in categories_set:
            return await ctx.send(f"❌ Category `{target}` is already disabled.")
        categories_set.add(target)
        await ctx.send(f"✅ Disabled category `{target}`.")
    else:
        return await ctx.send("⚠️ Unknown command or category.")

    await disabled_col.update_one(
        {"guild": guild_id},
        {"$set": {
            "disabled_commands": list(commands_set),
            "disabled_categories": list(categories_set)
        }},
        upsert=True
    )

@bot.hybrid_command(name="enable", description="Enable a disabled command or category. Staff-only.")
@staffperm("toggle_commands")
@staff_only()
async def enable(ctx, target: str):
    guild_id = str(ctx.guild.id)
    doc = await disabled_col.find_one({"guild": guild_id}) or {"disabled_commands": [], "disabled_categories": []}
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
        {"$set": {
            "disabled_commands": list(commands_set),
            "disabled_categories": list(categories_set)
        }},
        upsert=True
    )
    
@bot.hybrid_command(name="listdisabled", description="List currently disabled commands and categories. Staff-only.")
@staffperm("toggle_commands")
@staff_only()
async def listdisabled(ctx):
    doc = await disabled_col.find_one({"guild": str(ctx.guild.id)})

    if not doc or ("commands" not in doc and "categories" not in doc):
        return await ctx.send("✅ No commands or categories are currently disabled.")

    disabled_cmds = doc.get("commands", [])
    disabled_cats = doc.get("categories", [])

    embed = discord.Embed(title="🔒 Disabled Features", color=discord.Color.red())
    if disabled_cmds:
        embed.add_field(name="Commands", value="\n".join(f"`{cmd}`" for cmd in disabled_cmds), inline=False)
    if disabled_cats:
        embed.add_field(name="Categories", value="\n".join(f"`{cat}`" for cat in disabled_cats), inline=False)

    await ctx.send(embed=embed)
    
@bot.hybrid_command(name="setprefix", description="Change the bot prefix. Staff-only.")
@staffperm("config")
@staff_only()
async def setprefix(ctx, new: str):
    await settings_col.update_one({"guild": str(ctx.guild.id)}, {"$set": {"prefix": new}}, upsert=True)
    await ctx.send(f"✅ Prefix updated to `{new}`.")
    await log_action(ctx, f"Prefix changed to {new}", action_type="setprefix")
    
@bot.hybrid_command(name="userinfo", description="View info about the specified user.")
@app_commands.describe(member="The user to check (optional - shows your info if not provided)")
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    join = member.joined_at.strftime("%Y-%m-%d")
    created = member.created_at.strftime("%Y-%m-%d")
    doc = await mod_col.find_one({"guild": str(ctx.guild.id), "user": str(member.id)})
    warns = len(doc.get("warnings", [])) if doc else 0
    embed = discord.Embed(title="User Information", color=discord.Color.blurple())
    embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined Server", value=join)
    embed.add_field(name="Account Created", value=created)
    embed.add_field(name="Warnings", value=warns)
    await ctx.send(embed=embed)

async def fetch_punishments(guild_id: int, user_id: int):
    data = await mod_col.find_one({"guild": str(guild_id), "user": str(user_id)})
    if not data:
        return "No recorded punishments."

    punishments = []
    for key, records in data.items():
        if isinstance(records, list) and key != "notes":
            for r in records:
                ts = ""
                tval = r.get("time")
                if tval:
                    try:
                        dt = datetime.fromisoformat(tval)
                        ts = f" (on <t:{int(dt.timestamp())}:f>)"
                    except Exception:
                        ts = f" ({tval})"
                punishments.append(f"**{key.title()}** - {r.get('reason', 'No reason')} *(by {r.get('by', 'Unknown')})*{ts}")

    notes = data.get("notes", [])
    if notes:
        last_note = notes[-1]
        nts = ""
        nt = last_note.get("time")
        if nt:
            try:
                ndt = datetime.fromisoformat(nt)
                nts = f" (on <t:{int(ndt.timestamp())}:f>)"
            except Exception:
                nts = f" ({nt})"
        punishments.append(f"📝 **Note:** {last_note.get('note')} *(by {last_note.get('by', 'Unknown')})*{nts}")

    if not punishments:
        return "No past recorded punishments with this bot."
    if len(punishments) > 10:
        return "\n".join(punishments[:10]) + f"\n…(+{len(punishments)-10} more)"
    return "\n".join(punishments)

def format_permissions(member: discord.Member):
    perms = [perm.replace("_", " ").title() for perm, val in member.guild_permissions if val]
    if not perms:
        return "None"
    lines = [", ".join(perms[i:i + 5]) for i in range(0, len(perms), 5)]
    result = "\n".join(lines)
    return result if len(result) <= 1024 else result[:1000] + "…"


def format_roles(member: discord.Member):
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    if not roles:
        return "None"
    if len(roles) > 10:
        return ", ".join(roles[:10]) + f"… (+{len(roles)-10} more)"
    return ", ".join(roles)


def format_flags(member: discord.Member):
    try:
        flags = [flag.name.replace("_", " ").title() for flag in member.public_flags.all()]
    except Exception:
        flags = []
    if not flags:
        return "None"
    if len(flags) > 10:
        return ", ".join(flags[:10]) + f"… (+{len(flags)-10} more)"
    return ", ".join(flags)


def format_activity(member: discord.Member):
    if not member.activity:
        return "None"
    activity_name = str(member.activity.name)[:100]
    return activity_name

class ModViewButtons(discord.ui.View):
    def __init__(self, bot, ctx, member, message=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.message = message
    
    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            try:
                await interaction.response.send_message("❌ This modview belongs to another moderator.", ephemeral=True)
            except Exception:
                pass
            return False
        return True

    @discord.ui.button(label="📋 Copy User ID", style=discord.ButtonStyle.grey)
    async def copy_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"🆔 **User ID:** `{self.member.id}`", ephemeral=True)

    @discord.ui.button(label="📝 Add Note", style=discord.ButtonStyle.blurple)
    async def add_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NoteModal(self.bot, self.ctx, self.member, self.message))

    @discord.ui.button(label="⚠️ Warn", style=discord.ButtonStyle.danger)
    async def warn_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self.bot, self.ctx, self.member, "warn", self.message))

    @discord.ui.button(label="🔇 Mute", style=discord.ButtonStyle.danger)
    async def mute_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self.bot, self.ctx, self.member, "mute", self.message))

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.danger)
    async def kick_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self.bot, self.ctx, self.member, "kick", self.message))

    @discord.ui.button(label="⛔ Ban", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self.bot, self.ctx, self.member, "ban", self.message))

    @discord.ui.button(label="🧹 Clear Warns", style=discord.ButtonStyle.green)
    async def clear_warns(self, interaction: discord.Interaction, button: discord.ui.Button):
        await mod_col.update_one(
            {"guild": str(self.ctx.guild.id), "user": str(self.member.id)},
            {"$set": {"warnings": []}}
        )
        await interaction.response.send_message(f"✅ All warnings for {self.member.mention} have been cleared.", ephemeral=True)
        await log_action(self.ctx, f"Cleared all warnings for {self.member}", user_id=self.member.id, action_type="clearwarns")

    @discord.ui.button(label="🧽 Clear Punishment", style=discord.ButtonStyle.green)
    async def clear_specific(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🔢 Please enter the **number** of the punishment or note you wish to clear.\nExample: `1` to remove the first one.",
            ephemeral=True
        )

        def check(m):
            return m.author == self.ctx.author and m.channel == self.ctx.channel

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=30.0)
            content = msg.content.strip()

            if not content.isdigit():
                await interaction.followup.send(f"❌ Expected a plain number, got `{content}`.", ephemeral=True)
                return

            number = int(content) - 1
        except asyncio.TimeoutError:
            await interaction.followup.send("⌛ Timed out. Please try again.", ephemeral=True)
            return

        guild_id = str(self.ctx.guild.id)
        user_id = str(self.member.id)
        data = await mod_col.find_one({"guild": guild_id, "user": user_id})

        if not data:
            await interaction.followup.send("❌ No punishments or notes found for this user.", ephemeral=True)
            return

        entries = []
        for key, records in data.items():
            if isinstance(records, list):
                for idx, r in enumerate(records):
                    entries.append((key, idx, r))

        if number < 0 or number >= len(entries):
            await interaction.followup.send("❌ That number doesn’t match any record.", ephemeral=True)
            return

        key, idx, record = entries[number]
        data[key].pop(idx)
        await mod_col.update_one({"guild": guild_id, "user": user_id}, {"$set": {key: data[key]}})

        entry_desc = f"{key.title()} - {record.get('reason', record.get('note', 'No details'))} (by {record.get('by', 'Unknown')})"
        await log_action(self.ctx, f"Cleared specific {key} for {self.member}: {entry_desc}",
                         user_id=self.member.id, action_type="clearspecific")

        await interaction.followup.send(f"✅ Cleared **{key} #{number + 1}** for {self.member.mention}.", ephemeral=True)

        punishments = await fetch_punishments(self.ctx.guild.id, self.member.id)
        if not self.message.embeds:
            return
        embed = self.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "📜 Past Punishments":
                embed.set_field_at(i, name="📜 Past Punishments", value=punishments, inline=False)
                break
        await self.message.edit(embed=embed, view=ModViewButtons(self.bot, self.ctx, self.member, self.message))

class NoteModal(discord.ui.Modal, title="Add Moderator Note"):
    note = discord.ui.TextInput(
        label="Note Content",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Ban this user if he does it again",
        required=True,
        max_length=500
    )

    def __init__(self, bot, ctx, member, message):
        super().__init__()
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        ctx = self.ctx
        member = self.member
        note_content = self.note.value

        await mod_col.update_one(
            {"guild": str(ctx.guild.id), "user": str(member.id)},
            {"$push": {"notes": {
                "by": str(ctx.author),
                "note": note_content,
                "time": datetime.now(timezone.utc).isoformat()
            }}},
            upsert=True
        )

        await interaction.response.send_message(f"✅ Note added for {member.mention}.", ephemeral=True)
        await log_action(ctx, f"Added note for {member}: {note_content}", user_id=member.id, action_type="note")

        punishments = await fetch_punishments(ctx.guild.id, member.id)
        if not self.message.embeds:
            return
        embed = self.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "📜 Past Punishments":
                embed.set_field_at(i, name="📜 Past Punishments", value=punishments, inline=False)
                break

        await self.message.edit(embed=embed, view=ModViewButtons(self.bot, ctx, member, self.message))

class PerformanceView(discord.ui.View):
    def __init__(self, guild_id, staff_members, days=30):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.staff_members = staff_members
        self.days = days
        
        options = []
        for member in staff_members[:25]:
            options.append(
                discord.SelectOption(
                    label=member.display_name,
                    description=f"Review {member.display_name}'s performance",
                    value=str(member.id),
                    emoji="👤"
                )
            )
        
        self.dropdown = discord.ui.Select(
            placeholder="📊 Select a staff member to review...",
            options=options
        )
        self.dropdown.callback = self.dropdown_callback
        self.add_item(self.dropdown)

    async def dropdown_callback(self, interaction: discord.Interaction):
        if interaction.user.id not in [m.id for m in self.staff_members]:
            await interaction.response.send_message("❌ Only staff members can use this!", ephemeral=True)
            return
        
        selected_id = int(self.dropdown.values[0])
        selected_member = interaction.guild.get_member(selected_id)
        
        if not selected_member:
            await interaction.response.send_message("❌ Staff member not found!", ephemeral=True)
            return
        
        analytics = await generate_performance_analytics(self.guild_id, selected_id, days=self.days)
        
        embed = discord.Embed(
            title=f"📊 Performance Review: {selected_member.display_name}",
            description=f"Analytics for {selected_member.mention}",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="📈 Basic Statistics",
            value=f"**Total Actions:** {analytics['total_actions']}\n"
                  f"**Messages Sent:** {analytics['total_messages']:,}\n"
                  f"**Commands Used:** {analytics['commands_used']:,}\n"
                  f"**Staff Since:** {analytics['staff_since']}",
            inline=False
        )
        
        if analytics['punishments']['total'] > 0:
            punish_text = f"**Total:** {analytics['punishments']['total']}\n"
            for ptype, count in analytics['punishments'].items():
                if ptype != 'total' and count > 0:
                    punish_text += f"**{ptype.capitalize()}:** {count}\n"
            embed.add_field(name="⚖️ Punishments", value=punish_text, inline=False)
        else:
            embed.add_field(name="⚖️ Punishments", value="No punishments recorded", inline=False)
        
        embed.add_field(
            name="🕐 Activity Metrics",
            value=f"**Avg. Actions/Day:** {analytics['avg_actions_per_day']:.1f}\n"
                  f"**Most Active Day:** {analytics['most_active_day']}\n"
                  f"**Peak Hour:** {analytics['peak_hour']}:00\n"
                  f"**Active This Week:** {'Yes' if analytics['active_this_week'] else 'No'}",
            inline=False
        )
        
        embed.add_field(
            name="📊 Efficiency",
            value=f"**Efficiency:** {analytics['efficiency']:.1f}%",
            inline=False
        )
        
        if analytics['recent_activity']:
            recent_text = "\n".join([f"• {activity}" for activity in analytics['recent_activity'][:5]])
            embed.add_field(name="📝 Recent Activity", value=recent_text, inline=False)
        
        embed.set_thumbnail(url=selected_member.display_avatar.url)
        embed.set_footer(text=f"Performance data for last {self.days} days")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def generate_performance_analytics(guild_id, staff_id, days=30):
    try:
        analytics = {
            'total_actions': 0,
            'total_messages': 0,
            'commands_used': 0,
            'staff_since': "Unknown",
            'punishments': {
                'warn': 0,
                'mute': 0,
                'kick': 0,
                'ban': 0,
                'total': 0
            },
            'avg_actions_per_day': 0,
            'most_active_day': "Monday",
            'peak_hour': 14,
            'active_this_week': False,
            'recent_activity': []
        }
        
        days_ago = datetime.now(timezone.utc) - timedelta(days=days)
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        
        mod_data = await mod_col.find({"guild": str(guild_id)}).to_list(length=None)
        
        for doc in mod_data:
            if 'warnings' in doc:
                for warning in doc.get('warnings', []):
                    if warning.get('by') == str(staff_id):
                        try:
                            warning_time = parser.isoparse(warning['time'])
                            if warning_time >= days_ago:
                                analytics['punishments']['warn'] += 1
                                analytics['total_actions'] += 1
                        except:
                            pass
            
            if 'mutes' in doc:
                for mute in doc.get('mutes', []):
                    if mute.get('by') == str(staff_id):
                        try:
                            mute_time = parser.isoparse(mute['time'])
                            if mute_time >= days_ago:
                                analytics['punishments']['mute'] += 1
                                analytics['total_actions'] += 1
                        except:
                            pass
            
            if 'kicks' in doc:
                for kick in doc.get('kicks', []):
                    if kick.get('by') == str(staff_id):
                        try:
                            kick_time = parser.isoparse(kick['time'])
                            if kick_time >= days_ago:
                                analytics['punishments']['kick'] += 1
                                analytics['total_actions'] += 1
                        except:
                            pass
            
            if 'bans' in doc:
                for ban in doc.get('bans', []):
                    if ban.get('by') == str(staff_id):
                        try:
                            ban_time = parser.isoparse(ban['time'])
                            if ban_time >= days_ago:
                                analytics['punishments']['ban'] += 1
                                analytics['total_actions'] += 1
                        except:
                            pass
        
        analytics['punishments']['total'] = sum(analytics['punishments'][p] for p in ['warn', 'mute', 'kick', 'ban'])
        
        analytics['commands_used'] = analytics['punishments']['total']
        
        for doc in mod_data:
            if 'notes' in doc:
                for note in doc.get('notes', []):
                    if note.get('by') == str(staff_id):
                        try:
                            note_time = parser.isoparse(note['time'])
                            if note_time >= days_ago:
                                analytics['commands_used'] += 1
                        except:
                            pass
        
        earliest_action = None
        for doc in mod_data:
            for action_type in ['warnings', 'mutes', 'kicks', 'bans', 'notes']:
                if action_type in doc:
                    for action in doc.get(action_type, []):
                        if action.get('by') == str(staff_id):
                            try:
                                action_time = parser.isoparse(action['time'])
                                if not earliest_action or action_time < earliest_action:
                                    earliest_action = action_time
                            except:
                                pass
        
        if earliest_action:
            analytics['staff_since'] = earliest_action.strftime("%b %d, %Y")
        
        analytics['total_messages'] = await get_user_message_count(guild_id, staff_id, days_ago)
        
        analytics['avg_actions_per_day'] = analytics['total_actions'] / days if analytics['total_actions'] > 0 else 0
        
        analytics['active_this_week'] = analytics['total_actions'] > 0 and any(
            doc.get('time') and parser.isoparse(doc['time']) >= seven_days_ago
            for doc in mod_data for doc_list in [doc.get('warnings', []), doc.get('mutes', []), 
                                               doc.get('kicks', []), doc.get('bans', [])]
            for doc_item in doc_list if isinstance(doc_item, dict) and doc_item.get('by') == str(staff_id)
        )
        
        expected_daily = 2
        analytics['efficiency'] = min(100, (analytics['avg_actions_per_day'] / expected_daily) * 100)
        
        analytics['recent_activity'] = [
            f"Used {ptype} command" for ptype, count in analytics['punishments'].items() 
            if ptype != 'total' and count > 0
        ][:3]
        
        if not analytics['recent_activity']:
            analytics['recent_activity'] = ["No recent activity"]
        
        return analytics
        
    except Exception as e:
        print(f"[Performance Analytics Error] {e}")
        return {
            'total_actions': 0,
            'total_messages': 0,
            'commands_used': 0,
            'staff_since': "Unknown",
            'punishments': {'warn': 0, 'mute': 0, 'kick': 0, 'ban': 0, 'total': 0},
            'avg_actions_per_day': 0,
            'most_active_day': "Monday",
            'peak_hour': 14,
            'active_this_week': False,
            'efficiency': 0,
            'recent_activity': ["No data available"]
        }

async def get_user_message_count(guild_id, user_id, since_date):
    try:
        message_count = 0
        mod_data = await mod_col.find({"guild": str(guild_id)}).to_list(length=None)
        
        for doc in mod_data:
            for action_type in ['warnings', 'mutes', 'kicks', 'bans', 'notes']:
                if action_type in doc:
                    for action in doc.get(action_type, []):
                        if action.get('by') == str(user_id):
                            try:
                                action_time = parser.isoparse(action['time'])
                                if action_time >= since_date:
                                    message_count += 1
                            except:
                                pass
        
        return message_count
    except Exception as e:
        print(f"[Message Count Error] {e}")
        return 0

@bot.command(name="performance", description="View staff performance analytics. Staff-only.")
@staffperm("other_moderation")
@staff_only()
async def performance(ctx, days: int = 30):
    try:
        staff_role_id = None
        settings = await settings_col.find_one({"guild": str(ctx.guild.id)})
        if settings and "staff_role" in settings:
            staff_role_id = settings["staff_role"]
        
        if not staff_role_id:
            return await ctx.send("❌ No staff role configured. Use `.staffset` to configure one.")
        
        if days < 1 or days > 365:
            return await ctx.send("❌ Review period must be between 1 and 365 days.")
        
        staff_role = ctx.guild.get_role(staff_role_id)
        if not staff_role:
            return await ctx.send("❌ Staff role not found.")
        
        staff_members = [member for member in ctx.guild.members if staff_role in member.roles]
        
        if not staff_members:
            return await ctx.send("❌ No staff members found.")
        
        embed = discord.Embed(
            title="📊 Staff Performance Review",
            description=f"Select a staff member from the dropdown below to view their performance analytics.\n\n"
                       f"**Total Staff Members:** {len(staff_members)}\n"
                       f"**Review Period:** Last {days} days",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="📊 Available Analytics",
            value="• Total moderation actions\n"
                  "• Punishment breakdown\n"
                  "• Activity patterns\n"
                  "• Efficiency rating",
            inline=False
        )
        
        embed.set_footer(text=f"Review period can be adjusted with .performance <days> (1-365)")
        
        view = PerformanceView(ctx.guild.id, staff_members, days)
        
        await ctx.send(embed=embed, view=view)
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred: `{type(e).__name__}: {e}`")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        prefix = await get_prefix(bot, ctx.message)
        await ctx.send(f"❌ Command not found. Use `{prefix}help` to see available commands.")
        return
    
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="❌ Missing Arguments",
            description=f"You're missing required arguments for this command.\n"
                       f"Use `?help` to see proper usage.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed, ephemeral=True)
    
    elif isinstance(error, commands.CheckFailure):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You don't have permission to use this command.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed, ephemeral=True)
    
    else:
        print(f"Unhandled error: {error}")
        if "not found" in str(error) and "<@" in str(error):
            embed = discord.Embed(
                title="❌ Invalid Member Input",
                description="Multiple users were mentioned. Please mention only one user at a time.",
                color=discord.Color.red()
            )
        else:
            embed = discord.Embed(
                title="❌ Unexpected Error",
                description="An unexpected error occurred. Please try again later.",
                color=discord.Color.red()
            )
        await ctx.send(embed=embed, ephemeral=True)

class ModerationConfirmView(discord.ui.View):
    def __init__(self, action, member, reason, duration=None, ctx=None, interaction=None, message=None):
        super().__init__(timeout=60)
        self.action = action
        self.member = member
        self.reason = reason
        self.duration = duration
        self.ctx = ctx
        self.interaction = interaction
        self.message = message
        self.confirmed = False

    @discord.ui.button(label="✅ Yes", style=discord.ButtonStyle.green, custom_id="confirm_yes")
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != (self.ctx.author.id if self.ctx else self.interaction.user.id):
            await interaction.response.send_message("❌ You can't confirm this action!", ephemeral=True)
            return
        
        self.confirmed = True
        await self.execute_moderation(interaction)

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.red, custom_id="confirm_no")
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != (self.ctx.author.id if self.ctx else self.interaction.user.id):
            await interaction.response.send_message("❌ You can't cancel this action!", ephemeral=True)
            return
        
        self.confirmed = False
        
        embed = discord.Embed(
            title="❌ Moderation Cancelled",
            description=f"The {self.action} action on {self.member.mention} has been cancelled.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)

    async def execute_moderation(self, interaction):
        try:
            ctx = self.ctx or self.interaction
            member = self.member
            reason = self.reason
            duration = self.duration

            if self.action == "warn":
                await mod_col.update_one(
                    {"guild": str(ctx.guild.id), "user": str(member.id)},
                    {"$push": {"warnings": {
                        "by": str(ctx.author),
                        "reason": reason,
                        "time": datetime.now(timezone.utc).isoformat()
                    }}},
                    upsert=True
                )

                try:
                    await member.send(f"⚠️ You were warned in **{ctx.guild.name}**\nReason: {reason}")
                except discord.Forbidden:
                    pass

                msg = f"✅ Warned {member.mention}."
                await log_action(ctx, f"Warn executed on {member}: {reason}", user_id=member.id, action_type="warn")

            elif self.action == "mute":
                mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
                if not mute_role:
                    mute_role = await ctx.guild.create_role(name="Muted")
                    for ch in ctx.guild.channels:
                        await ch.set_permissions(mute_role, speak=False, send_messages=False)

                await member.add_roles(mute_role, reason=reason)
                await mod_col.update_one(
                    {"guild": str(ctx.guild.id), "user": str(member.id)},
                    {"$push": {"mutes": {
                        "by": str(ctx.author),
                        "reason": reason,
                        "time": datetime.now(timezone.utc).isoformat()
                    }}},
                    upsert=True
                )
                
                if duration:
                    try:
                        seconds = parse_time(duration)
                        end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                        await mutes_col.update_one(
                            {"guild_id": ctx.guild.id, "user_id": member.id},
                            {"$set": {"mute_end": end_time}},
                            upsert=True
                        )
                        msg = f"🔇 Muted {member.mention} until <t:{int(end_time.timestamp())}:f>."
                    except Exception as e:
                        msg = f"🔇 Muted {member.mention}. (Duration error: {e})"
                else:
                    msg = f"🔇 Muted {member.mention}."
                await log_action(ctx, f"Mute executed on {member}: {reason}", user_id=member.id, action_type="mute")

            elif self.action == "kick":
                await member.kick(reason=f"{reason} (by {ctx.author})")
                await mod_col.update_one(
                    {"guild": str(ctx.guild.id), "user": str(member.id)},
                    {"$push": {"kicks": {
                        "by": str(ctx.author),
                        "reason": reason,
                        "time": datetime.now(timezone.utc).isoformat()
                    }}},
                    upsert=True
                )
                msg = f"✅ Kicked {member.mention}."
                await log_action(ctx, f"Kick executed on {member}: {reason}", user_id=member.id, action_type="kick")

            elif self.action == "ban":
                await member.ban(reason=f"{reason} (by {ctx.author})")
                await mod_col.update_one(
                    {"guild": str(ctx.guild.id), "user": str(member.id)},
                    {"$push": {"bans": {
                        "by": str(ctx.author),
                        "reason": reason,
                        "time": datetime.now(timezone.utc).isoformat()
                    }}},
                    upsert=True
                )
                msg = f"✅ Banned {member.mention}."
                await log_action(ctx, f"Ban executed on {member}: {reason}", user_id=member.id, action_type="ban")

            embed = discord.Embed(
                title=f"✅ {self.action.capitalize()} Executed",
                description=f"{msg}\n\nReason: {reason}" + (f"\nDuration: {duration}" if duration else ""),
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            
            if self.message:
                punishments = await fetch_punishments(ctx.guild.id, member.id)
                if self.message.embeds:
                    modview_embed = self.message.embeds[0]
                    for i, field in enumerate(modview_embed.fields):
                        if field.name == "📜 Past Punishments":
                            modview_embed.set_field_at(i, name="📜 Past Punishments", value=punishments, inline=False)
                            break
                    await self.message.edit(embed=modview_embed, view=ModViewButtons(bot, ctx, member, self.message))

        except Exception as e:
            error_embed = discord.Embed(
                title=f"❌ {self.action.capitalize()} Failed",
                description=f"An error occurred: `{type(e).__name__}: {e}`",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=error_embed, view=None)

class WarnModal(discord.ui.Modal, title="Moderator Action"):
    reason = discord.ui.TextInput(label="Reason (optional)", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, bot, ctx, member, action, message):
        super().__init__()
        self.bot = bot
        self.ctx = ctx
        self.member = member
        self.action = action
        self.message = message
        if self.action != "warn":
            self.duration = discord.ui.TextInput(
                label="Duration (e.g., 1d 2h 7m; blank = permanent)",
                style=discord.TextStyle.short,
                required=False
            )
            self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        reason = self.reason.value or "No reason provided"
        ctx = self.ctx
        duration = getattr(self, "duration", None).value if hasattr(self, "duration") and getattr(self, "duration", None) else None

        guild = interaction.guild
        member = guild.get_member(self.member.id)

        if member is None:
            try:
                member = await guild.fetch_member(self.member.id)
            except discord.NotFound:
                await interaction.followup.send("User is no longer in the server.", ephemeral=True)
                return

        embed = discord.Embed(
            title=f"⚠️ Confirm {self.action.capitalize()}",
            description=f"Are you sure you want to {self.action} {member.mention}?",
            color=discord.Color.orange()
        )
        
        embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        
        if duration:
            embed.add_field(name="Duration", value=duration, inline=False)
            
        embed.set_footer(text="This action will be logged.")
        
        if ctx.channel:
            confirm_view = ModerationConfirmView(self.action, member, reason, duration, ctx=ctx, message=self.message)
            await ctx.send(embed=embed, view=confirm_view)
            await interaction.followup.send(f"✅ Confirmation dialog sent to {ctx.channel.mention}", ephemeral=True)
        else:
            await interaction.followup.send("❌ Could not send confirmation dialog.", ephemeral=True)

@bot.hybrid_command(name="modview", description="Open moderator view for a user. Staff-only.")
@staffperm("other_moderation")
@staff_only()
async def modview(ctx, member: discord.Member):
    punishments = await fetch_punishments(ctx.guild.id, member.id)
    mod_perms = format_permissions(member)
    roles = format_roles(member)
    flags = format_flags(member)
    activity = format_activity(member)

    nick = member.nick or "None"
    pending = "✅ Yes" if member.pending else "❌ No"
    bot_flag = "🤖 Yes" if member.bot else "👤 No"
    top_role = member.top_role.mention
    status = str(member.status).title()
    joined_discord = f"<t:{int(member.created_at.timestamp())}:F>"
    joined_server = f"<t:{int(member.joined_at.timestamp())}:F>"

    verification_map = {
        VerificationLevel.none: "None",
        VerificationLevel.low: "Low",
        VerificationLevel.medium: "Medium",
        VerificationLevel.high: "High",
    }
    verification_name = verification_map.get(ctx.guild.verification_level, str(ctx.guild.verification_level).title())

    embed = discord.Embed(
        title=f"🛠️ Moderator View: {member}",
        color=discord.Color.blurple(),
        timestamp=datetime.now()
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(name="👤 Username", value=f"{member} (`{member.name}`)", inline=False)
    embed.add_field(name="🪪 Nickname", value=nick, inline=True)
    embed.add_field(name="🤖 Bot Account", value=bot_flag, inline=True)
    embed.add_field(name="📶 Status", value=status, inline=True)
    embed.add_field(name="🧩 Top Role", value=top_role, inline=True)
    embed.add_field(name="🎭 Roles", value=roles, inline=False)
    embed.add_field(name="🕐 Joined Discord", value=joined_discord, inline=True)
    embed.add_field(name="🏠 Joined Server", value=joined_server, inline=True)
    embed.add_field(name="🧾 Pending Verification", value=pending, inline=True)
    embed.add_field(name="🔒 Guild Verification Level", value=verification_name, inline=False)
    embed.add_field(name="🎖️ Badges / Flags", value=flags, inline=False)
    embed.add_field(name="⚙️ Effective Permissions", value=mod_perms, inline=False)
    embed.add_field(name="📜 Past Punishments", value=punishments, inline=False)

    msg = await ctx.send(embed=embed)
    view = ModViewButtons(bot, ctx, member, msg)
    await msg.edit(view=view)

@modview.error
async def modview_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have the required permissions to use this command.", ephemeral=True)
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ This command is restricted to staff members only.", ephemeral=True)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid member provided. Please mention a valid user.", ephemeral=True)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Could not find that member in this server.", ephemeral=True)
    elif isinstance(error, commands.CommandInvokeError):
        await ctx.send(f"⚠️ An unexpected error occurred: `{error.original}`", ephemeral=True)
    else:
        await ctx.send(f"⚠️ An error occurred: `{error}`", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    data = await reaction_col.find_one({"message": payload.message_id})
    if not data:
        return

    if str(payload.emoji) != data["emoji"]:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    role = guild.get_role(data["role"])
    if role is None:
        return

    member = guild.get_member(payload.user_id)
    if member is None:
        return

    try:
        await member.add_roles(role)
    except Exception as e:
        print(f"[reactionrole add error] {e}")

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    data = await reaction_col.find_one({"message": payload.message_id})
    if not data:
        return

    if str(payload.emoji) != data["emoji"]:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    role = guild.get_role(data["role"])
    if role is None:
        return

    member = guild.get_member(payload.user_id)
    if member is None:
        return

    try:
        await member.remove_roles(role)
    except Exception as e:
        print(f"[reactionrole remove error] {e}")

@bot.hybrid_command(name="reactionrole", description="Set up a reaction role. Staff-only.")
@staffperm("reactionroles")
@staff_only()
async def reactionrole(ctx, message_id: int, emoji, role: discord.Role):
    try:
        msg = await ctx.channel.fetch_message(message_id)
        await msg.add_reaction(emoji)
        await reaction_col.update_one({"message": message_id}, {"$set": {"emoji": str(emoji), "role": role.id}}, upsert=True)
        await ctx.send(f"✅ Reaction role set: {emoji} will grant {role.mention}.")
    except Exception as e:
        print(f"[reactionrole error] {e}")
        await ctx.send("❌ Could not set reaction role. Check your permissions and message ID.")

@bot.hybrid_command(name="stickynote", description="Set a sticky note in this channel. Staff-only.")
@staffperm("stickynotes")
@staff_only()
async def stickynote(ctx):
    await ctx.send("📝 Please type the message to pin as sticky:")

    def check(m): return m.author == ctx.author and m.channel == ctx.channel
    try:
        reply = await bot.wait_for("message", check=check, timeout=60)

        doc = await sticky_col.find_one({"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)})
        if doc:
            try:
                old_msg = await ctx.channel.fetch_message(doc["message"])
                await old_msg.delete()
            except discord.NotFound:
                print(f"[stickynote] Previous message {doc['message']} not found, creating new one")
            except discord.Forbidden:
                print(f"[stickynote] No permission to delete message {doc['message']}")
            except Exception as e:
                print(f"[stickynote delete error] {e}")

        sent = await ctx.send(reply.content)
        await sticky_col.update_one(
            {"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)},
            {"$set": {"text": reply.content, "message": sent.id}},
            upsert=True
        )
        await ctx.send("✅ Sticky note created.")
    except asyncio.TimeoutError:
        await ctx.send("❌ Timeout. Sticky note creation cancelled.")

@bot.hybrid_command(name="unstickynote", description="Remove the sticky note. Staff-only.")
@staffperm("stickynotes")
@staff_only()
async def unstickynote(ctx):
    doc = await sticky_col.find_one({"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)})
    if doc:
        try:
            msg = await ctx.channel.fetch_message(doc["message"])
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
        sticky_col.delete_one({"guild": str(ctx.guild.id), "channel": str(ctx.channel.id)})
        await ctx.send("✅ Sticky note removed.")
    else:
        await ctx.send("⚠️ No sticky note set for this channel.")

@bot.command()
@staffperm("config")
@staff_only()
async def testwelcome(ctx, member: discord.Member = None):
    member = member or ctx.author
    config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}

    channel_id = config.get("welcome_channel")
    msg_template = config.get("welcome_message")
    channel = ctx.guild.get_channel(channel_id)

    if not channel:
        return await ctx.send("❌ No welcome channel set.")

    msg_template = msg_template or (
        "👋 Welcome {mention} to **{server}**! You are member #{membercount}!"
    )

    text = (
        msg_template
        .replace("{username}", member.name)
        .replace("{mention}", member.mention)
        .replace("{server}", ctx.guild.name)
        .replace("{membercount}", str(ctx.guild.member_count))
    )

    embed = discord.Embed(description=text, color=discord.Color.blue())
    embed.set_thumbnail(url=member.display_avatar.url)

    await channel.send(embed=embed)
    await ctx.send("✅ Sent test welcome message.")

@bot.command()
@staffperm("config")
@staff_only()
async def testboost(ctx, member: discord.Member = None):
    member = member or ctx.author
    config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}

    channel_id = config.get("boost_channel")
    msg_template = config.get("boost_message")
    react_emoji = config.get("boost_react_emoji")
    channel = ctx.guild.get_channel(channel_id)

    if not channel:
        return await ctx.send("❌ No boost channel set.")

    msg_template = msg_template or (
        "🚀 {mention} just boosted **{server}**! We’re now at {boostcount} boosts! 🎉"
    )

    text = (
        msg_template
        .replace("{username}", member.name)
        .replace("{mention}", member.mention)
        .replace("{server}", ctx.guild.name)
        .replace("{boostcount}", str(ctx.guild.premium_subscription_count or 0))
    )

    embed = discord.Embed(
        description=text,
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_author(name="Boost Alert!", icon_url=member.display_avatar.url)

    try:
        sent_message = await channel.send(embed=embed)
        await ctx.send("✅ Sent test boost message.")
        if react_emoji:
            try:
                await sent_message.add_reaction(react_emoji)
            except discord.HTTPException:
                await ctx.send("⚠️ Could not react with the configured emoji (invalid or deleted).")
    except Exception as e:
        await ctx.send(f"⚠️ Failed to send test boost message: `{e}`")

@tasks.loop(minutes=30)
async def check_boosters_loop():
    for guild in bot.guilds:
        try:
            config = await config_col.find_one({"guild": str(guild.id)})
            if not config:
                continue

            boost_channel_id = config.get("boost_channel")
            if not boost_channel_id:
                continue

            channel = guild.get_channel(boost_channel_id)
            if not channel:
                continue

            boost_message = config.get("boost_message")
            if not boost_message:
                continue

            for member in guild.members:
                if not member.premium_since:
                    continue

                boost_key = f"{guild.id}-{member.id}"
                boost_record = await boost_col.find_one({"_id": boost_key})

                if not boost_record or boost_record.get("last_thanked") != member.premium_since.isoformat():
                    msg_content = (
                        boost_message
                        .replace("{username}", member.name)
                        .replace("{mention}", member.mention)
                        .replace("{server}", guild.name)
                        .replace("{boostcount}", str(guild.premium_subscription_count or 0))
                    )

                    embed = discord.Embed(
                        description=msg_content,
                        color=discord.Color.fuchsia(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.set_author(name="Boost Alert!", icon_url=member.display_avatar.url)
                    embed.set_thumbnail(url=member.display_avatar.url)

                    try:
                        sent_message = await channel.send(embed=embed)
                        emoji = config.get("boost_react_emoji")
                        if emoji:
                            try:
                                await sent_message.add_reaction(emoji)
                            except:
                                pass

                        await boost_col.update_one(
                            {"_id": boost_key},
                            {"$set": {"last_thanked": member.premium_since.isoformat()}},
                            upsert=True
                        )
                    except Exception as e:
                        print(f"⚠️ Error sending periodic boost message in {guild.name} for {member}: {e}")

        except Exception as e:
            print(f"⚠️ Error in check_boosters_loop for guild {guild.id}: {e}")

@check_boosters_loop.before_loop
async def before_check_boosters():
    await bot.wait_until_ready()

@bot.event
async def on_member_update(before, after):
    # Boost thanks are handled by on_message via Discord system boost messages.
    # Keeping this event minimal to avoid duplicate thank-yous and to ensure
    # multiple boosts by the same member are each thanked individually.
    return

@bot.event
async def on_member_join(member):
    guild = member.guild
    try:
        doc = await guild_config_col.find_one({"guild_id": str(guild.id)})
        if not doc:
            doc = {}
        new_invites = await get_guild_invites(guild)
        old_invites_data = invite_cache.get(guild.id, (time.time(), []))
        
        if isinstance(old_invites_data, tuple) and len(old_invites_data) == 2:
            _, old_invites = old_invites_data
        else:
            old_invites = old_invites_data if isinstance(old_invites_data, list) else []

        used_invite = None
        for new_inv in new_invites:
            old = discord.utils.get(old_invites, code=new_inv.code)
            if old and new_inv.uses > old.uses:
                used_invite = new_inv
                break

        invite_cache[guild.id] = (time.time(), new_invites)

        if used_invite:
            inviter = used_invite.inviter
            await invites_col.update_one(
                {"guild_id": str(guild.id), "user_id": str(inviter.id)},
                {"$inc": {"total": 1, "regular": 1, "joins": 1}},
                upsert=True
            )
            await invites_col.update_one(
                {"guild_id": str(guild.id), "code": used_invite.code},
                {
                    "$set": {"inviter_id": str(inviter.id), "uses": used_invite.uses},
                    "$addToSet": {"joined_users": str(member.id)}
                },
                upsert=True
            )
            config = await invite_config_col.find_one({"guild_id": str(guild.id)})
            if config:
                channel = guild.get_channel(int(config["channel_id"]))
                if channel:
                    await channel.send(
                        f"👋 Welcome {member.mention}! Invited by {inviter.mention} "
                        f"(now **{used_invite.uses}** uses)"
                    )

        welcome_ch = guild.get_channel(doc.get("welcome_channel"))
        if welcome_ch:
            welcome_msg = doc.get("welcome_message") or (
                "⭐ **Quack loud in** <#1370374734037909576> and enjoy the pond! ✨\n"
                "⭐ **Check** <#1370374725108236379> to equip tag! ✨\n"
                "⭐ **Boost our pond** and get exclusive <@&1370367716892082236> role! ✨"
            )
            embed = discord.Embed(
                title=f"Welcome to Duck Paradise 🦆 quack!",
                description=welcome_msg,
                color=discord.Color.from_str("#2f3136")
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_image(url="https://cdn.discordapp.com/attachments/1370374741579534408/1386456926300409939/duckduckgo-welcome.gif")
            embed.set_footer(text=f"You are our {guild.member_count}th member!")
            msg = await welcome_ch.send(f"welcome, {member.mention} 🐥!", embed=embed)
            duck_emoji = discord.utils.get(guild.emojis, name="duckwave2")
            if duck_emoji:
                await msg.add_reaction(duck_emoji)
            else:
                print("Custom emoji 'duckwave2' not found in guild.")

        if member.premium_since:
            boost_key = f"{guild.id}-{member.id}"
            boost_record = await boost_col.find_one({"_id": boost_key})
            
            if not boost_record or boost_record.get("last_thanked") != member.premium_since.isoformat():
                boost_ch = guild.get_channel(doc.get("boost_channel"))
                if boost_ch:
                    boost_msg = doc.get("boost_message") or (
                        f"{member.mention} just boosted the pond! 🌟\nThank you for your support!"
                    )
                    
                    text = (
                        boost_msg
                        .replace("{username}", member.name)
                        .replace("{mention}", member.mention)
                        .replace("{server}", guild.name)
                        .replace("{boostcount}", str(guild.premium_subscription_count or 0))
                    )

                    boost_embed = discord.Embed(
                        title="🚀 Boost Alert!",
                        description=text,
                        color=discord.Color.fuchsia(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    boost_embed.set_thumbnail(url=member.display_avatar.url)
                    sent_msg = await boost_ch.send(embed=boost_embed)

                    emoji = doc.get("boost_react_emoji")
                    if emoji:
                        try:
                            await sent_msg.add_reaction(emoji)
                        except:
                            pass
                
                await boost_col.update_one(
                    {"_id": boost_key},
                    {"$set": {"last_thanked": member.premium_since.isoformat()}},
                    upsert=True
                )

        doc = await mutes_col.find_one({"guild_id": member.guild.id, "user_id": member.id})
        if doc:
            mute_role = discord.utils.get(member.guild.roles, name="Muted")
            if mute_role and mute_role not in member.roles:
                await member.add_roles(mute_role, reason="Reapplying mute after rejoin")

            mute_end = doc.get("mute_end")
            if mute_end:
                if isinstance(mute_end, str):
                    try:
                        mute_end = datetime.fromisoformat(mute_end)
                    except Exception:
                        print(f"[on_member_join] Invalid mute_end format for {member.id}: {mute_end}")
                        mute_end = None

                if mute_end:
                    if isinstance(mute_end, str):
                        try:
                            mute_end = datetime.fromisoformat(mute_end)
                        except ValueError:
                            mute_end = datetime.strptime(mute_end, "%Y-%m-%d %H:%M:%S")

                    if mute_end.tzinfo is None:
                        mute_end = mute_end.replace(tzinfo=timezone.utc)
                    now_utc = datetime.now(timezone.utc)

                    if now_utc < mute_end:
                        remaining = (mute_end - now_utc).total_seconds()
                        bot.loop.create_task(schedule_unmute(member.guild, member, remaining))
                    elif now_utc >= mute_end:
                        await mutes_col.delete_one({"guild_id": member.guild.id, "user_id": member.id})

    except Exception as e:
        print("on_member_join ERROR:", e)

@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    code_doc = await invites_col.find_one(
        {"guild_id": str(guild.id), "joined_users": str(member.id)}
    )
    if code_doc:
        inviter_id = code_doc.get("inviter_id")
        await invites_col.update_one(
            {"guild_id": str(guild.id), "code": code_doc.get("code")},
            {"$pull": {"joined_users": str(member.id)}}
        )
        if inviter_id:
            stats = await invites_col.find_one({"guild_id": str(guild.id), "user_id": str(inviter_id)})
            joins = (stats.get("joins", stats.get("regular", 0)) if stats else 0)
            leaves = (stats.get("leaves", stats.get("left", 0)) if stats else 0)
            await invites_col.update_one(
                {"guild_id": str(guild.id), "user_id": str(inviter_id)},
                {
                    "$inc": {"leaves": 1},
                    "$set": {"total": max(joins - (leaves + 1), 0)}
                },
                upsert=True
            )

@bot.hybrid_command(name="duck", description="Random picture of a duck.")
@cooldown(1, 5, BucketType.member)
@blacklist_barrier()
async def duck(ctx):
    config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}

    allowed_channels = config.get("ALLOWED_DUCK_CHANNELS", [])
    if allowed_channels and ctx.channel.id not in allowed_channels:
        return await ctx.send("🚫 You can't use this command here.")

    async with aiohttp.ClientSession() as session:
        async with session.get("https://random-d.uk/api/random") as resp:
            if resp.status != 200:
                return await ctx.send("❌ Could not get a duck right now, try again later!")
            data = await resp.json()
            url = data.get("url")
            if not url:
                return await ctx.send("❌ Duck image not found, sorry!")

    embed = discord.Embed(
        title="🦆 Quack!",
        color=discord.Color.blue()
    )
    embed.set_image(url=url)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="quote", description="Get a random quote.")
@cooldown(1, 5, BucketType.member)
@blacklist_barrier()
async def quote(ctx):
    api_url = "https://zenquotes.io/api/random"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                text = await resp.text()

                if resp.status != 200:
                    return await ctx.send(
                        f"❌ Could not fetch a quote right now (Status {resp.status})"
                    )

                try:
                    data = json.loads(text)
                except Exception as e:
                    print(f"[JSON PARSE ERROR] {type(e).__name__} - {e}")
                    return await ctx.send(
                        f"⚠️ API returned invalid data:\n```{text[:200]}...```"
                    )

        if not data or not isinstance(data, list):
            return await ctx.send("❌ Couldn't fetch a quote this time, try again!")

        quote_text = str(data[0].get("q") or "No quote found")
        author = str(data[0].get("a") or "Unknown")

        embed = discord.Embed(
            title="💬 Random Quote",
            description=f"“{quote_text}”\n\n- *{author}*",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send("⚠️ Something went wrong while fetching a quote. Contact thetruck.")
        print(f"[QUOTE ERROR] {type(e).__name__} - {e}")

@tasks.loop(seconds=0.01)
async def check_reminders():
    now = datetime.now(timezone.utc)

    reminders = await reminders_col.find({"remind_at": {"$lte": now}}).to_list(length=None)

    for reminder in reminders:
        user = bot.get_user(int(reminder["user_id"]))
        if user:
            try:
                await user.send(f"⏰ Reminder: {reminder['message']}")
            except Exception as e:
                print(f"Failed to send reminder to {user}: {e}")

        await reminders_col.delete_one({"_id": reminder["_id"]})

@check_reminders.before_loop
async def before_check_reminders():
    await bot.wait_until_ready()

@bot.hybrid_command(name="serverinfo", description="View server information")
async def serverinfo(ctx):
    guild = ctx.guild

    embed = discord.Embed(
        title=f"📜 Server Information - {guild.name}",
        color=discord.Color.blurple()
    )
    embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
    embed.add_field(name="👥 Members", value=f"{guild.member_count:,}", inline=True)
    embed.add_field(name="🆔 Server ID", value=guild.id, inline=True)
    embed.add_field(name="📅 Created On", value=guild.created_at.strftime("%B %d, %Y"), inline=False)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)

    await ctx.send(embed=embed)

class TutorialPages(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=300)
        self.pages = pages
        self.current = 0

    async def switch(self, interaction, index):
        self.current = index
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @discord.ui.button(label="🏠 Intro", style=discord.ButtonStyle.primary)
    async def intro(self, interaction, button):
        await self.switch(interaction, 0)

    @discord.ui.button(label="🧭 Setup Order", style=discord.ButtonStyle.secondary)
    async def setuporder(self, interaction, button):
        await self.switch(interaction, 1)

    @discord.ui.button(label="💰 Economy", style=discord.ButtonStyle.secondary)
    async def economy(self, interaction, button):
        await self.switch(interaction, 2)

    @discord.ui.button(label="⚔️ Moderation", style=discord.ButtonStyle.secondary)
    async def moderation(self, interaction, button):
        await self.switch(interaction, 3)

    @discord.ui.button(label="🎟 Tickets", style=discord.ButtonStyle.secondary)
    async def tickets(self, interaction, button):
        await self.switch(interaction, 4)

    @discord.ui.button(label="⚙️ Config", style=discord.ButtonStyle.secondary)
    async def config(self, interaction, button):
        await self.switch(interaction, 5)

    @discord.ui.button(label="🗒 StickyNotes", style=discord.ButtonStyle.secondary)
    async def stickynotes(self, interaction, button):
        await self.switch(interaction, 6)

    @discord.ui.button(label="📨 Invites", style=discord.ButtonStyle.secondary)
    async def invites(self, interaction, button):
        await self.switch(interaction, 7)

    @discord.ui.button(label="✨ Vanity", style=discord.ButtonStyle.secondary)
    async def vanity(self, interaction, button):
        await self.switch(interaction, 8)

    @discord.ui.button(label="🎭 Roles", style=discord.ButtonStyle.secondary)
    async def roles(self, interaction, button):
        await self.switch(interaction, 9)

    @discord.ui.button(label="📦 Other", style=discord.ButtonStyle.secondary)
    async def other(self, interaction, button):
        await self.switch(interaction, 10)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

@bot.hybrid_command(name="tutorial", description="Learn how to use each bot system.")
async def tutorial(ctx):
    settings = await settings_col.find_one({"guild": str(ctx.guild.id)}) or {}
    config = await config_col.find_one({"guild": str(ctx.guild.id)}) or {}
    guild_cfg = await guild_config_col.find_one({"guild": str(ctx.guild.id)}) or {}
    invite_cfg = await invite_config_col.find_one({"guild": str(ctx.guild.id)}) or {}
    prefix = settings.get("prefix", "?") if settings else "?"

    staff_role = settings.get("staff_role") if settings else None
    log_channel = settings.get("log_channel") if settings else None
    invite_log = invite_cfg.get("log_channel") if invite_cfg else None

    ticket_panels = await ticket_panels_col.count_documents({"guild": str(ctx.guild.id)})
    shop_items = await shop_col.count_documents({})
    sticky_notes = await sticky_col.count_documents({"guild": str(ctx.guild.id)})

    missing = []

    if not staff_role:
        missing.append("• **Staff role not set**")
    if not log_channel:
        missing.append("• **Logging channel not set**")
    if ticket_panels == 0:
        missing.append("• **No ticket panels created**")
    if shop_items == 0:
        missing.append("• **Economy shop is empty**")
    if sticky_notes == 0:
        missing.append("• **No sticky notes created**")
    if not invite_log:
        missing.append("• **Invite logging not configured**")

    missing_block = "\n".join(missing) if missing else "🎉 All core systems are configured!"

    pages = []

    def bar(index, total=10):
        filled = "█" * index
        empty = "░" * (total - index)
        return f"{filled}{empty} **{index}/{total}**"

    intro = discord.Embed(
        title="📚 Bot Tutorial — How Everything Works",
        description=(
            f"{bar(1)}\n\n"
            "Welcome to the full system tutorial! This menu guides you through every bot feature.\n"
            "Use the navigation buttons to browse each category.\n\n"
            f"Your server prefix is: **{prefix}**\n\n"
            "**Setup Status:**\n"
            f"{missing_block}\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}help`\n"
            f"• `{prefix}configure`\n"
        ),
        color=discord.Color.blue()
    )
    pages.append(intro)

    setup_order = discord.Embed(
        title="🧭 Recommended Setup Order",
        description=(
            f"{bar(2)}\n\n"
            "**Best setup order for a fresh server:**\n"
            "1. Config → Prefix, staff role, logs\n"
            "2. Moderation → Make sure permissions work\n"
            "3. Tickets → Create panels\n"
            "4. Economy → Add shop items\n"
            "5. Sticky Notes → Channel reminders\n"
            "6. Invites → Set logging\n"
            "7. Vanity → Enable tracking\n"
            "8. Roles → Claimable roles\n"
            "9. Other → Giveaways & misc tools\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}setprefix <prefix>`\n"
            f"• `{prefix}configure`\n"
        ),
        color=discord.Color.purple()
    )
    pages.append(setup_order)

    econ = discord.Embed(
        title="💰 Economy System",
        description=(
            f"{bar(3)}\n\n"
            "Users earn coins, store cash in bank, gamble, work jobs, and buy items.\n"
            "Admins can fully customize the shop.\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}work`\n"
            f"• `{prefix}daily`\n"
            f"• `{prefix}balance`\n"
        ),
        color=discord.Color.green()
    )
    pages.append(econ)

    mod = discord.Embed(
        title="⚔️ Moderation System",
        description=(
            f"{bar(4)}\n\n"
            "Kicks, bans, slowmode, warnings, mutes, purges, blacklisting, and more.\n"
            "Everything logs cleanly once configured.\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}warn @user <reason>`\n"
            f"• `{prefix}mute @user <time>`\n"
            f"• `{prefix}purge <amount>`"
        ),
        color=discord.Color.red()
    )
    pages.append(mod)

    ticket = discord.Embed(
        title="🎟 Ticket System",
        description=(
            f"{bar(5)}\n\n"
            "Create custom ticket panels with buttons, categories, transcripts, and support tools.\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}ticketsetup`\n"
            f"• `{prefix}ticketadd @user`\n"
            f"• `{prefix}ticketclose`"
        ),
        color=discord.Color.blurple()
    )
    pages.append(ticket)

    config = discord.Embed(
        title="⚙️ Config System",
        description=(
            f"{bar(6)}\n\n"
            "Manage prefix, roles, logs, and system toggles.\n"
            "This is where the bot truly comes alive.\n\n"
            "**Starter commands:**\n"
            f"• `{prefix}configure`\n"
            f"• `{prefix}viewconfig`\n"
            f"• `{prefix}editconfig`"
        ),
        color=discord.Color.orange()
    )
    pages.append(config)

    sticky = discord.Embed(
        title="🗒 Sticky Notes System",
        description=(
            f"{bar(7)}\n\n"
            "Pin an auto-reposting sticky message to keep rules or reminders visible.\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}stickynote <channel> <message>`\n"
            f"• `{prefix}unstickynote <id>`"
        ),
        color=discord.Color.yellow()
    )
    pages.append(sticky)

    invites_page = discord.Embed(
        title="📨 Invite Tracking System",
        description=(
            f"{bar(8)}\n\n"
            "Tracks who invited who, logs joins, and counts user invites.\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}invites @user`\n"
            f"• `{prefix}invitechannel`\n"
            f"• `{prefix}removeinvites @user <amount>`"
        ),
        color=discord.Color.teal()
    )
    pages.append(invites_page)

    vanity = discord.Embed(
        title="✨ Vanity System",
        description=(
            f"{bar(9)}\n\n"
            "Reward users who promote the server externally.\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}vanityroles @role #log <status>`\n"
            f"• `{prefix}promoters`\n"
            f"• `{prefix}resetpromoters`"
        ),
        color=discord.Color.magenta()
    )
    pages.append(vanity)

    roles = discord.Embed(
        title="🎭 Role System",
        description=(
            f"{bar(10)}\n\n"
            "Create claimable roles that members can pick from.\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}roleadd @role`\n"
            f"• `{prefix}roleremove @role`"
        ),
        color=discord.Color.gold()
    )
    pages.append(roles)

    other = discord.Embed(
        title="📦 Other Systems",
        description=(
            f"{bar(10)}\n\n"
            "Giveaways, reaction roles, and more.\n\n"
            "**Starter Commands:**\n"
            f"• `{prefix}giveaway`\n"
            f"• `{prefix}reactionrole <msg_id> <emoji> @role`\n"
            f"• `{prefix}disable <cmd/category>`\n"
            f"• `{prefix}enable <cmd/category>`"
        ),
        color=discord.Color.light_gray()
    )
    pages.append(other)

    view = TutorialPages(pages)
    await ctx.send(embed=pages[0], view=view)

class StaffSections(discord.ui.View):
    def __init__(self, prefix):
        super().__init__(timeout=300)
        self.prefix = prefix

    @discord.ui.button(label="⚔️ Moderation", style=discord.ButtonStyle.secondary)
    async def moderation(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="⚔️ Moderation Commands", color=discord.Color.red())
        embed.add_field(name=f"{self.prefix}kick @user [reason]", value="Kick a member from the server.", inline=False)
        embed.add_field(name=f"{self.prefix}ban @user [reason]", value="Ban a member from the server.", inline=False)
        embed.add_field(name=f"{self.prefix}unban <user_id>", value="Unban a previously banned user.", inline=False)
        embed.add_field(name=f"{self.prefix}mute @user <time> [reason]", value="Temporarily mute a member.", inline=False)
        embed.add_field(name=f"{self.prefix}unmute @user", value="Unmute a previously muted member.", inline=False)
        embed.add_field(name=f"{self.prefix}warn @user [reason]", value="Issue a warning to a user.", inline=False)
        embed.add_field(name=f"{self.prefix}clearwarns @user", value="Clear all warnings for a user.", inline=False)
        embed.add_field(name=f"{self.prefix}purge <amount>", value="Bulk delete a number of messages.", inline=False)
        embed.add_field(name=f"{self.prefix}slowmode <seconds>", value="Set a slowmode timer for the current channel.", inline=False)
        embed.add_field(name=f"{self.prefix}blacklist @user", value="Blacklist a user from using bot commands.", inline=False)
        embed.add_field(name=f"{self.prefix}whitelist @user", value="Remove a user from the blacklist.", inline=False)
        embed.set_footer(text="Moderation - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🎟 Tickets", style=discord.ButtonStyle.secondary)
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎟 Ticket Commands", color=discord.Color.blurple())
        embed.add_field(name=f"{self.prefix}ticketsetup", value="Create an interactive ticket panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketdeletepanel <name>", value="Delete a saved panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketlist", value="List saved panels.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketforceclose", value="Force close ticket.", inline=False)
        embed.add_field(name=f"{self.prefix}transcriptsearch <username>", value="Search transcripts by username.", inline=False)
        embed.add_field(name=f"{self.prefix}transcriptlist", value="List all tickets (open & closed) with details.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketaddbutton", value="Add a ticket button to an existing panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketeditbutton", value="Edit a ticket button from an existing panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketpanel <name>", value="Post a saved ticket panel.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketclose", value="Request to close a ticket.", inline=False)
        embed.add_field(name=f"{self.prefix}transcript <id>", value="Fetch a saved transcript of a ticket.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketadduser @user", value="Add a user to the current ticket.", inline=False)
        embed.add_field(name=f"{self.prefix}ticketremoveuser @user", value="Remove a user from the current ticket.", inline=False)
        embed.set_footer(text="Tickets - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🗒 StickyNotes", style=discord.ButtonStyle.secondary)
    async def stickynotes(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🗒 Sticky Note Commands", color=discord.Color.yellow())
        embed.add_field(name=f"{self.prefix}stickynote", value="Set a sticky note in the current channel.", inline=False)
        embed.add_field(name=f"{self.prefix}unstickynote", value="Remove the sticky note from this channel.", inline=False)
        embed.set_footer(text="StickyNotes - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="💰 Economy", style=discord.ButtonStyle.secondary)
    async def economy(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="💰 Economy Commands", color=discord.Color.green())
        embed.add_field(name=f"{self.prefix}additem \"<name>\" <price>", value="Add a new item to the shop. `/additem` uses separate name and price boxes.", inline=False)
        embed.add_field(name=f"{self.prefix}edititem <item> <price> <desc>", value="Edit an existing shop item.", inline=False)
        embed.add_field(name=f"{self.prefix}delitem <item>", value="Delete a shop item.", inline=False)
        embed.add_field(name=f"{self.prefix}drop <amount> <message>", value="Drop a random coin reward in chat.", inline=False)
        embed.add_field(name=f"{self.prefix}addmoney @user <amount>", value="Add money to a user’s balance. PREFIX ONLY.", inline=False)
        embed.add_field(name=f"{self.prefix}removemoney @user <amount>", value="Remove money from a user's balance. PREFIX ONLY.", inline=False)
        embed.set_footer(text="Economy - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="✨ Vanity", style=discord.ButtonStyle.secondary)
    async def vanity(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="✨ Vanity Commands", color=discord.Color.magenta())
        embed.add_field(name=f"{self.prefix}vanityroles @role #log <status>", value="Set up vanity roles.", inline=False)
        embed.add_field(name=f"{self.prefix}promoters", value="View vanity users.", inline=False)
        embed.add_field(name=f"{self.prefix}resetpromoters", value="Clear all vanity users.", inline=False)
        embed.set_footer(text="Vanity - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🎭 Roles", style=discord.ButtonStyle.secondary)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="✨ Vanity Commands", color=discord.Color.magenta())
        embed.add_field(name=f"{self.prefix}roleadd @role", value="Add a claimable role to /roles.", inline=False)
        embed.add_field(name=f"{self.prefix}roleremove @role", value="Remove a claimable role from /roles.", inline=False)
        embed.set_footer(text="Roles - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⚙️ Config", style=discord.ButtonStyle.secondary)
    async def config(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="⚙️ Config Commands", color=discord.Color.orange())
        embed.add_field(name=f"{self.prefix}configure", value="Open configuration setup menu.", inline=False)
        embed.add_field(name=f"{self.prefix}viewconfig", value="View current configuration settings.", inline=False)
        embed.add_field(name=f"{self.prefix}editconfig", value="Edit specific configuration values.", inline=False)
        embed.add_field(name=f"{self.prefix}resetconfig", value="Reset configuration values to default. PREFIX ONLY.", inline=False)
        embed.add_field(name=f"{self.prefix}setprefix <prefix>", value="Change the bot’s command prefix.", inline=False)
        embed.set_footer(text="Config - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📨 Invites", style=discord.ButtonStyle.secondary)
    async def invites(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="📨 Invite Commands", color=discord.Color.teal())
        embed.add_field(name=f"{self.prefix}invitechannel #channel", value="Set the invite logging channel.", inline=False)
        embed.add_field(name=f"{self.prefix}invites @user", value="Set the invite logging channel.", inline=False)
        embed.add_field(name=f"{self.prefix}removeinvites @user <amount>", value="Remove a specific number of invites from a user.", inline=False)
        embed.set_footer(text="Invites - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📦 Other", style=discord.ButtonStyle.secondary)
    async def other(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="📦 Other Staff Commands", color=discord.Color.light_gray())
        embed.add_field(name=f"{self.prefix}giveaway", value="Start a giveaway setup wizard.", inline=False)
        embed.add_field(name=f"{self.prefix}reroll <id>", value="Reroll a completed giveaway winner.", inline=False)
        embed.add_field(name=f"{self.prefix}disable <cmd/category>", value="Disable a specific command or category.", inline=False)
        embed.add_field(name=f"{self.prefix}enable <cmd/category>", value="Enable a disabled command or category.", inline=False)
        embed.add_field(name=f"{self.prefix}listdisabled", value="List all currently disabled commands.", inline=False)
        embed.add_field(name=f"{self.prefix}stop", value="Lock the bot, only custom prefix, only thetruck and server owner can unlock.", inline=False)
        embed.add_field(name=f"{self.prefix}testwelcome @user", value="Test welcome channel from a specified user. PREFIX ONLY.", inline=False)
        embed.add_field(name=f"{self.prefix}testboost @user", value="Test boost channel from a specified user. PREFIX ONLY.", inline=False)
        embed.add_field(name=f"{self.prefix}reactionrole <msg_id> <emoji> @role", value="Set up a reaction role.", inline=False)
        embed.add_field(name=f"{self.prefix}onetime #channel", value="Set up a one-time message channel. PREFIX ONLY.", inline=False)
        embed.add_field(name=f"{self.prefix}restore @user #channel", value="Restore messaging permissions for a user in a one-time channel. PREFIX ONLY.", inline=False)
        embed.add_field(name=f"{self.prefix}disableonetime #channel", value="Disable one-time message restrictions for a channel. PREFIX ONLY.", inline=False)
        embed.add_field(name=f"{self.prefix}performance", value="View staff performance analytics and statistics.", inline=False)
        embed.set_footer(text="Other - Staff Tools")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="↩️ Back", style=discord.ButtonStyle.danger)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🦆 DuckParadise Help Menu",
            description=(
                "Use the buttons below to navigate through command categories.\n\n"
                "💬 **General** - Normal commands for everyone\n"
                "💰 **Economy** - Fun currency commands\n"
                "🛠️ **Staff** - Admin & mod tools (staff only)"
            ),
            color=discord.Color.orange()
        )

        pages = getattr(interaction.client, "help_pages", None)
        if not pages:
            await interaction.response.send_message("❌ Help menu data not found. Please report this to staff or thetruck.", ephemeral=True)
            return

        doc = await settings_col.find_one({"guild": str(interaction.guild.id)})
        staff_role = interaction.guild.get_role(doc.get("staff_role")) if doc else None
        is_staff = staff_role in interaction.user.roles if staff_role else False

        view = CommandPages(pages, is_staff)
        await interaction.response.edit_message(embed=pages[0], view=view)

    async def handle_error(self, interaction, exception):
        try:
            await interaction.response.send_message(f"❌ An error occurred: `{type(exception).__name__}: {exception}`", ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(f"❌ An error occurred: `{type(exception).__name__}: {exception}`", ephemeral=True)
        except Exception:
            pass

class CommandPages(discord.ui.View):
    def __init__(self, embeds, is_staff: bool):
        super().__init__(timeout=300)
        self.embeds = embeds
        self.is_staff = is_staff
        self.current = 0
        self.sect = {0: "General", 1: "Economy"}
        if is_staff:
            staff_idx = next((i for i, e in enumerate(embeds) if e.title.startswith("🛠️")), None)
            if staff_idx is not None:
                self.sect[staff_idx] = "Staff"

        self.sect = {}
        for idx, embed in enumerate(self.embeds):
            if embed.title.startswith("💬") and "General" not in self.sect.values():
                self.sect[idx] = "General"
            elif embed.title.startswith("💰") and "Economy" not in self.sect.values():
                self.sect[idx] = "Economy"
            elif embed.title.startswith("🛠️") and self.is_staff and "Staff" not in self.sect.values():
                self.sect[idx] = "Staff"

    def get_section_bounds(self):
        starts = sorted(self.sect)
        idx = max(k for k in starts if k <= self.current)
        start = idx
        next_idx = [k for k in starts if k > idx]
        end = next_idx[0] if next_idx else len(self.embeds)
        return start, end

    def update_nav_buttons(self):
        for btn in (self.prev_button, self.next_button):
            if btn in self.children:
                self.remove_item(btn)
        start, end = self.get_section_bounds()
        if end - start <= 1:
            return
        if self.current > start:
            self.add_item(self.prev_button)
        if self.current < end - 1:
            self.add_item(self.next_button)

    @discord.ui.button(label="💬 General", style=discord.ButtonStyle.secondary)
    async def general(self, interaction: discord.Interaction, button: discord.ui.Button):
        general_idx = next((idx for idx, name in self.sect.items() if name == "General"), 0)
        self.current = general_idx
        self.update_nav_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)

    @discord.ui.button(label="💰 Economy", style=discord.ButtonStyle.success)
    async def economy(self, interaction: discord.Interaction, button: discord.ui.Button):
        econ_idx = next((i for i, e in enumerate(self.embeds) if e.title.startswith("💰")), None)
        if econ_idx is not None:
            self.current = econ_idx
            self.update_nav_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
        else:
            await interaction.response.send_message("❌ No economy pages found.", ephemeral=True)

    @discord.ui.button(label="🛠️ Staff", style=discord.ButtonStyle.danger)
    async def staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_staff:
            await interaction.response.send_message("❌ You don’t have permission to view staff commands.", ephemeral=True)
            return
        embed = discord.Embed(title="🛠️ Staff Command Sections", description="Select a category below to view its commands.", color=discord.Color.orange())
        await interaction.response.edit_message(embed=embed, view=StaffSections("?"))

    @discord.ui.button(label="⏮ Prev", style=discord.ButtonStyle.secondary, custom_id="prev_button_unique")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        start, _ = self.get_section_bounds()
        if self.current > start:
            self.current -= 1
            self.update_nav_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="⏭ Next", style=discord.ButtonStyle.secondary, custom_id="next_button_unique")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, end = self.get_section_bounds()
        if self.current < end - 1:
            self.current += 1
            self.update_nav_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
        else:
            await interaction.response.defer()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    async def handle_error(self, interaction, exception):
        try:
            await interaction.response.send_message(f"❌ An error occurred: `{type(exception).__name__}: {exception}`", ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(f"❌ An error occurred: `{type(exception).__name__}: {exception}`", ephemeral=True)
        except Exception:
            pass

bot.remove_command("help")
@bot.hybrid_command(name="help", description="View bot commands.", aliases=["commands", "cmds"])
async def help(ctx):
    doc = await settings_col.find_one({"guild": str(ctx.guild.id)})
    prefix = doc.get("prefix", "?") if doc else "?"
    staff_role = ctx.guild.get_role(doc.get("staff_role")) if doc else None
    is_staff = staff_role in ctx.author.roles if staff_role else False

    def format_field(name, value):
        return name.replace("?", prefix), value

    pages = []

    general_commands = [
        ("?cmds", "Show this help menu"),
        ("?serverinfo", "View server information"),
        ("?userinfo [@user]", "Detailed user info"),
        ("?afk [reason]", "Set your AFK status"),
        ("?duck", "Random picture of a duck"),
        ("?duckquiz", "Standardized Duck Quiz"),
        ("?slap", "Slap another user"),
        ("?duckfact", "Get a random duck fact"),
        ("?quote", "Get a random quote"),
        ("?roles", "Show claimable roles"),
        ("?invitechannel", "Set the channel where invite joins are announced."),
        ("?invites", "Check how many invites a user has."),
        ("?inviteleaderboard", "Show the top inviters in the server.")
    ]

    per_page = 10
    for i in range(0, len(general_commands), per_page):
        chunk = general_commands[i:i+per_page]
        general_embed = discord.Embed(
            title=f"💬 General Commands (Page {i//per_page + 1})",
            color=discord.Color.blurple()
        )
        for name, value in chunk:
            general_embed.add_field(
                name=format_field(name, value)[0],
                value=value,
                inline=False
            )
        pages.append(general_embed)

    economy_commands = [
        ("?balance / ?bal", "Check your balance"),
        ("?daily", "Claim your daily reward"),
        ("?work", "Work to earn coins"),
        ("?beg", "Beg for coins"),
        ("?deposit / ?dep <amount>", "Deposit to bank"),
        ("?withdraw / ?with <amount>", "Withdraw from bank"),
        ("?shop", "View the shop"),
        ("?buy <item>", "Buy an item from the shop"),
        ("?use <item>", "Use an item from your inventory"),
        ("?inventory / ?inv", "View your items"),
        ("?give / ?pay @user <amount>", "Give coins to another user"),
        ("?leaderboard / ?lb", "View the top users"),
        ("?coinflip / ?cf <amount>", "Coin flip for coins"),
        ("?fish", "Go fishing to earn coins"),
        ("?rob / ?steal @user", "Attempt to rob another user"),
        ("?lottery", "Join the lottery"),
        ("?choosejob", "Choose your dream job"),
        ("?jobstatus", "Check your next promotion"),
        ("?crime <bank/shoplift/payroll>", "Attempt a risky crime to earn coins"),
        ("?sell <item> <amount>", "Sell items from your inventory"),
        ("?invest", "Invest in fake companies for profit"),
        ("?investstatus", "Check your investments"),
        ("?hunt", "Go hunting for animals"),
        ("?mine", "Go mining for ores"),
        ("?doorgame", "Try your luck through multiple doors!"),
        ("?ducktowers", "Play a game of Duck Towers!"),
        ("?mines", "Play Mines and test your luck!"),
    ]

    for i in range(0, len(economy_commands), per_page):
        chunk = economy_commands[i:i+per_page]
        economy_embed = discord.Embed(
            title=f"💰 Economy Commands (Page {i//per_page + 1})",
            color=discord.Color.green()
        )
        for name, value in chunk:
            economy_embed.add_field(
                name=format_field(name, value)[0],
                value=value,
                inline=False
            )
        pages.append(economy_embed)

    view = CommandPages(pages, is_staff)
    ctx.bot.help_pages = pages
    await ctx.send(embed=pages[0], view=view)

@bot.command()
async def sync(ctx):
    try:
        print("✅ Manual command sync activated")
        synced = await asyncio.wait_for(bot.tree.sync(), timeout=30.0)
        await ctx.send(f"✅ Synced **{len(synced)}** global commands!")
    except asyncio.TimeoutError:
        await ctx.send("⚠️ Sync timed out - Discord may be rate limiting. Try again in a few minutes.")
    except discord.HTTPException as e:
        if e.status == 429:
            retry_after = e.retry_after if hasattr(e, 'retry_after') else 300
            await ctx.send(f"⚠️ Rate limited, wait {retry_after}s and try again.")
        else:
            await ctx.send(f"⚠️ Discord API error: `{e}`")
    except Exception as e:
        await ctx.send(f"⚠️ Sync failed: `{e}`")
        print(f"Sync error details: {type(e).__name__} - {e}")

@bot.command()
@staffperm("stopbot")
@staff_only()
async def stop(ctx):
    bot_locks[str(ctx.guild.id)] = True
    await ctx.send("🔒 Bot locked. Use 'override' by theofficialtruck or CuteBatak to unlock.")

@bot.command()
@staffperm("config")
@staff_only()
async def onetime(ctx, channel: discord.TextChannel = None):
    target_channel = channel or ctx.channel
    
    guild_id = str(ctx.guild.id)
    channel_id = str(target_channel.id)
    
    if guild_id not in onetime_channels:
        onetime_channels[guild_id] = {}
    
    if channel_id not in onetime_channels[guild_id]:
        onetime_channels[guild_id][channel_id] = {}
        
        await settings_col.update_one(
            {"guild": guild_id},
            {"$set": {f"onetime_channels.{channel_id}": {}}},
            upsert=True
        )
        
        embed = discord.Embed(
            title="✅ One-Time Message Channel Set Up",
            description=f"**{target_channel.mention}** is now a one-time message channel.\n\n"
                       "Non-staff members can send **only one message** in this channel. "
                       "After their first message, they will lose permission to send more messages.\n\n"
                       "Staff members are exempt and can continue messaging normally.\n\n"
                       "Use `.restore <user>` to give a user back their messaging permissions.",
            color=discord.Color.green()
        )
        
        await ctx.send(embed=embed)
        
        try:
            await target_channel.send(
                "🔔 **This is now a one-time message channel!**\n"
                "Non-staff members can send only one message here. "
                "Staff can restore permissions with `.restore <user>`."
            )
        except:
            pass
    else:
        await ctx.send(f"⚠️ {target_channel.mention} is already a one-time message channel.")

@bot.command()
@staffperm("config")
@staff_only()
async def restore(ctx, member: discord.Member, channel: discord.TextChannel = None):
    target_channel = channel or ctx.channel
    
    guild_id = str(ctx.guild.id)
    channel_id = str(target_channel.id)
    user_id = str(member.id)
    
    if (guild_id not in onetime_channels or 
        channel_id not in onetime_channels[guild_id]):
        return await ctx.send(f"⚠️ {target_channel.mention} is not a one-time message channel.")
    
    if user_id in onetime_channels[guild_id][channel_id]:
        del onetime_channels[guild_id][channel_id][user_id]
        
        await settings_col.update_one(
            {"guild": guild_id},
            {"$unset": {f"onetime_channels.{channel_id}.{user_id}": ""}}
        )
    
    try:
        await target_channel.set_permissions(
            member, 
            send_messages=None,
            reason="One-time message permission restored"
        )
        
        embed = discord.Embed(
            title="✅ Permissions Restored",
            description=f"{member.mention} can now send messages in {target_channel.mention} again.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Failed to restore permissions: `{e}`")

@bot.command()
@staffperm("config")
@staff_only()
async def disableonetime(ctx, channel: discord.TextChannel = None):
    target_channel = channel or ctx.channel
    
    guild_id = str(ctx.guild.id)
    channel_id = str(target_channel.id)
    
    if (guild_id not in onetime_channels or 
        channel_id not in onetime_channels[guild_id]):
        return await ctx.send(f"⚠️ {target_channel.mention} is not a one-time message channel.")
    
    del onetime_channels[guild_id][channel_id]
    
    await settings_col.update_one(
        {"guild": guild_id},
        {"$unset": {f"onetime_channels.{channel_id}": ""}}
    )
    
    try:
        for target, overwrite in target_channel.overwrites.items():
            if isinstance(target, discord.Member) and not await has_staff_role(target, target_channel.guild):
                if overwrite.send_messages is False:
                    await target_channel.set_permissions(target, send_messages=None)
        
        embed = discord.Embed(
            title="✅ One-Time Channel Disabled",
            description=f"{target_channel.mention} is no longer a one-time message channel.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Failed to disable one-time restrictions: `{e}`")

@bot.command()
async def override(ctx):
    if ctx.author.id == 1059882387590365314 or ctx.author.id == 903123014420406302:
        bot_locks[str(ctx.guild.id)] = False
        await ctx.send("🚀 Bot unlocked!")
    else:
        await ctx.send("❌ You don't have permission.")

if __name__ == "__main__":
    print("📊 Checking registered commands...")
    for cmd in bot.tree.walk_commands():
        print(f"📌 Registered command: {cmd.name}, guilds: {cmd._guild_ids}")
    print(f"📊 Total commands registered: {len(list(bot.tree.walk_commands()))}")
    
    @bot.hybrid_command(name="test", description="Test command for debugging")
    async def test(ctx):
        await ctx.send("✅ Test command works!")
    
    print("Starting bot...")
    bot.run(TOKEN)
