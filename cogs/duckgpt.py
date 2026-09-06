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

"""DuckGPT AI chat (Google Gemini with key rotation), the @mention trigger, and conversation cleanup."""

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone
from itertools import cycle

from discord.ext import commands, tasks

from core import config as cfg
from core import permsHelperFuncs as perms
from core import state

# Google Gemini AI SDK. The newer google.genai package is preferred.
# If it is not installed the bot falls back to the older google.generativeai package,
# which is attempted later only when an AI response is actually needed.
try:
    import google.genai as genai_new
except Exception:
    genai_new = None
genai_old = None


# In memory conversation history per user, keyed by user ID
duck_conversations = {}


# Timestamp of each user's last DuckGPT request, used for per user rate limiting
_duckgpt_last_used: dict[int, float] = {}


_DUCKGPT_COOLDOWN_SECONDS = 5


# System prompt defining DuckGPT's personality and response rules
SYSTEM_PROMPT = f"You are DuckGPT a knowledgeable talking duck created by '{cfg.BOT_ADMIN_NAME}'. You can answer real questions in a SHORT, clear, and funny way while staying in duck character.If the user is named '{cfg.BOT_ADMIN_NAME}', NEVER EVER EVER EVER say 'my creator is {cfg.BOT_ADMIN_NAME}' or repeat that fact, just talk LIKE A NORMAL HUMAN EVEN THOUGH YOU ARENT. Always keep your reply to one sentence, humorous if possible, ending with one quack sound like 'Quack!' YOU CAN DO OTHERS PLEASE PLEASE PLEASE DONT STICK TO JUST QUACK. Never add blank lines or paragraphs. Never say things like 'you told me your name' or 'you didn't tell me your name'. If asked any kind of questions, give a short and accurate summary as a talking duck. If greeted, you can greet back naturally, but DONT YOU DARE repeat the full intro every time. Your name is PatosX when requested for your name MAKE SURE TO RESPOND WITH PatosX. Always speak in first person as if the user is talking directly to you, not anyone else."


async def cleanup_old_conversations():
    """Remove DuckGPT conversation histories that have not been updated in 30 days."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        result = await state.duck_conversations_col.delete_many({"last_updated": {"$lt": cutoff}})
        print(f"[DuckGPT Cleanup] Deleted {result.deleted_count} old conversations.")
        return result.deleted_count
    except Exception as e:
        print(f"[DuckGPT Cleanup Error] {e}")
        return 0


# Tracks the currently active Gemini key for logging purposes
active_key = None


# Infinite cycle over the available Gemini keys for round-robin rotation
GEMINI_KEY_CYCLE = cycle(cfg.GEMINI_API_KEYS)


def next_gemini_key():
    """Advance to the next Gemini API key in the round robin cycle and return it."""
    global active_key
    active_key = next(GEMINI_KEY_CYCLE)
    print(f"[GEMINI] Rotating to key: {active_key[:12]}... (total keys: {len(cfg.GEMINI_API_KEYS)})")
    return active_key


def build_gemini_client_for_key(key: str, model_name: str):
    """Return a client info dict for key and model_name.
    Prefers the newer google.genai SDK, falling back to google.generativeai if unavailable."""
    if genai_new is not None and hasattr(genai_new, "Client"):
        client = genai_new.Client(api_key=key)
        return {"mode": "new", "client": client, "model": model_name}
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
        genai_old.configure(api_key=key)
        model = genai_old.GenerativeModel(model_name)
        return {"mode": "old", "model": model}


def gemini_generate_once(client_info, prompt: str):
    """Call the Gemini API synchronously using the appropriate SDK mode.
    Designed to be run inside a ThreadPoolExecutor so it does not block the event loop."""
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


GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"


# Error substrings that mean "this specific key is out of quota / invalid" - worth
# rotating to the next key for. Distinct from _is_transient_gemini_error below,
# which covers blips that are worth retrying on the *same* key.
_QUOTA_ERROR_MARKERS = ("429", "quota", "api key not valid", "exceeded")


# Error substrings for transient failures (brief outages, timeouts, rate limiting
# at the infra level) that are worth retrying rather than giving up immediately.
# Giving up on the first attempt for these is what caused DuckGPT to intermittently
# fail with "the duck slipped on a banana peel" even though the very next query -
# a fresh attempt against the same flaky backend - would succeed.
_TRANSIENT_ERROR_MARKERS = (
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "deadline",
    "unavailable",
    "connection",
    "reset by peer",
    "temporarily",
    "internal error",
)


async def get_gemini_client():
    """Cycle through available Gemini keys and return the first working client info dict.
    Returns None when all keys fail."""
    for _ in range(len(cfg.GEMINI_API_KEYS)):
        key = next_gemini_key()
        try:
            client_info = build_gemini_client_for_key(key, GEMINI_MODEL_NAME)
            return client_info
        except Exception as e:
            print(f"❌ Gemini key {key[:8]} failed: {e}")
            continue
    print("❌ No working Gemini API keys found.")
    return None


async def generate_gemini_response(messages):
    """Convert messages list to a flat prompt string, then call Gemini with automatic key rotation
    and exponential back off. Falls back to a duck themed failure message when all keys are exhausted."""
    loop = asyncio.get_event_loop()
    prompt = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in messages)
    client_info = await get_gemini_client()
    if not client_info:
        return "🦆 The duck slipped on a banana peel and can't respond right now."
    max_attempts = max(len(cfg.GEMINI_API_KEYS), 1) + 2
    for attempt in range(max_attempts):
        try:
            response = await loop.run_in_executor(
                state.executor, lambda ci=client_info: gemini_generate_once(ci, prompt)
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
            lower_err = err_str.lower()
            is_quota_error = any(word in lower_err for word in _QUOTA_ERROR_MARKERS)
            is_transient_error = any(word in lower_err for word in _TRANSIENT_ERROR_MARKERS)
            if not (is_quota_error or is_transient_error) or attempt == max_attempts - 1:
                print("💥 Non-recoverable Gemini error (or out of retries), stopping attempts.")
                break
            delay = 2**attempt + random.uniform(0, 1)
            if is_quota_error:
                print("⚠️ Gemini key hit limit or failed, switching key...")
                print(f"🕒 Waiting {delay:.1f}s before switching...")
                await asyncio.sleep(delay)
                new_key = next_gemini_key()
                try:
                    client_info = build_gemini_client_for_key(new_key, GEMINI_MODEL_NAME)
                except Exception as e2:
                    print(f"❌ Failed to switch Gemini key: {e2}")
            else:
                print(f"🕒 Transient Gemini error, retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
    print("❌ All Gemini attempts failed.")
    return "🦆 The duck slipped on a banana peel and can't respond right now."


async def ask_duck_gpt(ctx, prompt: str) -> str:
    """Main entry point for a DuckGPT request. Enforces per user cooldown, loads and saves
    conversation history from MongoDB, prepends the SYSTEM_PROMPT, then calls generate_gemini_response."""
    if not ctx.guild:
        return "🦆 I can only assist you in servers, not in DMs!"
    guild_id = str(ctx.guild.id)
    guild_name = ctx.guild.name
    user_id = str(ctx.author.id)
    display_name = ctx.author.display_name
    config = await state.config_col.find_one({"guild": guild_id}) or {}
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
    conv_key = f"{guild_id}-{user_id}"
    if conv_key not in duck_conversations:
        record = await state.duck_conversations_col.find_one({"user_id": user_id, "guild_id": guild_id})
        if record and "messages" in record:
            duck_conversations[conv_key] = record["messages"]
            greeted = False
        else:
            duck_conversations[conv_key] = []
            greeted = False
    else:
        greeted = True
    lowered_prompt = prompt.lower()
    greetings = ["hi", "hello", "hey", "yo", "hiya", "sup", "greetings"]
    if any(word in lowered_prompt.split() for word in greetings):
        duck_conversations[conv_key] = []
        greeted = False
    duck_conversations[conv_key].append({"role": "user", "content": f"{display_name} said: {prompt}"})
    total_tokens = sum(len(msg["content"].split()) * 4 for msg in duck_conversations[conv_key])
    if total_tokens > 1500:
        duck_conversations[conv_key] = [{"role": "user", "content": prompt}]
    ai_task_keywords = [
        "do my homework",
        "solve this math",
        "write this code",
        "can you code",
        "generate art",
        "make ai art",
        "draw me",
        "write an essay",
        "make it",
        "create it",
    ]
    if any(phrase in prompt.lower() for phrase in ai_task_keywords):
        await perms.log_action(
            ctx, f"⚠️ Attempted AI misuse: `{prompt}`", user_id=ctx.author.id, action_type="duckgpt_flag"
        )
        return "🦆 I'm just a talking duck! I can't do things for you."

    async def detect_duck_intent(prompt: str) -> str:
        intent_prompt = f'\nAnalyze this message and decide what the user is asking:\n- If they ask about your creator/owner, respond "owner".\n- If they ask their name, respond "name".\n- If they ask the server, respond "server".\n- If they ask member count, respond "members".\n- Otherwise respond "none".\nMessage: "{prompt}"\nOnly return one word: owner, name, server, members, or none.\n'
        client_info = await get_gemini_client()
        if not client_info:
            return "none"
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                state.executor, lambda: gemini_generate_once(client_info, intent_prompt)
            )
            return response.text.strip().lower() if hasattr(response, "text") else "none"
        except Exception as e:
            print(f"[DuckGPT detect intent error] {e}")
            return "none"

    intent = await detect_duck_intent(prompt)
    if intent == "owner":
        if ctx.author.id in cfg.AUTHORIZED_USER_IDS:
            return "🦆 You are my owner! Quack!"
        elif display_name.lower() == cfg.BOT_ADMIN_NAME.lower():
            return "🦆 You may *look* like my owner, but you're not the real one! Bad duck! *angry quack!* 🦆"
        else:
            return f"🦆 My owner is {cfg.BOT_ADMIN_NAME}! Quack!"
    elif intent == "name":
        return f"🦆 Your name is `{display_name}`! Quack!"
    elif intent == "server":
        return f"🦆 You're in `{guild_name}`! Quack!"
    elif intent == "members":
        return f"🦆 There are `{ctx.guild.member_count}` members in `{guild_name}`! Quack!"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + duck_conversations[conv_key]
    response_text = await generate_gemini_response(messages)
    if not response_text:
        text = "🦆 The duck slipped on a banana peel and can't respond right now."
    else:
        text = response_text
    duck_conversations[conv_key].append({"role": "assistant", "content": text})
    await state.duck_conversations_col.update_one(
        {"user_id": user_id, "guild_id": guild_id},
        {"$set": {"messages": duck_conversations[conv_key], "last_updated": datetime.now(timezone.utc)}},
        upsert=True,
    )
    text = " ".join(text.split())
    if not greeted:
        return f"🦆 Quack! Hello {display_name}! I remember you from {guild_name}! {text}"
    else:
        return f"🦆 {text}"


class DuckGPT(commands.Cog):
    """DuckGPT AI chat (Google Gemini with key rotation), the @mention trigger, and conversation cleanup."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    @tasks.loop(hours=24)
    async def periodic_cleanup(self):
        """Daily maintenance: remove stale DuckGPT conversation histories from MongoDB."""
        deleted = await cleanup_old_conversations()
        print(f"[DuckGPT] Cleanup complete: {deleted} old conversations removed.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the daily conversation-history cleanup loop (runs once)."""
        if self._ready_done:
            return
        self._ready_done = True
        self.periodic_cleanup.start()

    @commands.Cog.listener()
    async def on_message(self, message):
        """Reply as DuckGPT when the bot is mentioned, with a short per-user cooldown."""
        if message.author.bot:
            return
        if not message.guild:
            return
        if self.bot.user in message.mentions:
            if await perms.is_category_disabled(message.guild.id, "duckgpt"):
                return
            _now = time.time()
            _last = _duckgpt_last_used.get(message.author.id, 0)
            if _now - _last < _DUCKGPT_COOLDOWN_SECONDS:
                return
            _duckgpt_last_used[message.author.id] = _now
            prompt = message.clean_content.replace(f"<@{self.bot.user.id}>", "").strip()
            if not prompt:
                prompt = "Quack!"
            ctx = await self.bot.get_context(message)
            if not ctx.guild:
                return await message.reply(" I can only assist you in servers, not in DMs!")
            await message.channel.typing()
            reply = await ask_duck_gpt(ctx, prompt)
            await message.reply(reply)

    def cog_unload(self):
        """Cancel the cleanup loop this cog owns."""
        if self.periodic_cleanup.is_running():
            self.periodic_cleanup.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DuckGPT(bot))
