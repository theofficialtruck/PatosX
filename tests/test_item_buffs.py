"""Tests for coffee cup, energy drink, and lucky cookie buffs."""
import pytest
from unittest.mock import AsyncMock, MagicMock
import main


# ── pop_food_item helper ──────────────────────────────────────────────────────

def test_pop_food_item_finds_space_key():
    """Items stored with spaces are found by underscore item_id."""
    inv = ['energy drink']
    result = main.pop_food_item(inv, 'energy_drink')
    assert result is True
    assert inv == []


def test_pop_food_item_space_stored_key_not_underscore():
    """Items stored with underscores are NOT matched (shop stores with spaces via name_lower)."""
    inv = ['energy_drink']  # underscore — would not be stored this way normally
    result = main.pop_food_item(inv, 'energy_drink')
    # normalize converts item_id to 'energy drink'; stored 'energy_drink' != 'energy drink'
    assert result is False


def test_pop_food_item_returns_false_when_missing():
    inv = ['fishing rod', 'laptop']
    result = main.pop_food_item(inv, 'energy_drink')
    assert result is False
    assert len(inv) == 2


def test_pop_food_item_only_removes_one():
    """Only one item should be removed even if multiple are present."""
    inv = ['lucky cookie', 'lucky cookie']
    result = main.pop_food_item(inv, 'lucky_cookie')
    assert result is True
    assert len(inv) == 1


def test_pop_food_item_leaves_other_items():
    inv = ['coffee cup', 'fishing rod', 'laptop']
    main.pop_food_item(inv, 'coffee_cup')
    assert inv == ['fishing rod', 'laptop']


def test_pop_food_item_dict_with_space_name_lower():
    """Dict items with space name_lower are matched correctly."""
    # This is how a dict food item would be stored if ever created with name_lower
    inv = [{'_id': 'energy drink', 'uses_left': 1}, 'laptop']
    result = main.pop_food_item(inv, 'energy_drink')
    assert result is True
    assert inv == ['laptop']


# ── check_and_use_food_item key normalization ─────────────────────────────────

@pytest.mark.asyncio
async def test_check_and_use_food_item_normalizes_underscore_to_space(monkeypatch):
    """check_and_use_food_item finds item stored as 'energy drink' when searching 'energy_drink'."""
    user_data = {'_id': '1-2', 'inventory': ['energy drink']}
    mock_get_user = AsyncMock(return_value=user_data)
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()
    monkeypatch.setattr(main, 'get_user', mock_get_user)
    monkeypatch.setattr(main, 'economy_col', mock_col)

    result = await main.check_and_use_food_item(2, 1, 'energy_drink')

    assert result is True
    mock_col.update_one.assert_awaited_once()
    # inventory should have been saved with the item removed
    saved_inv = mock_col.update_one.call_args[0][1]['$set']['inventory']
    assert saved_inv == []


@pytest.mark.asyncio
async def test_check_and_use_food_item_returns_false_when_not_found(monkeypatch):
    user_data = {'_id': '1-2', 'inventory': ['fishing rod']}
    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()
    monkeypatch.setattr(main, 'economy_col', mock_col)

    result = await main.check_and_use_food_item(2, 1, 'energy_drink')

    assert result is False
    mock_col.update_one.assert_not_awaited()


# ── Work command: energy drink ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_work_energy_drink_sends_message_and_removes_from_inventory(monkeypatch):
    """When inventory has 'energy drink', work command sends the buff message."""
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.author.display_avatar.url = 'https://example.com/av.png'
    ctx.send = AsyncMock()
    ctx.channel.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 500, 'bank': 0,
        'inventory': ['energy drink'], 'job': 'duck',
        'promotion_level': 0, 'last_beg': None,
    }
    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=None)  # no cooldown
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(main, 'check_and_award_badges', AsyncMock())

    await main.work.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any('Energy Drink consumed' in t for t in sent_texts), \
        f"Expected energy drink message, got: {sent_texts}"


@pytest.mark.asyncio
async def test_work_energy_drink_not_consumed_when_still_on_cooldown(monkeypatch):
    """Energy drink is NOT consumed when user is still on cooldown even with reduction."""
    import datetime as dt_module
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    # Inventory has energy drink
    user_data = {
        '_id': '100-200', 'wallet': 0, 'bank': 0,
        'inventory': ['energy drink'], 'job': 'duck', 'promotion_level': 0,
    }
    # Last worked 2h ago — with 50% reduction cooldown is 6h, still 4h remaining
    two_hours_ago = (dt_module.datetime.now(dt_module.timezone.utc) - dt_module.timedelta(hours=2)).isoformat()
    cooldown_data = {'_id': 'work_cooldown_100-200', 'timestamp': two_hours_ago}

    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=cooldown_data)
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))

    await main.work.callback(ctx)

    # Should have returned early with cooldown message
    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any('cooldown' in t.lower() for t in sent_texts), "Expected cooldown message"
    # Energy drink should NOT have been consumed
    assert 'energy drink' in user_data['inventory'], "Energy drink must not be consumed during cooldown reject"


@pytest.mark.asyncio
async def test_work_energy_drink_allows_work_after_half_cooldown(monkeypatch):
    """Energy drink reduces effective cooldown so user can work at 6h instead of waiting 12h."""
    import datetime as dt_module
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.author.display_avatar.url = 'https://example.com/av.png'
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 0, 'bank': 0,
        'inventory': ['energy drink'], 'job': 'duck', 'promotion_level': 0,
    }
    # Worked 7h ago — normally 5h remaining, but with drink cooldown is 6h so 1h elapsed past threshold
    seven_hours_ago = (dt_module.datetime.now(dt_module.timezone.utc) - dt_module.timedelta(hours=7)).isoformat()
    cooldown_data = {'_id': 'work_cooldown_100-200', 'timestamp': seven_hours_ago}

    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=cooldown_data)
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(main, 'check_and_award_badges', AsyncMock())

    await main.work.callback(ctx)

    # Should NOT have shown the "on cooldown" rejection message
    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert not any("You're on cooldown" in t for t in sent_texts), \
        f"Should have bypassed cooldown with energy drink; got: {sent_texts}"
    assert any('Energy Drink consumed' in t for t in sent_texts), \
        "Should confirm energy drink was consumed"


# ── Work command: lucky cookie ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_work_lucky_cookie_doubles_earnings(monkeypatch):
    """lucky_cookie in inventory doubles work earnings and is removed."""
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.author.display_avatar.url = 'https://example.com/av.png'
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 0, 'bank': 0,
        'inventory': ['lucky cookie'], 'job': 'duck', 'promotion_level': 0,
    }

    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=None)
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(main, 'check_and_award_badges', AsyncMock())

    await main.work.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any('Lucky Cookie consumed' in t for t in sent_texts), \
        f"Expected lucky cookie message; got: {sent_texts}"
    # Cookie must have been removed from inventory
    assert 'lucky cookie' not in user_data['inventory'], "Lucky cookie should be removed after use"


@pytest.mark.asyncio
async def test_work_no_buffs_when_inventory_empty(monkeypatch):
    """No buff messages when inventory is empty."""
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.author.display_avatar.url = 'https://example.com/av.png'
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 0, 'bank': 0,
        'inventory': [], 'job': 'duck', 'promotion_level': 0,
    }
    mock_col = MagicMock()
    mock_col.find_one = AsyncMock(return_value=None)
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(main, 'check_and_award_badges', AsyncMock())

    await main.work.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert not any('Energy Drink' in t for t in sent_texts)
    assert not any('Lucky Cookie' in t for t in sent_texts)


# ── Beg command: lucky cookie ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_beg_lucky_cookie_doubles_amount(monkeypatch):
    """lucky_cookie in inventory doubles beg earnings."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 0, 'bank': 0,
        'inventory': ['lucky cookie'], 'last_beg': None,
    }

    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(_random, 'randint', lambda a, b: 100)  # fixed base amount

    await main.beg.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any('Lucky Cookie consumed' in t for t in sent_texts), \
        f"Expected lucky cookie message; got: {sent_texts}"
    # Earnings should be doubled: 100 * 2 = 200
    assert any('200 coins' in t for t in sent_texts), \
        f"Expected doubled earnings (200 coins); got: {sent_texts}"


@pytest.mark.asyncio
async def test_beg_lucky_cookie_removed_from_inventory(monkeypatch):
    """Lucky cookie is removed from inventory after beg."""
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 0, 'bank': 0,
        'inventory': ['lucky cookie'], 'last_beg': None,
    }

    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())

    await main.beg.callback(ctx)

    # Check that update_one was called with the cookie removed
    inv_saves = [
        call.args[1].get('$set', {}).get('inventory')
        for call in mock_col.update_one.call_args_list
        if '$set' in call.args[1] and 'inventory' in call.args[1]['$set']
    ]
    assert any(inv is not None and 'lucky cookie' not in inv for inv in inv_saves), \
        "Inventory save should not contain lucky cookie"


@pytest.mark.asyncio
async def test_beg_no_cookie_message_without_item(monkeypatch):
    """No cookie message when inventory is empty."""
    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 0, 'bank': 0,
        'inventory': [], 'last_beg': None,
    }

    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())

    await main.beg.callback(ctx)

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert not any('Lucky Cookie' in t for t in sent_texts)


# ── Crime command: coffee cup ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crime_coffee_cup_sends_message(monkeypatch):
    """Coffee cup sends consumed message and is removed from inventory."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 2000, 'bank': 0,
        'inventory': ['coffee cup'],
        'last_crime': None,
    }

    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(main, 'subtract_balance', AsyncMock())
    monkeypatch.setattr(_random, 'random', lambda: 0.0)  # always succeed

    await main.crime.callback(ctx, choice='shoplift')

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any('Coffee Cup consumed' in t for t in sent_texts), \
        f"Expected coffee cup message; got: {sent_texts}"


@pytest.mark.asyncio
async def test_crime_coffee_cup_removed_on_success(monkeypatch):
    """Coffee cup is removed from saved inventory on crime success."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 2000, 'bank': 0,
        'inventory': ['coffee cup'],
        'last_crime': None,
    }

    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(_random, 'random', lambda: 0.0)  # success

    await main.crime.callback(ctx, choice='shoplift')

    inv_saves = [
        call.args[1].get('$set', {}).get('inventory')
        for call in mock_col.update_one.call_args_list
        if '$set' in call.args[1] and 'inventory' in call.args[1]['$set']
    ]
    assert any(inv is not None and 'coffee cup' not in inv for inv in inv_saves), \
        "Saved inventory should not contain coffee cup after use"


@pytest.mark.asyncio
async def test_crime_coffee_cup_removed_on_failure(monkeypatch):
    """Coffee cup is removed from saved inventory even on crime failure."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 2000, 'bank': 0,
        'inventory': ['coffee cup'],
        'last_crime': None,
    }

    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(_random, 'random', lambda: 0.99)  # always fail

    await main.crime.callback(ctx, choice='shoplift')

    inv_saves = [
        call.args[1].get('$set', {}).get('inventory')
        for call in mock_col.update_one.call_args_list
        if '$set' in call.args[1] and 'inventory' in call.args[1]['$set']
    ]
    assert any(inv is not None and 'coffee cup' not in inv for inv in inv_saves), \
        "Coffee cup should be removed even on failure"


@pytest.mark.asyncio
async def test_crime_coffee_cup_increases_success_chance(monkeypatch):
    """Coffee cup raises success chance by 25% (shoplift 0.5 → 0.75)."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 2000, 'bank': 0,
        'inventory': ['coffee cup'],
        'last_crime': None,
    }

    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(main, 'subtract_balance', AsyncMock())

    # Use random.random = 0.74 → should succeed with coffee (adjusted 0.75) but fail without (0.50)
    monkeypatch.setattr(_random, 'random', lambda: 0.74)

    await main.crime.callback(ctx, choice='shoplift')

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert any('Crime successful' in t for t in sent_texts), \
        f"Expected success with coffee cup boosting chance to 0.75; got: {sent_texts}"


@pytest.mark.asyncio
async def test_crime_no_coffee_message_without_item(monkeypatch):
    """No coffee cup message when inventory doesn't have it."""
    import random as _random

    ctx = MagicMock()
    ctx.guild.id = 100
    ctx.author.id = 200
    ctx.send = AsyncMock()
    ctx.interaction = None

    user_data = {
        '_id': '100-200', 'wallet': 2000, 'bank': 0,
        'inventory': [],
        'last_crime': None,
    }

    mock_col = MagicMock()
    mock_col.update_one = AsyncMock()

    monkeypatch.setattr(main, 'get_user', AsyncMock(return_value=user_data))
    monkeypatch.setattr(main, 'economy_col', mock_col)
    monkeypatch.setattr(main, 'check_channel', AsyncMock(return_value=True))
    monkeypatch.setattr(main, 'add_balance', AsyncMock())
    monkeypatch.setattr(_random, 'random', lambda: 0.0)

    await main.crime.callback(ctx, choice='shoplift')

    sent_texts = [call.args[0] for call in ctx.send.call_args_list if call.args]
    assert not any('Coffee' in t for t in sent_texts)
