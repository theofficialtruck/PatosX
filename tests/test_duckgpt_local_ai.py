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

    assert reply == "🦆 It's 19481, quack!"
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
    assert reply == "🦆 Gemini says quack!"
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
    assert reply == "🦆 Hey, good to see you! What's going on?"
    assert len(local_ai.requests) == 1


@pytest.mark.asyncio
async def test_no_forced_greeting_or_server_recognition(local_ai, gemini, duck_ctx):
    replies = await chat(local_ai, duck_ctx, ["Hey there, what's up?"], ["hi"])
    assert replies == ["🦆 Hey there, what's up?"]
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
        "🦆 Great to hear from you! I'm enjoying my time here and haven't had any trouble with the new model yet."
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
    assert "display name is @\u200beveryone" in prompt  # sanitised: can't become a ping
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
    stored = {
        "version": duckgpt.HISTORY_VERSION,
        "messages": [
            {"role": "user", "content": "my cat is called Biscuit"},
            {"role": "assistant", "content": "Biscuit is a great name for a cat!"},
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
    assert reply == f"🦆 {duckgpt.BANANA_PEEL}"  # one duck emoji, not two
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
    assert reply == "🦆 My answer is Quack, quack!"
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
    assert reply == "🦆 Doing great, thanks for asking! How about you?"
    assert "Verified for this message" not in local_ai.requests[0]["prompt"]


@pytest.mark.asyncio
async def test_owner_asking_directly_is_told_they_are_the_owner(local_ai, gemini, duck_ctx, owner_setup):
    local_ai.reply = {"answer": "You sure are, you're my owner and the one who made me!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "am i your owner?")
    assert reply == "🦆 You sure are, you're my owner and the one who made me!"
    assert "the person asking really is your owner and creator, thetruck" in local_ai.requests[0]["prompt"]


@pytest.mark.asyncio
async def test_owner_is_always_confirmed_even_if_the_model_dodges(local_ai, gemini, duck_ctx, owner_setup):
    local_ai.reply = {"answer": "That's a deep question for a duck to answer!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "am i your owner?")
    assert reply == "🦆 Yep, you're my owner! That's a deep question for a duck to answer!"


@pytest.mark.asyncio
async def test_someone_else_asking_is_told_who_the_owner_is(local_ai, gemini, duck_ctx, owner_setup):
    duck_ctx.author.id = 99
    local_ai.reply = {"answer": "That would be thetruck, they made me!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "who's your owner?")
    assert reply == "🦆 That would be thetruck, they made me!"
    prompt = local_ai.requests[0]["prompt"]
    assert "your owner and creator is thetruck, and the person asking is not them" in prompt


@pytest.mark.asyncio
async def test_impersonator_with_the_owners_name_is_not_recognised(local_ai, gemini, duck_ctx, owner_setup):
    duck_ctx.author.id = 99
    duck_ctx.author.display_name = "TheTruck"
    local_ai.reply = {"answer": "Nice try, you look like my owner but you're not the real one!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "am i your owner?")
    assert reply == "🦆 Nice try, you look like my owner but you're not the real one!"
    assert "is NOT your owner, even though their name looks like your creator's" in local_ai.requests[0]["prompt"]
    assert not reply.startswith("🦆 Yep")  # the owner backstop is only for real owners


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
    assert reply == "🦆 I'm just a talking duck! I can't do things for you."
    log.assert_awaited_once()
    assert local_ai.requests == []


@pytest.mark.asyncio
async def test_make_it_no_longer_trips_the_filter(local_ai, gemini, duck_ctx, monkeypatch):
    log = AsyncMock()
    monkeypatch.setattr(duckgpt.perms, "log_action", log)
    local_ai.reply = {"answer": "Have fun at the party, I hope it's a good one!"}
    reply = await duckgpt.ask_duck_gpt(duck_ctx, "i'll make it to the party tonight")
    assert reply == "🦆 Have fun at the party, I hope it's a good one!"
    log.assert_not_awaited()
