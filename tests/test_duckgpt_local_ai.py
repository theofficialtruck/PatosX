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

"""DuckGPT's local AI model (primary) and the Gemini fallback. The local model is a real aiohttp server
on a random localhost port, so these tests exercise the actual HTTP request and response handling."""

import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web

from cogs import duckgpt
from core import config as cfg
from core import state

MESSAGES = [
    {"role": "system", "content": duckgpt.SYSTEM_PROMPT},
    {"role": "user", "content": "Tester said: what is 847 * 23?"},
]


@pytest.fixture
async def http_session(monkeypatch):
    """A session of our own, closed after the test, instead of the bot's shared one."""
    session = aiohttp.ClientSession()
    monkeypatch.setattr(state, "http_session", lambda: session)
    yield session
    await session.close()


@pytest.fixture
async def local_ai(monkeypatch, http_session):
    """Start a fake local AI server and point DuckGPT at it. ``server.reply`` decides what /chat does:
    a dict (sent as JSON), a (status, body) tuple, or a coroutine function run before answering."""
    server = SimpleNamespace(requests=[], reply={"answer": "Quack! It's 19481.", "ms": 12, "model": "chatbot-fast"})

    async def chat(request):
        server.requests.append(await request.json())
        reply = server.reply
        if callable(reply):
            reply = await reply()
        if isinstance(reply, tuple):
            status, body = reply
            return web.Response(status=status, text=body, content_type="application/json")
        return web.json_response(reply)

    app = web.Application()
    app.router.add_post("/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(cfg, "LOCAL_AI_ENABLED", True)
    monkeypatch.setattr(cfg, "LOCAL_AI_URL", f"http://127.0.0.1:{port}/chat")
    monkeypatch.setattr(cfg, "LOCAL_AI_TIMEOUT_SECONDS", 5.0)
    yield server
    await runner.cleanup()


@pytest.fixture
def gemini(monkeypatch):
    """The Gemini fallback, mocked."""
    mock = AsyncMock(return_value="Gemini says quack!")
    monkeypatch.setattr(duckgpt, "generate_gemini_response", mock)
    return mock


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# === the local model is the primary backend =====================================================


@pytest.mark.asyncio
async def test_local_model_answers_and_gemini_is_not_called(local_ai, gemini):
    reply = await duckgpt.generate_duck_response(MESSAGES)
    assert reply == "Quack! It's 19481."
    gemini.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_model_gets_the_same_prompt_and_structure_as_gemini(local_ai, gemini):
    await duckgpt.generate_duck_response(MESSAGES)
    assert local_ai.requests == [{"prompt": duckgpt.build_prompt(MESSAGES)}]
    prompt = local_ai.requests[0]["prompt"]
    assert prompt.startswith(f"System: {duckgpt.SYSTEM_PROMPT}\n")
    assert prompt.endswith("User: Tester said: what is 847 * 23?")


@pytest.mark.asyncio
async def test_gemini_still_gets_the_identical_flat_prompt(monkeypatch):
    """generate_gemini_response builds its prompt with the same build_prompt helper."""
    seen = []
    monkeypatch.setattr(cfg, "GEMINI_API_KEYS", ["key1"])
    monkeypatch.setattr(duckgpt, "get_gemini_client", AsyncMock(return_value={"mode": "new"}))

    def fake_generate_once(client_info, prompt):
        seen.append(prompt)
        return SimpleNamespace(text="That is a fine question, quack!")

    monkeypatch.setattr(duckgpt, "gemini_generate_once", fake_generate_once)
    await duckgpt.generate_duck_response(MESSAGES)  # local model is off in tests by default
    assert seen == [duckgpt.build_prompt(MESSAGES)]


# === falling back to Gemini =====================================================================


@pytest.mark.asyncio
async def test_falls_back_to_gemini_when_the_local_server_is_not_running(monkeypatch, http_session, gemini):
    monkeypatch.setattr(cfg, "LOCAL_AI_ENABLED", True)
    monkeypatch.setattr(cfg, "LOCAL_AI_URL", f"http://127.0.0.1:{free_port()}/chat")
    assert await duckgpt.generate_duck_response(MESSAGES) == "Gemini says quack!"
    gemini.assert_awaited_once_with(MESSAGES)


@pytest.mark.parametrize(
    "reply",
    [
        (500, '{"detail": "model crashed"}'),
        (404, '{"detail": "Not Found"}'),
        (200, "this is not json"),
        {"answer": ""},
        {"answer": "   "},
        {"answer": None},
        {"error": "no answer field"},
        ["a", "list"],
    ],
)
@pytest.mark.asyncio
async def test_falls_back_to_gemini_on_a_bad_local_reply(local_ai, gemini, reply):
    local_ai.reply = reply
    assert await duckgpt.generate_duck_response(MESSAGES) == "Gemini says quack!"
    gemini.assert_awaited_once()


@pytest.mark.asyncio
async def test_falls_back_to_gemini_when_the_local_model_is_too_slow(monkeypatch, local_ai, gemini):
    async def slow():
        await asyncio.sleep(2)
        return {"answer": "too late"}

    local_ai.reply = slow
    monkeypatch.setattr(cfg, "LOCAL_AI_TIMEOUT_SECONDS", 0.2)
    assert await duckgpt.generate_duck_response(MESSAGES) == "Gemini says quack!"


@pytest.mark.asyncio
async def test_disabled_local_model_is_never_called(monkeypatch, local_ai, gemini):
    monkeypatch.setattr(cfg, "LOCAL_AI_ENABLED", False)
    assert await duckgpt.generate_duck_response(MESSAGES) == "Gemini says quack!"
    assert local_ai.requests == []


@pytest.mark.asyncio
async def test_a_failed_local_model_is_skipped_for_a_while_then_retried(monkeypatch, local_ai, gemini):
    clock = [1000.0]
    monkeypatch.setattr(duckgpt.time, "monotonic", lambda: clock[0])
    local_ai.reply = (503, "{}")
    await duckgpt.generate_duck_response(MESSAGES)
    assert len(local_ai.requests) == 1

    local_ai.reply = {"answer": "I am back online, quack!"}
    clock[0] += cfg.LOCAL_AI_RETRY_AFTER_SECONDS - 1
    assert await duckgpt.generate_duck_response(MESSAGES) == "Gemini says quack!"  # still skipped
    assert len(local_ai.requests) == 1

    clock[0] += 2
    assert await duckgpt.generate_duck_response(MESSAGES) == "I am back online, quack!"  # retried after the wait
    assert len(local_ai.requests) == 2
    assert gemini.await_count == 2


# === the whole DuckGPT flow =====================================================================


@pytest.fixture
def duck_ctx(monkeypatch):
    duckgpt.duck_conversations.clear()
    monkeypatch.setattr(state, "config_col", SimpleNamespace(find_one=AsyncMock(return_value={})))
    monkeypatch.setattr(
        state,
        "duck_conversations_col",
        SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock()),
    )
    guild = SimpleNamespace(id=1, name="Pond", member_count=5, get_member=lambda _id: None, get_role=lambda _id: None)
    yield SimpleNamespace(
        guild=guild,
        author=SimpleNamespace(id=2, display_name="Tester"),
        channel=SimpleNamespace(id=3),
    )
    duckgpt.duck_conversations.clear()


@pytest.mark.asyncio
async def test_full_request_uses_the_local_model_for_intent_and_reply(local_ai, gemini, monkeypatch, duck_ctx):
    gemini_client = AsyncMock(return_value=None)
    monkeypatch.setattr(duckgpt, "get_gemini_client", gemini_client)
    replies = iter([{"answer": "none"}, {"answer": "It's 19481, quack!"}])

    async def next_reply():
        return next(replies)

    local_ai.reply = next_reply
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "what is 847 * 23?")

    assert reply.endswith("It's 19481, quack!")
    assert len(local_ai.requests) == 2
    assert "Only return one word" in local_ai.requests[0]["prompt"]  # the intent check
    assert local_ai.requests[1]["prompt"].startswith(f"System: {duckgpt.SYSTEM_PROMPT}")
    gemini.assert_not_awaited()
    gemini_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_model_intent_answer_drives_the_canned_replies(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": '"Server".'}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "which server is this?")
    assert "`Pond`" in reply
    gemini.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_request_falls_back_to_gemini_when_the_local_model_is_down(
    monkeypatch, http_session, gemini, duck_ctx
):
    monkeypatch.setattr(cfg, "LOCAL_AI_ENABLED", True)
    monkeypatch.setattr(cfg, "LOCAL_AI_URL", f"http://127.0.0.1:{free_port()}/chat")
    monkeypatch.setattr(duckgpt, "get_gemini_client", AsyncMock(return_value=None))  # Gemini intent -> "none"
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "tell me a joke")
    assert reply.endswith("Gemini says quack!")
    gemini.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_model_output_is_sanitised_like_gemini_output(local_ai, gemini, monkeypatch, duck_ctx):
    monkeypatch.setattr(duckgpt, "get_gemini_client", AsyncMock(return_value=None))
    replies = iter([{"answer": "none"}, {"answer": "Hey @everyone and <@123>, quack!"}])

    async def next_reply():
        return next(replies)

    local_ai.reply = next_reply
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "ping everyone")
    assert "@everyone" not in reply
    assert "<@123>" not in reply


# === intent parsing =============================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("owner", "owner"),
        ("Owner", "owner"),
        ('"members".', "members"),
        ("  name\n", "name"),
        ("none", "none"),
        ("The user wants the owner", "none"),
        ("", "none"),
        (None, "none"),
    ],
)
def test_normalize_intent(raw, expected):
    assert duckgpt.normalize_intent(raw) == expected


# === configuration ==============================================================================


@pytest.mark.parametrize("raw,expected", [("12.5", 12.5), ("abc", 30.0), ("-4", 30.0), ("0", 30.0), ("", 30.0)])
def test_local_ai_timeout_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("LOCAL_AI_TIMEOUT_SECONDS", raw)
    assert cfg._env_float("LOCAL_AI_TIMEOUT_SECONDS", 30.0) == expected


def test_gemini_keys_are_optional_while_the_local_model_is_on():
    assert "GEMINI_API_KEYS" not in cfg.required_keys


# === replies are always at least one full sentence ==============================================


@pytest.mark.parametrize(
    "text",
    [
        "It's 19481, quack!",
        "Paris is the capital of France. Quack!",
        "Is that really what you think?",
        "I'm PatosX, quack! 🦆",
        'He said "honk honk, quack!"',
        "Well, this duck is not sure…",
    ],
)
def test_full_sentences_are_accepted(text):
    assert duckgpt.is_full_sentence(text)


@pytest.mark.parametrize(
    "text",
    ["19481", "Quack!", "Yes.", "Paris", "Yes, quack!", "🦆🦆🦆", "", "   ", None, "Paris is the capital of France"],
)
def test_one_word_answers_and_fragments_are_rejected(text):
    assert not duckgpt.is_full_sentence(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("19481", "My answer is 19481, quack!"),
        ("Paris.", "My answer is Paris, quack!"),
        ("Yes, quack! 🦆", "My answer is Yes, quack, quack!"),
        ("Paris is the capital of France", "Paris is the capital of France."),
        ("", "🦆 The duck lost its words, quack!"),
        ("🦆", "🦆 The duck lost its words, quack!"),
        ("It's 19481, quack!", "It's 19481, quack!"),
    ],
)
def test_ensure_full_sentence(text, expected):
    result = duckgpt.ensure_full_sentence(text)
    assert result == expected
    assert duckgpt.is_full_sentence(result)


def test_system_prompt_asks_for_full_sentences():
    assert "NEVER reply with a single word" in duckgpt.SYSTEM_PROMPT
    assert "at least one complete sentence" in duckgpt.SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_one_word_local_answer_is_asked_again_with_a_reminder(local_ai, gemini):
    replies = iter([{"answer": "19481"}, {"answer": "That's 19481, my feathered friend, quack!"}])

    async def next_reply():
        return next(replies)

    local_ai.reply = next_reply
    reply = await duckgpt.generate_duck_response(MESSAGES)

    assert reply == "That's 19481, my feathered friend, quack!"
    assert len(local_ai.requests) == 2
    retry_prompt = local_ai.requests[1]["prompt"]
    assert retry_prompt.startswith(duckgpt.build_prompt(MESSAGES))  # same conversation, then the reminder
    assert retry_prompt.endswith(f"Assistant: 19481\nSystem: {duckgpt.FULL_SENTENCE_REMINDER}")
    gemini.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_word_gemini_answer_is_asked_again_too(monkeypatch):
    gemini = AsyncMock(side_effect=["Yes.", "Yes, this duck absolutely agrees with you!"])
    monkeypatch.setattr(duckgpt, "generate_gemini_response", gemini)

    assert await duckgpt.generate_duck_response(MESSAGES) == "Yes, this duck absolutely agrees with you!"
    assert gemini.await_count == 2
    assert gemini.await_args.args[0][-1] == {"role": "system", "content": duckgpt.FULL_SENTENCE_REMINDER}


@pytest.mark.asyncio
async def test_still_too_short_after_asking_again_becomes_a_sentence(local_ai, gemini):
    local_ai.reply = {"answer": "19481"}
    reply = await duckgpt.generate_duck_response(MESSAGES)
    assert reply == "My answer is 19481, quack!"
    assert duckgpt.is_full_sentence(reply)
    assert len(local_ai.requests) == 2  # asked once more, never in a loop


@pytest.mark.asyncio
async def test_full_sentence_reply_is_not_asked_again(local_ai, gemini):
    await duckgpt.generate_duck_response(MESSAGES)
    assert len(local_ai.requests) == 1


@pytest.mark.asyncio
async def test_intent_check_still_gets_its_one_word_answer(local_ai, gemini, monkeypatch, duck_ctx):
    """The rule is only for replies users see: the internal owner/name/server check still works on a
    single word and is never asked again."""
    local_ai.reply = {"answer": "members"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "how many members are here?")
    assert "`5` members" in reply
    assert len(local_ai.requests) == 1


@pytest.mark.asyncio
async def test_full_flow_never_sends_a_one_word_reply(local_ai, gemini, monkeypatch, duck_ctx):
    replies = iter([{"answer": "none"}, {"answer": "Quack!"}, {"answer": "Quack!"}])

    async def next_reply():
        return next(replies)

    local_ai.reply = next_reply
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "say something")
    model_part = reply.split("I remember you from Pond! ", 1)[1]
    assert model_part == "My answer is Quack, quack!"
    assert duckgpt.is_full_sentence(model_part)
