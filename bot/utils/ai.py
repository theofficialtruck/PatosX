"""Gemini-backed AI helpers used by the DuckGPT cog and on_message hook.

Keeping the SDK juggling here means the rest of the bot never has to know
whether ``google.genai`` (new SDK) or ``google.generativeai`` (legacy SDK)
is installed.
"""

from __future__ import annotations

import asyncio
import random
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from itertools import cycle
from typing import Any

from bot.config.constants import DUCKGPT_SYSTEM_PROMPT
from bot.config.secrets import GEMINI_API_KEYS
from bot.database import config_col, duck_conversations_col

try:
    import google.genai as _genai_new  # type: ignore
except Exception:  # pragma: no cover - SDK optional
    _genai_new = None

_genai_old = None
_executor = ThreadPoolExecutor()
_active_key: str | None = None
_GEMINI_KEY_CYCLE = cycle(GEMINI_API_KEYS) if GEMINI_API_KEYS else None

# In-memory rolling window of conversation history per Discord user.
duck_conversations: dict[str, list[dict[str, str]]] = {}


def _next_gemini_key() -> str:
    """Round-robin through the configured Gemini keys."""
    global _active_key
    if _GEMINI_KEY_CYCLE is None:
        raise RuntimeError("No Gemini API keys configured")
    _active_key = next(_GEMINI_KEY_CYCLE)
    return _active_key


def _build_gemini_client_for_key(key: str, model_name: str) -> dict[str, Any]:
    """Build a client wrapper around whichever Gemini SDK is available."""
    global _genai_old
    if _genai_new is not None and hasattr(_genai_new, "Client"):
        client = _genai_new.Client(api_key=key)
        return {"mode": "new", "client": client, "model": model_name}

    if _genai_old is None:
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=FutureWarning)
                import google.generativeai as _genai_old  # type: ignore
        except Exception:
            _genai_old = None  # type: ignore[assignment]

    if _genai_old is None:
        raise RuntimeError(
            "No Gemini SDK available. Install google-genai or google-generativeai."
        )

    _genai_old.configure(api_key=key)
    model = _genai_old.GenerativeModel(model_name)
    return {"mode": "old", "model": model}


def _gemini_generate_once(client_info: dict[str, Any], prompt: str):
    """Single synchronous call into whichever SDK is active."""
    if client_info["mode"] == "new":
        client = client_info["client"]
        model = client_info["model"]
        try:
            return client.models.generate_content(model=model, contents=prompt)
        except Exception:
            if hasattr(client, "responses"):
                return client.responses.generate(model=model, contents=prompt)
            raise

    return client_info["model"].generate_content(prompt)


async def _get_gemini_client():
    """Return a working client for *any* configured key, rotating as needed."""
    if not GEMINI_API_KEYS:
        return None

    for _ in range(len(GEMINI_API_KEYS)):
        key = _next_gemini_key()
        try:
            return _build_gemini_client_for_key(key, "gemini-2.5-flash-lite")
        except Exception as exc:
            print(f"❌ Gemini key {key[:8]} failed: {exc}")
            continue

    print("❌ No working Gemini API keys found.")
    return None


async def generate_gemini_response(messages: list[dict[str, str]]) -> str:
    """Generate a single Gemini reply, rotating keys on quota failures."""
    loop = asyncio.get_event_loop()
    prompt = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}" for m in messages
    )

    client_info = await _get_gemini_client()
    if not client_info:
        return "🦆 The duck slipped on a banana peel and can’t respond right now."

    for attempt in range(len(GEMINI_API_KEYS)):
        try:
            response = await loop.run_in_executor(
                _executor, lambda: _gemini_generate_once(client_info, prompt)
            )
            if hasattr(response, "text") and response.text:
                return response.text.strip()
            if isinstance(response, str):
                return response.strip()
            return "🦆 The duck was thinking too hard and forgot what it was going to say."
        except Exception as exc:
            err_str = str(exc)
            print(f"[DuckGPT Gemini Error] {err_str}")

            if any(
                w in err_str.lower()
                for w in ["429", "quota", "api key not valid", "exceeded"]
            ):
                print("⚠️ Gemini key hit limit or failed, switching key...")
                delay = 2 ** attempt + random.uniform(0, 1)
                print(f"🕒 Waiting {delay:.1f}s before switching...")
                await asyncio.sleep(delay)
                new_key = _next_gemini_key()
                try:
                    client_info = _build_gemini_client_for_key(
                        new_key, "gemini-2.0-flash"
                    )
                except Exception as exc2:
                    print(f"❌ Failed to switch Gemini key: {exc2}")
                    continue
                continue

            print("💥 Non-recoverable Gemini error, stopping attempts.")
            break

    print("❌ All Gemini keys failed.")
    return "🦆 The duck slipped on a banana peel and can’t respond right now."


async def _detect_duck_intent(prompt: str) -> str:
    """Lightweight intent classifier for canned responses (owner/name/etc.)."""
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
    client_info = await _get_gemini_client()
    if not client_info:
        return "none"

    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            _executor, lambda: _gemini_generate_once(client_info, intent_prompt)
        )
        return response.text.strip().lower() if hasattr(response, "text") else "none"
    except Exception as exc:
        print(f"[DuckGPT detect intent error] {exc}")
        return "none"


async def cleanup_old_conversations() -> int:
    """Drop conversation rows older than 30 days. Returns the deleted count."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        result = await duck_conversations_col.delete_many(
            {"last_updated": {"$lt": cutoff}}
        )
        print(
            f"[DuckGPT Cleanup] Deleted {result.deleted_count} old conversations."
        )
        return result.deleted_count
    except Exception as exc:
        print(f"[DuckGPT Cleanup Error] {exc}")
        return 0


async def ask_duck_gpt(ctx, prompt: str) -> str:
    """Public entry point used by the on_message handler."""
    from bot.config.constants import THETRUCK_ID
    from bot.utils.logging import log_action

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
        mention = (
            f"<#{allowed_channels[0]}>"
            if allowed_channels
            else "`a DuckGPT channel`"
        )
        return f"🦆 Please use this command in {mention}!"

    if user_id not in duck_conversations:
        record = await duck_conversations_col.find_one(
            {"user_id": user_id, "guild_id": guild_id}
        )
        if record and "messages" in record:
            duck_conversations[user_id] = record["messages"]
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

    duck_conversations[user_id].append(
        {"role": "user", "content": f"{display_name} said: {prompt}"}
    )

    total_tokens = sum(
        len(m["content"].split()) * 4 for m in duck_conversations[user_id]
    )
    if total_tokens > 1500:
        duck_conversations[user_id] = [{"role": "user", "content": prompt}]

    ai_task_keywords = [
        "do my homework", "solve this math", "write this code", "can you code",
        "generate art", "make ai art", "draw me", "write an essay",
        "make it", "create it",
    ]
    if any(phrase in prompt.lower() for phrase in ai_task_keywords):
        await log_action(
            ctx,
            f"⚠️ Attempted AI misuse: `{prompt}`",
            user_id=ctx.author.id,
            action_type="duckgpt_flag",
        )
        return "🦆 I'm just a talking duck! I can't do things for you."

    intent = await _detect_duck_intent(prompt)
    if intent == "owner":
        if ctx.author.id == THETRUCK_ID:
            return "🦆 You are my owner! Quack!"
        if display_name.lower() == "thetruck":
            return "🦆 You may *look* like my owner, but you’re not the real one! Bad duck! *angry quack!* 🦆"
        return "🦆 My owner is thetruck! Quack!"
    if intent == "name":
        return f"🦆 Your name is `{display_name}`! Quack!"
    if intent == "server":
        return f"🦆 You’re in `{guild_name}`! Quack!"
    if intent == "members":
        return f"🦆 There are `{ctx.guild.member_count}` members in `{guild_name}`! Quack!"

    messages = [{"role": "system", "content": DUCKGPT_SYSTEM_PROMPT}] + duck_conversations[user_id]
    response_text = await generate_gemini_response(messages)
    text = response_text or "🦆 The duck slipped on a banana peel and can’t respond right now."

    duck_conversations[user_id].append({"role": "assistant", "content": text})
    await duck_conversations_col.update_one(
        {"user_id": user_id, "guild_id": guild_id},
        {
            "$set": {
                "messages": duck_conversations[user_id],
                "last_updated": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )

    text = " ".join(text.split())
    if not greeted:
        return f"🦆 Quack! Hello {display_name}! I remember you from {guild_name}! {text}"
    return f"🦆 {text}"


__all__ = [
    "duck_conversations",
    "generate_gemini_response",
    "ask_duck_gpt",
    "cleanup_old_conversations",
]
