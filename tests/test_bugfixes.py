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

"""Regression tests for the defects found outside the giveaway system during the bug audit."""

import random
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs import duckgpt, economy, games_gambling, shop
from core import config as cfg
from core import economyHelperFuncs as econ
from core import permsHelperFuncs as perms
from core import state

# --- shop -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_shop_refund_returns_a_dict_when_the_item_left_the_shop(monkeypatch):
    """This return used to be a set literal (a typo'd dict), which crashed the refund dropdown."""
    monkeypatch.setattr(state, "guild_shop_col", SimpleNamespace(find_one=AsyncMock(return_value=None)))
    monkeypatch.setattr(state, "shop_col", SimpleNamespace(find_one=AsyncMock(return_value=None)))
    result = await shop.process_shop_refund(
        SimpleNamespace(id=1), SimpleNamespace(id=2), "widget", {"wallet": 0, "inventory": ["widget"]}
    )
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert "no longer exists" in result["message"]


@pytest.mark.asyncio
async def test_inventory_survives_missing_shop_documents(monkeypatch, shop_cog):
    """A Pet Duck / Nitro Boost in the inventory used to crash `inventory` with a TypeError when the
    global shop had not been seeded yet."""
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=1), author=SimpleNamespace(id=2, display_name="T"), send=AsyncMock(), interaction=None
    )
    monkeypatch.setattr(perms, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(
        econ,
        "get_user",
        AsyncMock(
            return_value={"inventory": [{"_id": "pet_duck", "uses_left": 3}, {"_id": "nitro_boost", "uses_left": 1}]}
        ),
    )
    monkeypatch.setattr(state, "shop_col", SimpleNamespace(find_one=AsyncMock(return_value=None)))
    from motor.motor_asyncio import AsyncIOMotorClient

    monkeypatch.setattr(state, "xp_col", AsyncIOMotorClient("mongodb://localhost:27017")["t"]["xp"])
    await shop_cog.inventory.callback(shop_cog, ctx)
    embed = ctx.send.await_args.kwargs["embed"]
    assert any("Pet Duck" in f.name for f in embed.fields)
    assert any("Nitro Boost" in f.name for f in embed.fields)


# --- economy ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deposit_accepts_k_suffix(monkeypatch, economy_cog):
    """The slash description promised k/m/b suffixes but the code only accepted plain digits."""
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=1), author=SimpleNamespace(id=2, display_name="T"), send=AsyncMock(), interaction=None
    )
    monkeypatch.setattr(perms, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(econ, "get_user", AsyncMock(return_value={"wallet": 5000, "bank": 0}))
    update_one = AsyncMock()
    monkeypatch.setattr(state, "economy_col", SimpleNamespace(update_one=update_one))
    from motor.motor_asyncio import AsyncIOMotorClient

    monkeypatch.setattr(state, "xp_col", AsyncIOMotorClient("mongodb://localhost:27017")["t"]["xp"])

    await economy_cog.deposit.callback(economy_cog, ctx, "1k")

    update_one.assert_awaited_once_with({"_id": "1-2"}, {"$set": {"wallet": 4000, "bank": 950}})
    assert "You deposited 1000 coins" in ctx.send.await_args.args[0]


def test_sell_summary_text_stays_within_embed_limit():
    text = economy.sell_summary_text(["x" * 100] * 100)
    assert len(text) <= 4096
    assert text.endswith("...")
    assert economy.sell_summary_text(["a", "b"]) == "a\nb"


# --- door game ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doorgame_loss_does_not_charge_the_bet_a_second_time(monkeypatch):
    """The bet is taken when the game starts; a losing door used to subtract it again."""
    subtract = AsyncMock()
    add = AsyncMock()
    monkeypatch.setattr(econ, "subtract_balance", subtract)
    monkeypatch.setattr(econ, "add_balance", add)
    monkeypatch.setattr(random, "choice", lambda outcomes: "0x")
    games_gambling.button_cooldowns.clear()
    ctx = SimpleNamespace(author=SimpleNamespace(id=5), guild=SimpleNamespace(id=1))
    view = games_gambling.DoorGameButton(ctx, "5", "1", 100, 1, 1, 900)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=5, name="tester"),
        data={"custom_id": "1"},
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    await view.door_clicked(interaction)

    subtract.assert_not_awaited()
    add.assert_not_awaited()
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "You now have `900` coins" in embed.description


# --- disable / enable ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_disabled_blocks_category_by_cog_but_never_management_commands(
    monkeypatch, economy_cog, guildcfg_cog
):
    doc = {"guild": "1", "disabled_commands": ["enable"], "disabled_categories": ["economy"]}
    monkeypatch.setattr(state, "disabled_col", SimpleNamespace(find_one=AsyncMock(return_value=doc)))
    guild = SimpleNamespace(id=1)
    assert await perms.check_disabled(SimpleNamespace(guild=guild, command=economy_cog.balance)) is False
    assert await perms.check_disabled(SimpleNamespace(guild=guild, command=guildcfg_cog.setprefix)) is True
    assert await perms.check_disabled(SimpleNamespace(guild=guild, command=guildcfg_cog.enable)) is True
    assert perms.command_category(economy_cog.balance) == "economy"
    assert perms.command_category(guildcfg_cog.setprefix) is None


@pytest.mark.asyncio
async def test_disable_resolves_aliases_and_protects_management_commands(monkeypatch, guildcfg_cog, economy_cog):
    update_one = AsyncMock()
    monkeypatch.setattr(
        state, "disabled_col", SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=update_one)
    )
    ctx = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())

    await guildcfg_cog.disable.callback(guildcfg_cog, ctx, "enable")
    assert "can't be disabled" in ctx.send.await_args.args[0]
    update_one.assert_not_awaited()

    await guildcfg_cog.disable.callback(guildcfg_cog, ctx, "bal")
    assert "Disabled command `balance`" in ctx.send.await_args.args[0]
    assert update_one.await_args.args[1]["$set"]["disabled_commands"] == ["balance"]

    await guildcfg_cog.disable.callback(guildcfg_cog, ctx, "economy")
    assert "Disabled category `economy`" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_listdisabled_reads_the_keys_disable_writes(monkeypatch, guildcfg_cog):
    doc = {"guild": "1", "disabled_commands": ["fish"], "disabled_categories": ["economy"]}
    monkeypatch.setattr(state, "disabled_col", SimpleNamespace(find_one=AsyncMock(return_value=doc)))
    ctx = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
    await guildcfg_cog.listdisabled.callback(guildcfg_cog, ctx)
    embed = ctx.send.await_args.kwargs["embed"]
    assert {f.name: f.value for f in embed.fields} == {"Commands": "`fish`", "Categories": "`economy`"}


@pytest.mark.asyncio
async def test_duckgpt_mention_respects_disabled_category(monkeypatch, duckgpt_cog, bot):
    monkeypatch.setattr(
        state, "disabled_col", SimpleNamespace(find_one=AsyncMock(return_value={"disabled_categories": ["duckgpt"]}))
    )
    ask = AsyncMock()
    monkeypatch.setattr(duckgpt, "ask_duck_gpt", ask)
    bot_user = SimpleNamespace(id=42)
    monkeypatch.setattr(type(bot), "user", property(lambda self: bot_user))
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=7), guild=SimpleNamespace(id=1), mentions=[bot_user], reply=AsyncMock()
    )
    await duckgpt_cog.on_message(message)
    ask.assert_not_awaited()


# --- log_action -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_action_posts_to_the_configured_channel_without_a_ctx(monkeypatch):
    """Automated actions (mute expiry) pass ctx=None and used to crash; the log channel is the one
    saved by .configure in config_col, which log_action never read before."""
    log_channel = SimpleNamespace(send=AsyncMock())
    guild = SimpleNamespace(id=1, get_channel=lambda cid: log_channel if cid == 5 else None)
    monkeypatch.setattr(
        state, "config_col", SimpleNamespace(find_one=AsyncMock(return_value={"guild": "1", "log_channel": 5}))
    )
    monkeypatch.setattr(state, "settings_col", SimpleNamespace(find_one=AsyncMock(return_value=None)))
    inserted = []

    async def insert_one(doc):
        inserted.append(doc)

    monkeypatch.setattr(state, "logs_col", SimpleNamespace(insert_one=insert_one))

    await perms.log_action(None, "Auto-unmuted someone", user_id=9, action_type="unmute", guild=guild)

    log_channel.send.assert_awaited_once()
    embed = log_channel.send.await_args.kwargs["embed"]
    assert embed.footer.text == "Automated action"
    assert inserted[0]["by"] == {"name": "System", "id": None}
    assert inserted[0]["action"] == "unmute"


@pytest.mark.asyncio
async def test_log_action_with_ctx_uses_author_and_legacy_settings_fallback(monkeypatch):
    log_channel = SimpleNamespace(send=AsyncMock())
    guild = SimpleNamespace(id=1, get_channel=lambda cid: log_channel if cid == 6 else None)
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=3, __str__=lambda self: "Mod"))
    monkeypatch.setattr(state, "config_col", SimpleNamespace(find_one=AsyncMock(return_value={"guild": "1"})))
    monkeypatch.setattr(
        state, "settings_col", SimpleNamespace(find_one=AsyncMock(return_value={"guild": "1", "log_channel": 6}))
    )
    monkeypatch.setattr(state, "logs_col", SimpleNamespace(insert_one=AsyncMock()))

    await perms.log_action(ctx, "Kicked someone", user_id=9, action_type="kick")

    log_channel.send.assert_awaited_once()
    assert "• 3" in log_channel.send.await_args.kwargs["embed"].footer.text
    state.logs_col.insert_one.assert_awaited_once()


# --- misc commands ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serverinfo_works_for_a_guild_without_an_icon(info_cog):
    """`discord.Embed.Empty` no longer exists in discord.py 2.x, so servers without an icon crashed."""
    guild = SimpleNamespace(
        name="Pond", icon=None, member_count=12, id=1, created_at=datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    ctx = SimpleNamespace(
        guild=guild,
        author=SimpleNamespace(display_avatar=SimpleNamespace(url="https://x/a.png"), __str__=lambda self: "T"),
        send=AsyncMock(),
    )
    await info_cog.serverinfo.callback(info_cog, ctx)
    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.title == "📜 Server Information: Pond"
    assert embed.thumbnail.url is None


@pytest.mark.asyncio
async def test_viewperms_handles_all_saved_staff_having_left(monkeypatch, staff_cog):
    class Cursor:
        async def to_list(self, length=None):
            return [{"guild": "1", "user": "5", "permissions": ["kick"]}]

    monkeypatch.setattr(state, "staffperms_col", SimpleNamespace(find=MagicMock(return_value=Cursor())))
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=1, name="Pond", get_member=lambda uid: None),
        author=SimpleNamespace(id=1),
        send=AsyncMock(),
    )
    await staff_cog.viewperms.callback(staff_cog, ctx)
    assert "No staff with saved permissions" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_purge_reports_only_the_purged_messages(monkeypatch, moderation_cog):
    channel = SimpleNamespace(purge=AsyncMock(return_value=[1, 2, 3]), name="general")
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=channel,
        message=SimpleNamespace(delete=AsyncMock()),
        send=AsyncMock(),
        interaction=None,
        author=SimpleNamespace(id=1),
    )
    monkeypatch.setattr(perms, "log_action", AsyncMock())
    await moderation_cog.purge.callback(moderation_cog, ctx, 3)
    ctx.message.delete.assert_awaited_once()
    channel.purge.assert_awaited_once()
    assert channel.purge.await_args.kwargs["limit"] == 3
    assert ctx.send.await_args.args[0] == "🧹 Deleted 3 messages."


# --- Copilot review follow-ups (PR #79) --------------------------------------------------------


class _FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Stands in for the shared aiohttp session: records requests, never opens a socket."""

    def __init__(self, payload):
        self.payload = payload
        self.requests = []
        self.closed = False

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return _FakeResponse(payload=self.payload)


@pytest.mark.asyncio
async def test_slap_defaults_to_the_invoker_and_uses_the_shared_session(monkeypatch, fun_cog):
    """The command description promises "will slap yourself if not provided"; the code used to
    reject a missing target. It also used to open a fresh aiohttp.ClientSession per request."""
    session = _FakeSession({"data": [{"images": {"original": {"url": "https://giphy.test/slap.gif"}}}]})
    monkeypatch.setattr(state, "http_session", lambda: session)
    author = SimpleNamespace(id=1, mention="<@1>")
    ctx = SimpleNamespace(author=author, send=AsyncMock(), defer=AsyncMock(), interaction=None)

    await fun_cog.slap.callback(fun_cog, ctx, None)

    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.description == "<@1> slapped themselves! Ouch!"
    assert embed.image.url == "https://giphy.test/slap.gif"
    assert len(session.requests) == 1
    assert not session.closed, "cogs must never close the shared session"

    target = SimpleNamespace(id=2, mention="<@2>")
    await fun_cog.slap.callback(fun_cog, ctx, target)
    assert ctx.send.await_args.kwargs["embed"].description == "<@1> slapped <@2>! Ouch!"


@pytest.mark.asyncio
async def test_duck_and_quote_use_the_shared_session(monkeypatch, fun_cog):
    duck_session = _FakeSession({"url": "https://random-d.test/1.jpg"})
    monkeypatch.setattr(state, "http_session", lambda: duck_session)
    monkeypatch.setattr(state, "config_col", SimpleNamespace(find_one=AsyncMock(return_value={})))
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=5), send=AsyncMock(), interaction=None
    )

    await fun_cog.duck.callback(fun_cog, ctx)

    assert ctx.send.await_args.kwargs["embed"].image.url == "https://random-d.test/1.jpg"
    assert duck_session.requests[0][0] == "https://random-d.uk/api/random"
    assert not duck_session.closed


@pytest.mark.asyncio
async def test_duckfact_thumbnail_is_an_image_endpoint(fun_cog):
    """`/api/v2/random` returns JSON, which Discord cannot render as a thumbnail."""
    ctx = SimpleNamespace(send=AsyncMock(), interaction=None)
    await fun_cog.duckfact.callback(fun_cog, ctx)
    embed = ctx.send.await_args.kwargs["embed"]
    assert embed.thumbnail.url == "https://random-d.uk/api/v2/randomimg"
    assert embed.description


@pytest.mark.asyncio
async def test_http_session_is_shared_and_created_lazily(monkeypatch):
    monkeypatch.setattr(state, "session", None)
    first = state.http_session()
    try:
        assert state.session is first
        assert state.http_session() is first, "every caller must get the same session"
        assert not first.closed
    finally:
        await first.close()
    replacement = state.http_session()
    try:
        assert replacement is not first, "a closed session must be replaced, not handed out again"
    finally:
        await replacement.close()


def test_parse_gemini_keys_ignores_empty_entries():
    assert cfg.parse_gemini_keys("key1, key2 ,,") == ["key1", "key2"]
    assert cfg.parse_gemini_keys(" , ,") == []
    assert cfg.parse_gemini_keys(None) == []
    assert cfg.parse_gemini_keys("") == []
