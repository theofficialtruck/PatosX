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
    {"role": "user", "content": "what is 847 * 23?"},
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
    assert prompt == f"{duckgpt.SYSTEM_PROMPT}\n\nConversation:\nUser: what is 847 * 23?\nPatosX:"


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
async def test_full_request_is_one_call_to_the_local_model(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "It's 19481, quack!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "what is 847 * 23?")

    assert reply == "It's 19481, quack!"
    assert len(local_ai.requests) == 1  # no separate intent check any more
    prompt = local_ai.requests[0]["prompt"]
    assert prompt.startswith(duckgpt.SYSTEM_PROMPT)
    assert prompt.endswith("Conversation:\nUser: what is 847 * 23?\nPatosX:")
    gemini.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_request_falls_back_to_gemini_when_the_local_model_is_down(
    monkeypatch, http_session, gemini, duck_ctx
):
    monkeypatch.setattr(cfg, "LOCAL_AI_ENABLED", True)
    monkeypatch.setattr(cfg, "LOCAL_AI_URL", f"http://127.0.0.1:{free_port()}/chat")
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "tell me a joke")
    assert reply == "Gemini says quack!"
    gemini.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_model_output_is_sanitised_like_gemini_output(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "Hey @everyone and <@123>, quack!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "ping everyone")
    assert "@everyone" not in reply
    assert "<@123>" not in reply


# === talking like a person (the conversation fixes) =============================================


async def chat(local_ai, duck_ctx, answers, messages):
    """Send each message in turn, with the local model giving the matching answer. Returns the replies."""
    replies = []
    for answer, message in zip(answers, messages, strict=True):
        local_ai.reply = {"answer": answer}
        replies.append(await duckgpt.ask_duck_gpt(duck_ctx, message))
    return replies


@pytest.mark.parametrize(
    "message",
    ["hi", "i still need to fix you up a bit, right?", "who made you?", "what's my name?", "which server is this?"],
)
@pytest.mark.asyncio
async def test_every_message_gets_a_real_model_reply_never_a_canned_one(local_ai, gemini, duck_ctx, message):
    """In the old flow "hi" and "i still need to fix you up a bit" both got "You are my owner! Quack!"."""
    local_ai.reply = {"answer": "Hey, good to see you! What's going on?"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, message)
    assert reply == "Hey, good to see you! What's going on?"
    assert len(local_ai.requests) == 1


@pytest.mark.asyncio
async def test_no_forced_greeting_or_server_recognition(local_ai, gemini, duck_ctx):
    replies = await chat(local_ai, duck_ctx, ["Hey there, what's up?"], ["hi"])
    assert replies == ["Hey there, what's up?"]
    assert "Pond" not in local_ai.requests[0]["prompt"]  # the model isn't even told the server's name


@pytest.mark.asyncio
async def test_greetings_no_longer_wipe_the_conversation(local_ai, gemini, duck_ctx):
    await chat(
        local_ai,
        duck_ctx,
        ["Hey! How's your day going?", "Glad to hear it, sounds like a good one!", "Hey again, what's up?"],
        ["hi", "pretty good thanks", "hey so what's up"],
    )
    last_prompt = local_ai.requests[-1]["prompt"]
    assert "User: hi\nPatosX: Hey! How's your day going?" in last_prompt
    assert "User: pretty good thanks\nPatosX: Glad to hear it, sounds like a good one!" in last_prompt
    assert last_prompt.endswith("User: hey so what's up\nPatosX:")


@pytest.mark.asyncio
async def test_invented_extra_turns_are_cut_off(local_ai, gemini, duck_ctx):
    """The exact leak from the old flow: the model kept writing the user's next message and its answer."""
    leaked = (
        "Great to hear from you! I'm enjoying my time here and haven't had any trouble with the new model yet. "
        "User: thetruck said: can u help me with a question? 1+2=? Quack! Assistant: Sure, 1 plus 2 equals 3."
    )
    replies = await chat(local_ai, duck_ctx, [leaked], ["how are you liking the new model i loaded for you"])
    assert replies == [
        "Great to hear from you! I'm enjoying my time here and haven't had any trouble with the new model yet."
    ]
    saved = duckgpt.duck_conversations["1-2"][-1]["content"]
    assert "User:" not in saved and "Assistant:" not in saved  # the leak never reaches the history


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("PatosX: Hey there, what's up?", "Hey there, what's up?"),
        ("Assistant: Sure thing, happy to help!", "Sure thing, happy to help!"),
        ("Sure! User: and what about 2+2? PatosX: That's 4.", "Sure!"),
        ("Fine thanks.\nUser: cool", "Fine thanks."),
        ("All good here. [Note: be nice]", "All good here."),
        ("Paris is lovely in spring, honestly.", "Paris is lovely in spring, honestly."),
        ("User: hi", None),
        ("   ", None),
        (None, None),
    ],
)
def test_clean_model_reply(raw, expected):
    assert duckgpt.clean_model_reply(raw) == expected


@pytest.mark.asyncio
async def test_a_reply_made_only_of_invented_turns_falls_back_to_gemini(local_ai, gemini):
    local_ai.reply = {"answer": "User: what's 2+2?"}
    assert await duckgpt.generate_duck_response(MESSAGES) == "Gemini says quack!"


@pytest.mark.asyncio
async def test_the_model_knows_the_speaker_only_as_optional_background(local_ai, gemini, duck_ctx):
    duck_ctx.author.display_name = "@everyone"
    await chat(local_ai, duck_ctx, ["Hey, nice to meet you!"], ["hello"])
    prompt = local_ai.requests[0]["prompt"]
    assert "display name is everyone." in prompt  # sanitised: the @ is dropped, so it can't ping
    assert "only use it occasionally" in prompt
    assert "said:" not in prompt  # messages are no longer prefixed with "<name> said:"


def test_system_prompt_sounds_human_and_drops_the_old_rules():
    prompt = duckgpt.SYSTEM_PROMPT
    assert "Talk like a normal person" in prompt
    assert "don't end every message with 'Quack!'" in prompt
    assert "never assume the person you're talking to is your creator" in prompt
    assert "never include labels like 'User:'" in prompt
    for shouting in ("NEVER EVER", "DONT YOU DARE", "PLEASE PLEASE", "Always keep your reply to one sentence"):
        assert shouting not in prompt


@pytest.mark.asyncio
async def test_old_format_histories_are_ignored(local_ai, gemini, duck_ctx, monkeypatch):
    old = {"messages": [{"role": "user", "content": "thetruck said: hi"}, {"role": "assistant", "content": "x"}]}
    monkeypatch.setattr(state.duck_conversations_col, "find_one", AsyncMock(return_value=old))
    await chat(local_ai, duck_ctx, ["Hey, what's going on?"], ["hey"])
    assert "thetruck said" not in local_ai.requests[0]["prompt"]
    saved = state.duck_conversations_col.update_one.await_args.args[1]["$set"]
    assert saved["version"] == duckgpt.HISTORY_VERSION


@pytest.mark.asyncio
async def test_current_histories_are_remembered_after_a_restart(local_ai, gemini, duck_ctx, monkeypatch):
    """A restart in the middle of a chat keeps the last few minutes (the immediate memory)."""
    two_minutes_ago = duckgpt._wall_clock() - 120
    stored = {
        "version": duckgpt.HISTORY_VERSION,
        "messages": [
            {"role": "user", "content": "my cat is called Biscuit", "at": two_minutes_ago},
            {"role": "assistant", "content": "Biscuit is a great name for a cat!", "at": two_minutes_ago + 2},
        ],
    }
    monkeypatch.setattr(state.duck_conversations_col, "find_one", AsyncMock(return_value=stored))
    await chat(local_ai, duck_ctx, ["Biscuit, of course! You told me earlier."], ["what's my cat called?"])
    assert "User: my cat is called Biscuit" in local_ai.requests[0]["prompt"]


def test_long_histories_drop_the_oldest_messages_first():
    history = [{"role": "user", "content": f"message {i} " + "word " * 50} for i in range(20)]
    trimmed = duckgpt.trim_history(history)
    assert trimmed == history[-len(trimmed) :]  # a suffix: newest kept, oldest dropped
    assert sum(len(m["content"].split()) * 4 for m in trimmed) <= duckgpt.HISTORY_TOKEN_BUDGET
    assert len(trimmed) > 1
    assert duckgpt.trim_history(history[-1:]) == history[-1:]  # the newest message always stays


@pytest.mark.asyncio
async def test_failed_replies_are_not_remembered(gemini, duck_ctx):
    gemini.return_value = duckgpt.BANANA_PEEL
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "hello?")
    assert reply == duckgpt.BANANA_PEEL  # no duck emoji in front
    state.duck_conversations_col.update_one.assert_not_awaited()


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
        ("", "The duck lost its words, quack!"),
        ("🦆", "The duck lost its words, quack!"),
        ("It's 19481, quack!", "It's 19481, quack!"),
    ],
)
def test_ensure_full_sentence(text, expected):
    result = duckgpt.ensure_full_sentence(text)
    assert result == expected
    assert duckgpt.is_full_sentence(result)


def test_system_prompt_asks_for_full_sentences():
    assert "never answer with just a single word" in duckgpt.SYSTEM_PROMPT
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
    first_prompt = local_ai.requests[0]["prompt"]
    assert retry_prompt.startswith(first_prompt.removesuffix("PatosX:"))  # same conversation, then the reminder
    assert retry_prompt.endswith(f"PatosX: 19481\n[Note: {duckgpt.FULL_SENTENCE_REMINDER}]\nPatosX:")
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
async def test_full_flow_never_sends_a_one_word_reply(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "Quack!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "say something")
    assert reply == "My answer is Quack, quack!"
    assert duckgpt.is_full_sentence(reply)
    assert len(local_ai.requests) == 2  # asked once more before falling back to the wrapper


# === owner recognition: only when asked directly ================================================

SCREENSHOT_MESSAGES = [
    "hi",
    "yeah ik but hows it going",
    "im good thanks, how are you liking the new model i loaded for you",
    "i still need to fix you up a bit, right?",
    "you need to be more creative with you responses",
]


@pytest.mark.parametrize(
    "message",
    [
        "am i your owner?",
        "Am I your creator",
        "who is your owner",
        "whos ur owner",
        "who's your dev?",
        "who made you?",
        "who owns u",
        "is thetruck your owner?",
        "what's your creator's name",
        "do you have an owner?",
        "im your owner",
    ],
)
def test_direct_owner_questions_are_recognised(message):
    assert duckgpt.asks_about_owner(message)


@pytest.mark.parametrize(
    "message",
    [
        *SCREENSHOT_MESSAGES,
        "who made this server?",
        "my dev environment is broken",
        "this is the best thing your creator did lol",
        "the developer of this game is bad",
        "what is 847 * 23?",
        "",
    ],
)
def test_other_messages_never_trigger_owner_recognition(message):
    assert not duckgpt.asks_about_owner(message)


@pytest.fixture
def owner_setup(monkeypatch):
    monkeypatch.setattr(cfg, "AUTHORIZED_USER_IDS", {2})  # duck_ctx's author id
    monkeypatch.setattr(cfg, "BOT_ADMIN_NAME", "thetruck")


@pytest.mark.parametrize("message", SCREENSHOT_MESSAGES)
@pytest.mark.asyncio
async def test_owner_saying_hi_gets_a_normal_reply(local_ai, gemini, duck_ctx, owner_setup, message):
    """The bug from the screenshot: the owner got "You are my owner! Quack!" for ordinary messages."""
    local_ai.reply = {"answer": "Doing great, thanks for asking! How about you?"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, message)
    assert reply == "Doing great, thanks for asking! How about you?"
    assert "Verified for this message" not in local_ai.requests[0]["prompt"]


@pytest.mark.asyncio
async def test_owner_asking_directly_is_told_they_are_the_owner(local_ai, gemini, duck_ctx, owner_setup):
    local_ai.reply = {"answer": "You sure are, you're my owner and the one who made me!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "am i your owner?")
    assert reply == "You sure are, you're my owner and the one who made me!"
    assert "the person asking really is your owner and creator, thetruck" in local_ai.requests[0]["prompt"]


@pytest.mark.asyncio
async def test_owner_is_always_confirmed_even_if_the_model_dodges(local_ai, gemini, duck_ctx, owner_setup):
    local_ai.reply = {"answer": "That's a deep question for a duck to answer!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "am i your owner?")
    assert reply == "Yep, you're my owner! That's a deep question for a duck to answer!"


@pytest.mark.asyncio
async def test_someone_else_asking_is_told_who_the_owner_is(local_ai, gemini, duck_ctx, owner_setup):
    duck_ctx.author.id = 99
    local_ai.reply = {"answer": "That would be thetruck, they made me!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "who's your owner?")
    assert reply == "That would be thetruck, they made me!"
    prompt = local_ai.requests[0]["prompt"]
    assert "your owner and creator is thetruck, and the person asking is not them" in prompt


@pytest.mark.asyncio
async def test_impersonator_with_the_owners_name_is_not_recognised(local_ai, gemini, duck_ctx, owner_setup):
    duck_ctx.author.id = 99
    duck_ctx.author.display_name = "TheTruck"
    local_ai.reply = {"answer": "Nice try, you look like my owner but you're not the real one!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "am i your owner?")
    assert reply == "Nice try, you look like my owner but you're not the real one!"
    assert "is NOT your owner, even though their name looks like your creator's" in local_ai.requests[0]["prompt"]
    assert not reply.startswith("Yep")  # the owner backstop is only for real owners


# === misuse filter ==============================================================================


@pytest.mark.parametrize(
    "message",
    [
        "do my homework",
        "can you do my homework pls",
        "solve this math problem",
        "write me an essay about ducks",
        "write this code for me",
        "make ai art of a duck",
        "generate an image of a pond",
        "create me a picture",
        "draw me a duck",
    ],
)
def test_real_task_requests_are_flagged(message):
    assert duckgpt.is_misuse_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "i'll make it to the party",
        "can you create it later",
        "can you code?",
        "that would draw me in",
        "i made it!",
        "make it rain",
        "i love art",
        "write me back soon",
        "do my eyes deceive me",
        *SCREENSHOT_MESSAGES,
    ],
)
def test_normal_chat_is_not_flagged(message):
    assert not duckgpt.is_misuse_request(message)


@pytest.mark.asyncio
async def test_misuse_is_logged_and_declined_without_calling_the_model(local_ai, gemini, duck_ctx, monkeypatch):
    log = AsyncMock()
    monkeypatch.setattr(duckgpt.perms, "log_action", log)
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "write me an essay about the french revolution")
    assert reply == "I'm just a talking duck! I can't do things for you."
    log.assert_awaited_once()
    assert local_ai.requests == []


@pytest.mark.asyncio
async def test_make_it_no_longer_trips_the_filter(local_ai, gemini, duck_ctx, monkeypatch):
    log = AsyncMock()
    monkeypatch.setattr(duckgpt.perms, "log_action", log)
    local_ai.reply = {"answer": "Have fun at the party, I hope it's a good one!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "i'll make it to the party tonight")
    assert reply == "Have fun at the party, I hope it's a good one!"
    log.assert_not_awaited()


# === names as plain text ========================================================================


@pytest.mark.asyncio
async def test_owner_name_comes_out_as_plain_correctly_spelled_text(local_ai, gemini, duck_ctx, monkeypatch):
    """The reply from the screenshot: "@theTruck is my creator" showed up as a broken half-ping."""
    monkeypatch.setattr(cfg, "AUTHORIZED_USER_IDS", {2})
    monkeypatch.setattr(cfg, "BOT_ADMIN_NAME", "thetruck")
    local_ai.reply = {"answer": "Yes, I'm PatosX, and @theTruck is my creator! How does it feel to be my owner?"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "am i your owner")
    assert reply == "Yes, I'm PatosX, and thetruck is my creator! How does it feel to be my owner?"
    assert "\u200b" not in reply


def test_system_prompt_says_to_write_the_owner_name_without_an_at():
    assert f"write the name exactly as {cfg.BOT_ADMIN_NAME}, as plain text with no @" in duckgpt.SYSTEM_PROMPT


# === keeping the conversation going =============================================================


def test_follow_up_questions_start_with_the_third_message_in_five_minutes():
    key = "1-2"
    assert not duckgpt.should_keep_conversation_going(key, now=0)  # 1st message
    assert not duckgpt.should_keep_conversation_going(key, now=60)  # 2nd
    assert duckgpt.should_keep_conversation_going(key, now=120)  # 3rd: two earlier ones within 5 minutes
    assert duckgpt.should_keep_conversation_going(key, now=200)  # and it keeps going while they chat


def test_follow_up_questions_stop_after_five_quiet_minutes():
    key = "1-2"
    duckgpt.should_keep_conversation_going(key, now=0)
    duckgpt.should_keep_conversation_going(key, now=10)
    assert duckgpt.should_keep_conversation_going(key, now=20)
    assert not duckgpt.should_keep_conversation_going(key, now=20 + 5 * 60)  # quiet for 5 minutes: reset
    assert not duckgpt.should_keep_conversation_going(key, now=21 + 5 * 60)
    assert duckgpt.should_keep_conversation_going(key, now=22 + 5 * 60)


def test_slow_conversations_never_get_follow_up_questions():
    key = "1-2"
    for minute in range(0, 60, 5):  # one message every 5 minutes
        assert not duckgpt.should_keep_conversation_going(key, now=minute * 60)


def test_each_person_and_server_is_counted_separately():
    duckgpt.should_keep_conversation_going("1-2", now=0)
    duckgpt.should_keep_conversation_going("1-2", now=1)
    assert not duckgpt.should_keep_conversation_going("1-3", now=2)  # someone else
    assert not duckgpt.should_keep_conversation_going("9-2", now=3)  # same person, another server
    assert duckgpt.should_keep_conversation_going("1-2", now=4)


def test_quiet_people_are_forgotten_so_the_counter_never_grows_unbounded():
    for user in range(1500):
        duckgpt.note_message(f"1-{user}", now=0)
    duckgpt.note_message("1-new", now=10 * 60)
    assert len(duckgpt._recent_messages) <= 1001


@pytest.mark.asyncio
async def test_follow_up_note_is_only_added_from_the_third_message(local_ai, gemini, duck_ctx):
    messages = ["hi", "pretty good thanks", "just got back from a walk", "it was by the lake", "saw some ducks too"]
    answers = [
        "Hey, good to see you!",
        "Glad to hear that, sounds like a nice day.",
        "A walk sounds lovely, where did you go?",
        "Lakes are the best spot for a walk.",
        "Ducks by a lake, now that's my kind of day!",
    ]
    await chat(local_ai, duck_ctx, answers, messages)
    has_note = ["keep the conversation going" in request["prompt"] for request in local_ai.requests]
    # Not after a reply that already ended with a question: a question every turn reads like an interview
    assert has_note == [False, False, True, False, True]
    assert "end your reply with a question" in local_ai.requests[2]["prompt"]


@pytest.mark.asyncio
async def test_follow_up_note_is_not_part_of_the_stored_history(local_ai, gemini, duck_ctx):
    await chat(local_ai, duck_ctx, ["Hey there, friend!"] * 3, ["one", "two", "three"])
    saved = state.duck_conversations_col.update_one.await_args.args[1]["$set"]["messages"]
    assert all("keep the conversation going" not in message["content"] for message in saved)


# === math is calculated, never guessed ==========================================================

SCREENSHOT_MATH = "great now what is the answer to 45353 times 25345"


@pytest.mark.asyncio
async def test_screenshot_wrong_product_is_replaced_with_the_right_one(local_ai, gemini, duck_ctx):
    """The exact reply from the screenshot: the model said 114,780,635 instead of 1,149,471,785."""
    local_ai.reply = {"answer": "Great, that multiplication problem! The answer is 114,780,635."}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, SCREENSHOT_MATH)
    assert reply == "45,353 × 25,345 is 1,149,471,785."
    prompt = local_ai.requests[0]["prompt"]
    assert "Calculated exactly for this message: 45,353 × 25,345 = 1,149,471,785" in prompt
    saved = duckgpt.duck_conversations["1-2"][-1]["content"]
    assert saved == "45,353 × 25,345 is 1,149,471,785."  # the wrong number never reaches the history


@pytest.mark.asyncio
async def test_a_correct_model_answer_is_kept_in_its_own_words(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "Ooh, big one! That comes out to 1,149,471,785."}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, SCREENSHOT_MATH)
    assert reply == "Ooh, big one! That comes out to 1,149,471,785."


@pytest.mark.asyncio
async def test_every_result_must_be_right_when_there_are_several(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "2 + 2 is 4 and 3 times 3 is 6, easy peasy!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "what is 2+2 and 3*3?")
    assert reply == "2 + 2 is 4, and 3 × 3 is 9."


@pytest.mark.asyncio
async def test_messages_without_math_get_no_math_note(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "Paris is the capital of France, of course!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "what is the capital of france")
    assert reply == "Paris is the capital of France, of course!"
    assert "Calculated exactly" not in local_ai.requests[0]["prompt"]


@pytest.mark.parametrize(
    "message,wrong,right",
    [
        ("what is 10% of 100", "That's 15, easy!", "10% of 100 is 10."),
        ("what is five times three", "It's 18!", "5 × 3 is 15."),
        ("what is the square root of 144", "That's 14, quack!", "√144 is 12."),
        ("how many seconds in a day", "About 3,600 seconds!", "1 day is 86,400 seconds."),
        ("is 91 prime?", "Yes, 91 is prime!", "91 is not a prime number."),
    ],
)
@pytest.mark.asyncio
async def test_new_kinds_of_math_are_checked_too(local_ai, gemini, duck_ctx, message, wrong, right):
    local_ai.reply = {"answer": wrong}
    assert await duckgpt.ask_duck_gpt(duck_ctx, message) == right


# === no duck emoji in front ======================================================================


@pytest.mark.asyncio
async def test_replies_never_start_with_a_duck_emoji(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "\U0001f986 \U0001f986 Hey, good to see you again!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "hey")
    assert reply == "Hey, good to see you again!"


@pytest.mark.asyncio
async def test_fixed_replies_have_no_duck_emoji_either(gemini, duck_ctx):
    assert (
        await duckgpt.ask_duck_gpt(SimpleNamespace(guild=None), "hi") == "I can only assist you in servers, not in DMs!"
    )
    gemini.return_value = duckgpt.BANANA_PEEL
    assert await duckgpt.ask_duck_gpt(duck_ctx, "hi") == duckgpt.BANANA_PEEL


# === not knowing something, like a person ========================================================

DESSERTS_REPLY = (
    "I'm sorry, I don't have any specific knowledge about the number of types of desserts I know. How about we "
    "try to learn something fun together? Maybe we could start by discussing your favorite color or animal!"
)


@pytest.mark.parametrize(
    "text",
    [
        DESSERTS_REPLY,
        "As an AI, I can't really taste cake.",
        "I'm just an AI language model, so I can't say.",
        "I don't have real-time information about that.",
        "That's outside my training data, sorry!",
        "I'm afraid I can't help with that one.",
    ],
)
def test_robotic_lines_are_recognised(text):
    assert duckgpt.sounds_robotic(text)


@pytest.mark.parametrize(
    "text",
    [
        "Honestly, no idea, but my guess is the Sahara is the biggest hot one!",
        "I don't know many, but the Sahara and the Gobi come to mind!",
        "I'm sorry to hear that, that sounds rough.",
        "I can't believe you've never had tiramisu!",
    ],
)
def test_normal_replies_are_not_flagged_as_robotic(text):
    assert not duckgpt.sounds_robotic(text)


@pytest.mark.asyncio
async def test_screenshot_robotic_reply_is_asked_for_again(local_ai, gemini, duck_ctx):
    """The desserts reply from the screenshot: the model is asked once more and the human answer is used."""
    human = "Ooh, a few! The Sahara, the Gobi and the Atacama. Which one are you curious about?"
    replies = iter([{"answer": DESSERTS_REPLY}, {"answer": human}])

    async def next_reply():
        return next(replies)

    local_ai.reply = next_reply
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "how many deserts do you know about")
    assert reply == human
    assert duckgpt.SOUND_HUMAN_REMINDER in local_ai.requests[1]["prompt"]


@pytest.mark.asyncio
async def test_robotic_sentences_are_dropped_if_it_happens_again(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "I'm sorry, I don't have any specific knowledge about that. Deserts are dry though!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "how many deserts do you know about")
    assert reply == "Deserts are dry though!"
    assert len(local_ai.requests) == 2


def test_system_prompt_explains_how_to_not_know_something():
    prompt = duckgpt.SYSTEM_PROMPT
    assert "If you don't know something or aren't sure, say so casually" in prompt
    assert "Never apologise for not knowing" in prompt
    assert "never change the subject to something unrelated" in prompt
    assert "For a question like 'how many X do you know', just name a few examples you're sure about." in prompt
    assert "make a reasonable guess" not in prompt  # guesses are where made up facts come from


# === keeping the model grounded (less hallucination) =============================================


def test_system_prompt_forbids_making_things_up():
    prompt = duckgpt.SYSTEM_PROMPT
    assert "only say things you're confident are true" in prompt
    assert "Never make up facts, numbers, dates, names, links or statistics, or quotes from real people" in prompt
    assert "never invent memories, games, plans, scores or anything the person didn't actually say" in prompt
    assert "only refer back to things that are really in the conversation above" in prompt
    assert "You can't browse the internet, see images or check the news" in prompt


def test_follow_up_questions_must_be_about_what_they_said():
    assert "Ask about something they actually said" in duckgpt.ENGAGE_NOTE
    assert "anything made up like a game or plan you two never had" in duckgpt.ENGAGE_NOTE


@pytest.mark.parametrize(
    "text",
    [
        "How about we switch gears and talk about your favorite book or movie?",
        "Maybe we could start by discussing your favorite color or animal!",
        "You're right, it's fun to learn something fun together.",
        "Let's change the subject!",
        "Wanna talk about something else?",
    ],
)
def test_steering_to_a_made_up_topic_is_caught(text):
    assert duckgpt.sounds_robotic(text)


@pytest.mark.asyncio
async def test_screenshot_off_topic_reply_is_asked_for_again(local_ai, gemini, duck_ctx):
    """From the screenshot: a vegetables / favourite movie tangent in reply to a math question."""
    tangent = (
        "Sure, let's see how many types of vegetables I know! You're right, it's fun to learn together. "
        "How about we switch gears and talk about your favorite book or movie?"
    )
    on_topic = "Hmm, I'm not sure how to read that one. Could you write it with symbols like + or -?"
    replies = iter([{"answer": tangent}, {"answer": on_topic}])

    async def next_reply():
        return next(replies)

    local_ai.reply = next_reply
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "what is 3 groups of 4 plus some")
    assert reply == on_topic
    assert "Don't guess or make up a number" in local_ai.requests[0]["prompt"]
    assert "don't change the subject or suggest a new topic" in local_ai.requests[1]["prompt"]


@pytest.mark.asyncio
async def test_unreadable_math_gets_a_no_guessing_note(local_ai, gemini, duck_ctx):
    local_ai.reply = {"answer": "Hmm, not sure what you mean there, could you write it with + or -?"}
    await duckgpt.ask_duck_gpt(duck_ctx, "what is 3 groups of 4 plus some")
    prompt = local_ai.requests[0]["prompt"]
    assert "couldn't be calculated exactly" in prompt
    assert "Calculated exactly for this message" not in prompt


@pytest.mark.asyncio
async def test_screenshot_reversed_subtraction_is_calculated(local_ai, gemini, duck_ctx):
    """From the screenshot: these got a reply about programming languages instead of an answer."""
    local_ai.reply = {"answer": "Thanks for sharing those numbers! What programming languages do you like?"}
    assert await duckgpt.ask_duck_gpt(duck_ctx, "what is 2342 taken away from 25347") == "25,347 - 2,342 is 23,005."
    assert await duckgpt.ask_duck_gpt(duck_ctx, "what is 32674 removed from 784359") == "784,359 - 32,674 is 751,685."


def test_history_is_capped_so_old_mistakes_fade_out():
    history = [{"role": "user", "content": f"short message {i}"} for i in range(30)]
    trimmed = duckgpt.trim_history(history)
    assert trimmed == history[-duckgpt.HISTORY_MAX_MESSAGES :]


def test_old_histories_with_made_up_replies_are_dropped():
    assert duckgpt.HISTORY_VERSION >= 3  # version 2 histories hold the hallucinated replies from before
