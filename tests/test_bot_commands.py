import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
import main

@pytest.mark.asyncio
async def test_warn_command():
    ctx = MagicMock()
    member = MagicMock()
    ctx.guild.id = 123456789
    ctx.author = MagicMock(name='Moderator', id=111)
    member.id = 222
    member.mention = '@TestUser'
    member.send = AsyncMock()
    main.warnings_data.clear()
    await main.warn(ctx, member, reason='Breaking rules')
    assert str(ctx.guild.id) in main.warnings_data
    assert str(member.id) in main.warnings_data[str(ctx.guild.id)]
    assert main.warnings_data[str(ctx.guild.id)][str(member.id)][0]['reason'] == 'Breaking rules'

@pytest.mark.asyncio
async def test_kick_command(monkeypatch):
    ctx = MagicMock()
    member = AsyncMock()
    ctx.guild.id = 987654321
    ctx.author = MagicMock(name='Moderator', id=111)
    member.id = 222
    member.mention = '@UserToKick'
    member.kick = AsyncMock()
    main.actions_data.clear()
    monkeypatch.setattr(main, 'check_target_permission', lambda ctx, m: None)
    await main.kick(ctx, member, reason='Violation')
    member.kick.assert_awaited_with(reason='Violation')
    assert str(ctx.guild.id) in main.actions_data
    assert str(member.id) in main.actions_data[str(ctx.guild.id)]
    assert main.actions_data[str(ctx.guild.id)][str(member.id)][-1]['type'] == 'kick'

@pytest.mark.asyncio
async def test_mute_command_with_duration(monkeypatch):
    ctx = MagicMock()
    member = MagicMock()
    mute_role = MagicMock()
    ctx.guild.id = 123
    ctx.guild.roles = [mute_role]
    mute_role.name = 'Muted'
    ctx.guild.channels = []
    member.roles = []
    member.add_roles = AsyncMock()
    ctx.author = MagicMock(name='Mod', id=999)
    member.id = 888
    member.mention = '@User'
    main.actions_data.clear()
    monkeypatch.setattr(main, 'check_target_permission', lambda ctx, m: None)
    monkeypatch.setattr(main.mutes_col, 'update_one', AsyncMock())
    await main.mute(ctx, member, duration='10s', reason='Spamming')
    member.add_roles.assert_awaited()
    main.mutes_col.update_one.assert_awaited_once()
    assert str(ctx.guild.id) in main.actions_data
    assert str(member.id) in main.actions_data[str(ctx.guild.id)]
    assert main.actions_data[str(ctx.guild.id)][str(member.id)][-1]['type'] == 'mute'

def test_detect_discord_service_unavailable_from_wrapped_error():
    wrapped = main.commands.CommandInvokeError(RuntimeError('DiscordServerError: 503 Service Unavailable (error code: 0): upstream connect error'))
    assert main.is_discord_service_unavailable_error(wrapped) is True

def test_do_not_treat_generic_invoke_error_as_service_unavailable():
    wrapped = main.commands.CommandInvokeError(RuntimeError('some unrelated failure'))
    assert main.is_discord_service_unavailable_error(wrapped) is False

@pytest.mark.asyncio
async def test_coinflip_error_hides_wrapped_503_details():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.interaction = None
    wrapped = main.commands.CommandInvokeError(RuntimeError('503 Service Unavailable: upstream connect error'))
    await main.coinflip_error(ctx, wrapped)
    ctx.send.assert_awaited_once_with(main.DISCORD_SERVICE_UNAVAILABLE_MESSAGE)

@pytest.mark.asyncio
async def test_coinflip_zero_bet_does_not_award_xp(monkeypatch):
    ctx = MagicMock()
    ctx.guild.id = 123
    ctx.author.id = 456
    ctx.author.mention = '@Tester'
    ctx.command = MagicMock()
    ctx.command.name = 'coinflip'
    ctx.send = AsyncMock()
    mock_xp_col = MagicMock()
    mock_xp_col.update_one = AsyncMock()
    monkeypatch.setattr(main, 'xp_col', mock_xp_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value={'wallet': 100}))
    await main.coinflip(ctx, '0')
    ctx.send.assert_awaited_once_with('❌ Invalid amount to coin flip.')
    mock_xp_col.update_one.assert_not_awaited()

@pytest.mark.asyncio
async def test_ticketclose_slash_prompt_is_public(monkeypatch):
    opener = SimpleNamespace(mention='@opener')
    guild = SimpleNamespace(id=123, get_member=MagicMock(return_value=opener))
    channel = SimpleNamespace(id=456, guild=guild)
    response = SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(guild=guild, channel=channel, interaction=interaction, send=AsyncMock())
    fake_tickets_col = SimpleNamespace(find_one=AsyncMock(return_value={'_id': 'ticket-1', 'owner_id': '999'}), update_one=AsyncMock())
    monkeypatch.setattr(main, 'tickets_col', fake_tickets_col)
    await main.ticketclose.callback(ctx)
    response.send_message.assert_awaited_once()
    send_kwargs = response.send_message.await_args.kwargs
    assert send_kwargs['ephemeral'] is False
    assert 'confirm closing this ticket' in send_kwargs['content']
    followup.send.assert_not_awaited()

@pytest.mark.asyncio
async def test_ticketclose_slash_prompt_uses_public_followup_when_response_done(monkeypatch):
    opener = SimpleNamespace(mention='@opener')
    guild = SimpleNamespace(id=123, get_member=MagicMock(return_value=opener))
    channel = SimpleNamespace(id=456, guild=guild)
    response = SimpleNamespace(is_done=MagicMock(return_value=True), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(guild=guild, channel=channel, interaction=interaction, send=AsyncMock())
    fake_tickets_col = SimpleNamespace(find_one=AsyncMock(return_value={'_id': 'ticket-1', 'owner_id': '999'}), update_one=AsyncMock())
    monkeypatch.setattr(main, 'tickets_col', fake_tickets_col)
    await main.ticketclose.callback(ctx)
    followup.send.assert_awaited_once()
    send_kwargs = followup.send.await_args.kwargs
    assert send_kwargs['ephemeral'] is False
    assert 'confirm closing this ticket' in send_kwargs['content']
    response.send_message.assert_not_awaited()

@pytest.mark.asyncio
async def test_send_hybrid_error_uses_ephemeral_initial_response_for_slash():
    response = SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())
    await main.send_hybrid_error(ctx, content='⚠️ test')
    response.send_message.assert_awaited_once()
    kwargs = response.send_message.await_args.kwargs
    assert kwargs['content'] == '⚠️ test'
    assert kwargs['ephemeral'] is True
    followup.send.assert_not_awaited()
    ctx.send.assert_not_awaited()

@pytest.mark.asyncio
async def test_send_hybrid_error_uses_ephemeral_followup_when_response_done_for_slash():
    response = SimpleNamespace(is_done=MagicMock(return_value=True), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())
    await main.send_hybrid_error(ctx, content='⚠️ test')
    followup.send.assert_awaited_once()
    kwargs = followup.send.await_args.kwargs
    assert kwargs['content'] == '⚠️ test'
    assert kwargs['ephemeral'] is True
    response.send_message.assert_not_awaited()
    ctx.send.assert_not_awaited()

@pytest.mark.asyncio
async def test_ticketclose_slash_error_is_ephemeral(monkeypatch):
    guild = SimpleNamespace(id=123, get_member=MagicMock())
    channel = SimpleNamespace(id=456, guild=guild)
    response = SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(response=response, followup=followup)
    ctx = SimpleNamespace(guild=guild, channel=channel, interaction=interaction, send=AsyncMock())
    fake_tickets_col = SimpleNamespace(find_one=AsyncMock(side_effect=RuntimeError('db failed')), update_one=AsyncMock())
    monkeypatch.setattr(main, 'tickets_col', fake_tickets_col)
    await main.ticketclose.callback(ctx)
    response.send_message.assert_awaited_once()
    kwargs = response.send_message.await_args.kwargs
    assert kwargs['ephemeral'] is True
    assert kwargs.get('embed') is not None
    followup.send.assert_not_awaited()

@pytest.mark.asyncio
async def test_investstatus_handles_legacy_timestamp_without_date(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456, display_name='Tester')
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    class _Cursor:

        async def to_list(self, length=None):
            return [{'_id': 'inv-1', 'user_id': '123-456', 'company': 'Techify', 'amount': 1000, 'timestamp': '2026-04-10T10:00:00+00:00', 'history': []}]
    investments_col = SimpleNamespace(find=MagicMock(return_value=_Cursor()), update_one=AsyncMock())
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'investments_col', investments_col)
    await main.investstatus.callback(ctx)
    ctx.send.assert_awaited_once()
    sent_embed = ctx.send.await_args.kwargs['embed']
    assert sent_embed is not None
    assert sent_embed.fields
    assert 'Date: <t:' in sent_embed.fields[0].value

@pytest.mark.asyncio
async def test_invest_slash_respects_active_investment_limit(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value={'wallet': 5000}))
    monkeypatch.setattr(main, 'create_investment', AsyncMock())
    monkeypatch.setattr(main, 'subtract_balance', AsyncMock())
    investments_col = SimpleNamespace(count_documents=AsyncMock(return_value=5))
    monkeypatch.setattr(main, 'investments_col', investments_col)
    await main.invest.callback(ctx, 'Techify', '500')
    ctx.send.assert_awaited_once_with('❌ You can only have up to **5 active investments** at a time. Sell some before investing again.')
    main.create_investment.assert_not_awaited()
    main.subtract_balance.assert_not_awaited()

@pytest.mark.asyncio
async def test_calculate_investment_value_prefers_current_value():
    inv = {'_id': 'inv-1', 'company': 'Oceanic', 'amount': 25000, 'current_value': 24250, 'date': '2026-01-01T00:00:00+00:00', 'history': [999999, -500000]}
    value = await main.calculate_investment_value(inv)
    assert value == 24250

@pytest.mark.asyncio
async def test_refresh_user_investments_for_today_updates_only_once(monkeypatch):
    today = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    investments = [{'_id': 'inv-a', 'amount': 1000, 'current_value': 1000, 'history': []}, {'_id': 'inv-b', 'amount': 2000, 'current_value': 2000, 'history': [], 'last_status_refresh_date': '2026-05-13'}]
    monkeypatch.setattr(main, 'pick_daily_investment_change_pct', lambda: 0.02)
    update_one = AsyncMock()
    monkeypatch.setattr(main, 'investments_col', SimpleNamespace(update_one=update_one))
    refreshed = await main.refresh_user_investments_for_today(investments, now=today)
    assert refreshed[0]['current_value'] == 1020
    assert refreshed[0]['last_status_refresh_date'] == '2026-05-13'
    assert refreshed[1]['current_value'] == 2000
    update_one.assert_awaited_once()

@pytest.mark.asyncio
async def test_find_sticky_note_doc_queries_legacy_and_canonical_ids(monkeypatch):
    expected_doc = {'_id': 'sticky-1', 'guild': 123, 'channel': 456, 'text': 'hello', 'message': 111}

    class _StickyCol:

        async def find_one(self, query):
            assert '$or' in query
            assert {'guild': '123', 'channel': '456'} in query['$or']
            assert {'guild': 123, 'channel': 456} in query['$or']
            return expected_doc
    monkeypatch.setattr(main, 'sticky_col', _StickyCol())
    doc = await main.find_sticky_note_doc(123, 456)
    assert doc == expected_doc

@pytest.mark.asyncio
async def test_unstickynote_removes_doc_and_cache(monkeypatch):
    channel = SimpleNamespace(id=456)
    message = SimpleNamespace(delete=AsyncMock())
    channel.fetch_message = AsyncMock(return_value=message)
    ctx = SimpleNamespace(guild=SimpleNamespace(id=123), channel=channel, send=AsyncMock())
    main.last_sticky_msg[456] = 99999
    monkeypatch.setattr(main, 'find_sticky_note_doc', AsyncMock(return_value={'_id': 'sticky-1', 'message': 99999}))
    monkeypatch.setattr(main, 'sticky_col', SimpleNamespace(delete_one=AsyncMock()))
    await main.unstickynote.callback(ctx)
    main.sticky_col.delete_one.assert_awaited_once_with({'_id': 'sticky-1'})
    assert 456 not in main.last_sticky_msg
    ctx.send.assert_awaited_with('✅ Sticky note removed.')

@pytest.mark.asyncio
async def test_xp_earn_skips_xp_when_command_sends_error(monkeypatch):

    async def _failing_cmd(ctx):
        await ctx.send('❌ You cannot give coins to yourself.')
    decorated = main.xp_earn(5, 5)(_failing_cmd)
    fake_xp_col = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(main, 'xp_col', fake_xp_col)
    ctx = SimpleNamespace(guild=SimpleNamespace(id=123), author=SimpleNamespace(id=456, mention='@tester'), command=SimpleNamespace(name='give'), send=AsyncMock())
    await decorated(ctx)
    fake_xp_col.update_one.assert_not_awaited()

@pytest.mark.asyncio
async def test_xp_earn_awards_xp_on_success(monkeypatch):

    async def _successful_cmd(ctx):
        await ctx.send('✅ Success')
    decorated = main.xp_earn(7, 7)(_successful_cmd)
    fake_xp_col = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(main, 'xp_col', fake_xp_col)
    ctx = SimpleNamespace(guild=SimpleNamespace(id=123), author=SimpleNamespace(id=456, mention='@tester'), command=SimpleNamespace(name='work'), send=AsyncMock())
    await decorated(ctx)
    fake_xp_col.update_one.assert_awaited_once()

@pytest.mark.asyncio
async def test_dig_requires_shovel_and_resets_cooldown(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    command = SimpleNamespace(reset_cooldown=MagicMock())
    ctx = SimpleNamespace(guild=guild, author=author, command=command, send=AsyncMock())
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value={'inventory': []}))
    monkeypatch.setattr(main, 'economy_col', SimpleNamespace(update_one=AsyncMock()))
    await main.dig.callback(ctx)
    command.reset_cooldown.assert_called_once_with(ctx)
    ctx.send.assert_awaited_once_with('🪏 You need a shovel to dig!')
    main.economy_col.update_one.assert_not_awaited()

@pytest.mark.asyncio
async def test_dig_adds_rock_to_inventory(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    command = SimpleNamespace(reset_cooldown=MagicMock())
    ctx = SimpleNamespace(guild=guild, author=author, command=command, send=AsyncMock())
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value={'inventory': ['shovel']}))
    monkeypatch.setattr(main.random, 'choice', MagicMock(return_value=('amber shard', 240)))
    monkeypatch.setattr(main, 'economy_col', SimpleNamespace(update_one=AsyncMock()))
    await main.dig.callback(ctx)
    main.economy_col.update_one.assert_awaited_once()
    sent_content = ctx.send.await_args.args[0]
    assert 'amber shard' in sent_content
    command.reset_cooldown.assert_not_called()

@pytest.mark.asyncio
async def test_bugcatch_requires_butterfly_net_and_resets_cooldown(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    command = SimpleNamespace(reset_cooldown=MagicMock())
    ctx = SimpleNamespace(guild=guild, author=author, command=command, send=AsyncMock())
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value={'inventory': []}))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(main, 'economy_col', SimpleNamespace(update_one=AsyncMock()))
    await main.bugcatch.callback(ctx)
    command.reset_cooldown.assert_called_once_with(ctx)
    sent_content = ctx.send.await_args.args[0]
    assert 'Butterfly Net' in sent_content
    main.add_balance.assert_not_awaited()

@pytest.mark.asyncio
async def test_bugcatch_sells_immediately_and_breaks_net(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456)
    command = SimpleNamespace(reset_cooldown=MagicMock())
    ctx = SimpleNamespace(guild=guild, author=author, command=command, send=AsyncMock())
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value={'inventory': [{'_id': 'butterfly net', 'uses_left': 1}]}))
    monkeypatch.setattr(main.random, 'choice', MagicMock(return_value=('🦋 butterfly', 180)))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(main, 'economy_col', SimpleNamespace(update_one=AsyncMock()))
    await main.bugcatch.callback(ctx)
    main.add_balance.assert_awaited_once_with(456, 123, 180)
    main.economy_col.update_one.assert_awaited_once_with({'_id': '123-456'}, {'$set': {'inventory': []}})
    sent_content = ctx.send.await_args.args[0]
    assert 'sold it immediately' in sent_content
    assert 'Butterfly Net' in sent_content
    command.reset_cooldown.assert_not_called()

def test_consume_tool_use_breaks_and_removes_tool():
    inventory = [{'_id': 'lockpick', 'uses_left': 1}]
    consumed, broke, uses_left = main.consume_tool_use(inventory, 'lockpick')
    assert consumed is True
    assert broke is True
    assert uses_left == 0
    assert inventory == []

@pytest.mark.asyncio
async def test_inventory_shows_tool_durability(monkeypatch):
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=456, display_name='Tester')
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value={'inventory': [{'_id': 'shovel', 'uses_left': 100}, {'_id': 'pet_duck', 'uses_left': 2}]}))

    class _ShopCol:

        async def find_one(self, query):
            if query == {'_id': 'pet_duck'}:
                return {'name': 'Pet Duck', 'description': 'Duck buddy'}
            if query == {'name_lower': 'shovel'}:
                return {'name': 'Shovel', 'description': 'Tool'}
            return None
    monkeypatch.setattr(main, 'shop_col', _ShopCol())
    await main.inventory.callback(ctx)
    embed = ctx.send.await_args.kwargs['embed']
    assert embed is not None
    assert any(('Shovel' in field.name for field in embed.fields))
    assert any(('100/336' in field.value for field in embed.fields))

def test_get_investment_date_handles_invalid_or_missing_values():
    dt_missing = main.get_investment_date({})
    dt_invalid = main.get_investment_date({'date': 'not-a-date'})
    dt_legacy = main.get_investment_date({'timestamp': '2026-04-10T10:00:00+00:00'})
    assert dt_missing.tzinfo is not None
    assert dt_invalid.tzinfo is not None
    assert dt_legacy.year == 2026
if __name__ == '__main__':
    pytest.main([__file__])