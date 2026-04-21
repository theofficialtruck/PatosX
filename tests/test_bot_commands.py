# test_bot_commands.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
from types import SimpleNamespace
import main
import asyncio

@pytest.mark.asyncio
async def test_warn_command():
    ctx = MagicMock()
    member = MagicMock()
    ctx.guild.id = 123456789
    ctx.author = MagicMock(name="Moderator", id=111)
    member.id = 222
    member.mention = "@TestUser"
    member.send = AsyncMock()
    main.warnings_data.clear()

    await main.warn(ctx, member, reason="Breaking rules")

    assert str(ctx.guild.id) in main.warnings_data
    assert str(member.id) in main.warnings_data[str(ctx.guild.id)]
    assert main.warnings_data[str(ctx.guild.id)][str(member.id)][0]["reason"] == "Breaking rules"

@pytest.mark.asyncio
async def test_kick_command():
    ctx = MagicMock()
    member = AsyncMock()
    ctx.guild.id = 987654321
    ctx.author = MagicMock(name="Moderator", id=111)
    member.id = 222
    member.mention = "@UserToKick"
    member.kick = AsyncMock()
    main.actions_data.clear()

    await main.kick(ctx, member, reason="Violation")
    member.kick.assert_awaited_with(reason="Violation")
    assert str(ctx.guild.id) in main.actions_data
    assert str(member.id) in main.actions_data[str(ctx.guild.id)]
    assert main.actions_data[str(ctx.guild.id)][str(member.id)][-1]["type"] == "kick"

@pytest.mark.asyncio
async def test_mute_command_with_duration():
    ctx = MagicMock()
    member = MagicMock()
    mute_role = MagicMock()
    ctx.guild.id = 123
    ctx.guild.roles = [mute_role]
    mute_role.name = "Muted"
    ctx.guild.channels = []
    mute_role in member.roles
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    ctx.author = MagicMock(name="Mod", id=999)
    member.id = 888
    member.mention = "@User"
    main.actions_data.clear()

    async def fake_sleep(x): pass
    asyncio.sleep = fake_sleep

    await main.mute(ctx, member, duration="10s", reason="Spamming")
    member.add_roles.assert_awaited()
    member.remove_roles.assert_awaited()
    assert str(ctx.guild.id) in main.actions_data
    assert str(member.id) in main.actions_data[str(ctx.guild.id)]
    assert main.actions_data[str(ctx.guild.id)][str(member.id)][-1]["type"] == "unmute"


def test_detect_discord_service_unavailable_from_wrapped_error():
    wrapped = main.commands.CommandInvokeError(
        RuntimeError(
            "DiscordServerError: 503 Service Unavailable (error code: 0): "
            "upstream connect error"
        )
    )
    assert main.is_discord_service_unavailable_error(wrapped) is True


def test_do_not_treat_generic_invoke_error_as_service_unavailable():
    wrapped = main.commands.CommandInvokeError(RuntimeError("some unrelated failure"))
    assert main.is_discord_service_unavailable_error(wrapped) is False


@pytest.mark.asyncio
async def test_coinflip_error_hides_wrapped_503_details():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.interaction = None
    wrapped = main.commands.CommandInvokeError(
        RuntimeError("503 Service Unavailable: upstream connect error")
    )

    await main.coinflip_error(ctx, wrapped)

    ctx.send.assert_awaited_once_with(main.DISCORD_SERVICE_UNAVAILABLE_MESSAGE)


@pytest.mark.asyncio
async def test_coinflip_zero_bet_does_not_award_xp(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 123
    ctx.author.id = 456
    ctx.author.mention = "@Tester"
    ctx.command = MagicMock()
    ctx.command.name = "coinflip"
    ctx.send = AsyncMock()

    mock_xp_col = MagicMock()
    mock_xp_col.update_one = AsyncMock()

    monkeypatch.setattr(main, "xp_col", mock_xp_col)
    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value={"wallet": 100}))

    await main.coinflip(ctx, "0")

    ctx.send.assert_awaited_once_with("❌ Invalid amount to coin flip.")
    mock_xp_col.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticketclose_slash_prompt_is_public(monkeypatch):
    opener = SimpleNamespace(mention="@opener")
    guild = SimpleNamespace(id=123, get_member=MagicMock(return_value=opener))
    channel = SimpleNamespace(id=456, guild=guild)

    response = SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)

    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        interaction=interaction,
        send=AsyncMock(),
    )

    fake_tickets_col = SimpleNamespace(
        find_one=AsyncMock(return_value={"_id": "ticket-1", "owner_id": "999"}),
        update_one=AsyncMock(),
    )
    monkeypatch.setattr(main, "tickets_col", fake_tickets_col)

    await main.ticketclose.callback(ctx)

    response.send_message.assert_awaited_once()
    send_kwargs = response.send_message.await_args.kwargs
    assert send_kwargs["ephemeral"] is False
    assert "confirm closing this ticket" in send_kwargs["content"]
    followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticketclose_slash_prompt_uses_public_followup_when_response_done(monkeypatch):
    opener = SimpleNamespace(mention="@opener")
    guild = SimpleNamespace(id=123, get_member=MagicMock(return_value=opener))
    channel = SimpleNamespace(id=456, guild=guild)

    response = SimpleNamespace(is_done=MagicMock(return_value=True), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)

    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        interaction=interaction,
        send=AsyncMock(),
    )

    fake_tickets_col = SimpleNamespace(
        find_one=AsyncMock(return_value={"_id": "ticket-1", "owner_id": "999"}),
        update_one=AsyncMock(),
    )
    monkeypatch.setattr(main, "tickets_col", fake_tickets_col)

    await main.ticketclose.callback(ctx)

    followup.send.assert_awaited_once()
    send_kwargs = followup.send.await_args.kwargs
    assert send_kwargs["ephemeral"] is False
    assert "confirm closing this ticket" in send_kwargs["content"]
    response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_hybrid_error_uses_ephemeral_initial_response_for_slash():
    response = SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())

    await main.send_hybrid_error(ctx, content="⚠️ test")

    response.send_message.assert_awaited_once()
    kwargs = response.send_message.await_args.kwargs
    assert kwargs["content"] == "⚠️ test"
    assert kwargs["ephemeral"] is True
    followup.send.assert_not_awaited()
    ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_hybrid_error_uses_ephemeral_followup_when_response_done_for_slash():
    response = SimpleNamespace(is_done=MagicMock(return_value=True), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())

    await main.send_hybrid_error(ctx, content="⚠️ test")

    followup.send.assert_awaited_once()
    kwargs = followup.send.await_args.kwargs
    assert kwargs["content"] == "⚠️ test"
    assert kwargs["ephemeral"] is True
    response.send_message.assert_not_awaited()
    ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticketclose_slash_error_is_ephemeral(monkeypatch):
    guild = SimpleNamespace(id=123, get_member=MagicMock())
    channel = SimpleNamespace(id=456, guild=guild)

    response = SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)

    ctx = SimpleNamespace(
        guild=guild,
        channel=channel,
        interaction=interaction,
        send=AsyncMock(),
    )

    fake_tickets_col = SimpleNamespace(
        find_one=AsyncMock(side_effect=RuntimeError("db failed")),
        update_one=AsyncMock(),
    )
    monkeypatch.setattr(main, "tickets_col", fake_tickets_col)

    await main.ticketclose.callback(ctx)

    response.send_message.assert_awaited_once()
    kwargs = response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs.get("embed") is not None
    followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_investstatus_handles_legacy_timestamp_without_date(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456, display_name="Tester")
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    class _Cursor:
        async def to_list(self, length=None):
            return [
                {
                    "_id": "inv-1",
                    "user_id": "123-456",
                    "company": "Techify",
                    "amount": 1000,
                    "timestamp": "2026-04-10T10:00:00+00:00",
                    "history": [],
                }
            ]

    investments_col = SimpleNamespace(
        find=MagicMock(return_value=_Cursor()),
        update_one=AsyncMock(),
    )

    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "investments_col", investments_col)

    await main.investstatus.callback(ctx)

    ctx.send.assert_awaited_once()
    sent_embed = ctx.send.await_args.kwargs["embed"]
    assert sent_embed is not None
    assert sent_embed.fields
    assert "Date: <t:" in sent_embed.fields[0].value


@pytest.mark.asyncio
async def test_invest_slash_respects_active_investment_limit(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value={"wallet": 5000}))
    monkeypatch.setattr(main, "create_investment", AsyncMock())
    monkeypatch.setattr(main, "subtract_balance", AsyncMock())

    investments_col = SimpleNamespace(count_documents=AsyncMock(return_value=5))
    monkeypatch.setattr(main, "investments_col", investments_col)

    await main.invest.callback(ctx, "Techify", "500")

    ctx.send.assert_awaited_once_with(
        "❌ You can only have up to **5 active investments** at a time. Sell some before investing again."
    )
    main.create_investment.assert_not_awaited()
    main.subtract_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_sticky_note_doc_queries_legacy_and_canonical_ids(monkeypatch):
    expected_doc = {"_id": "sticky-1", "guild": 123, "channel": 456, "text": "hello", "message": 111}

    class _StickyCol:
        async def find_one(self, query):
            assert "$or" in query
            assert {"guild": "123", "channel": "456"} in query["$or"]
            assert {"guild": 123, "channel": 456} in query["$or"]
            return expected_doc

    monkeypatch.setattr(main, "sticky_col", _StickyCol())
    doc = await main.find_sticky_note_doc(123, 456)
    assert doc == expected_doc


@pytest.mark.asyncio
async def test_unstickynote_removes_doc_and_cache(monkeypatch):
    channel = SimpleNamespace(id=456)
    message = SimpleNamespace(delete=AsyncMock())
    channel.fetch_message = AsyncMock(return_value=message)
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=123),
        channel=channel,
        send=AsyncMock(),
    )

    main.last_sticky_msg[456] = 99999
    monkeypatch.setattr(
        main,
        "find_sticky_note_doc",
        AsyncMock(return_value={"_id": "sticky-1", "message": 99999}),
    )
    monkeypatch.setattr(main, "sticky_col", SimpleNamespace(delete_one=AsyncMock()))

    await main.unstickynote.callback(ctx)

    main.sticky_col.delete_one.assert_awaited_once_with({"_id": "sticky-1"})
    assert 456 not in main.last_sticky_msg
    ctx.send.assert_awaited_with("✅ Sticky note removed.")


@pytest.mark.asyncio
async def test_xp_earn_skips_xp_when_command_sends_error(monkeypatch):
    async def _failing_cmd(ctx):
        await ctx.send("❌ You cannot give coins to yourself.")

    decorated = main.xp_earn(5, 5)(_failing_cmd)
    fake_xp_col = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(main, "xp_col", fake_xp_col)

    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=123),
        author=SimpleNamespace(id=456, mention="@tester"),
        command=SimpleNamespace(name="give"),
        send=AsyncMock(),
    )

    await decorated(ctx)

    fake_xp_col.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_xp_earn_awards_xp_on_success(monkeypatch):
    async def _successful_cmd(ctx):
        await ctx.send("✅ Success")

    decorated = main.xp_earn(7, 7)(_successful_cmd)
    fake_xp_col = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(main, "xp_col", fake_xp_col)

    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=123),
        author=SimpleNamespace(id=456, mention="@tester"),
        command=SimpleNamespace(name="work"),
        send=AsyncMock(),
    )

    await decorated(ctx)

    fake_xp_col.update_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_dig_requires_shovel_and_resets_cooldown(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    command = SimpleNamespace(reset_cooldown=MagicMock())
    ctx = SimpleNamespace(guild=guild, author=author, command=command, send=AsyncMock())

    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value={"inventory": []}))
    monkeypatch.setattr(main, "economy_col", SimpleNamespace(update_one=AsyncMock()))

    await main.dig.callback(ctx)

    command.reset_cooldown.assert_called_once_with(ctx)
    ctx.send.assert_awaited_once_with("🪏 You need a shovel to dig!")
    main.economy_col.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_dig_adds_rock_to_inventory(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    command = SimpleNamespace(reset_cooldown=MagicMock())
    ctx = SimpleNamespace(guild=guild, author=author, command=command, send=AsyncMock())

    monkeypatch.setattr(main, "check_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "get_user", AsyncMock(return_value={"inventory": ["shovel"]}))
    monkeypatch.setattr(main.random, "choice", MagicMock(return_value=("amber shard", 240)))
    monkeypatch.setattr(main, "economy_col", SimpleNamespace(update_one=AsyncMock()))

    await main.dig.callback(ctx)

    main.economy_col.update_one.assert_awaited_once()
    sent_content = ctx.send.await_args.args[0]
    assert "amber shard" in sent_content
    command.reset_cooldown.assert_not_called()


def test_get_investment_date_handles_invalid_or_missing_values():
    dt_missing = main.get_investment_date({})
    dt_invalid = main.get_investment_date({"date": "not-a-date"})
    dt_legacy = main.get_investment_date({"timestamp": "2026-04-10T10:00:00+00:00"})

    assert dt_missing.tzinfo is not None
    assert dt_invalid.tzinfo is not None
    assert dt_legacy.year == 2026


@pytest.mark.asyncio
async def test_backfill_investment_dates_from_timestamp_is_non_destructive(monkeypatch):
    docs = [
        {"_id": "a", "timestamp": "2026-04-10T10:00:00+00:00"},
        {"_id": "b", "timestamp": "bad-date"},
        {"_id": "c", "date": "2026-04-09T10:00:00+00:00", "timestamp": "2026-04-01T10:00:00+00:00"},
    ]

    class _Result:
        def __init__(self, modified_count):
            self.modified_count = modified_count

    class _Cursor:
        def __init__(self, data):
            self._iter = iter(data)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    class _InvestmentsCol:
        def __init__(self, data):
            self.data = data

        def find(self, query):
            filtered = [d for d in self.data if "date" not in d and "timestamp" in d]
            return _Cursor(filtered)

        async def update_one(self, query, update):
            for d in self.data:
                if d.get("_id") == query.get("_id") and "date" not in d:
                    d["date"] = update["$set"]["date"]
                    return _Result(1)
            return _Result(0)

    fake_col = _InvestmentsCol(docs)
    monkeypatch.setattr(main, "investments_col", fake_col)

    stats = await main.backfill_investment_dates_from_timestamp()

    assert stats["scanned"] == 2
    assert stats["updated"] == 1
    assert stats["invalid_timestamp"] == 1
    assert docs[0].get("date") == "2026-04-10T10:00:00+00:00"
    assert docs[1].get("date") is None
    assert docs[2]["date"] == "2026-04-09T10:00:00+00:00"


@pytest.mark.asyncio
async def test_investmigrate_reports_backfill_stats(monkeypatch):
    monkeypatch.setattr(
        main,
        "backfill_investment_dates_from_timestamp",
        AsyncMock(return_value={
            "scanned": 4,
            "updated": 3,
            "invalid_timestamp": 1,
            "skipped_conflict": 0,
            "write_errors": 0,
        }),
    )

    ctx = SimpleNamespace(send=AsyncMock())
    await main.investmigrate.callback(ctx)

    ctx.send.assert_awaited_once()
    content = ctx.send.await_args.args[0]
    assert "Updated: `3`" in content
    assert "Write errors: `0`" in content

if __name__ == '__main__':
    pytest.main([__file__])
