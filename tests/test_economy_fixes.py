from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import main


class FakeEconomyCol:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update, upsert=False):
        self.calls.append((query, update, upsert))


class FakeShopCol:
    def __init__(self, guild_item=None, default_item=None):
        self.guild_item = guild_item
        self.default_item = default_item

    async def find_one(self, query):
        if "guild" in query:
            return self.guild_item
        return self.default_item


@pytest.mark.asyncio
async def test_process_shop_refund_custom_item_success(monkeypatch):
    member = SimpleNamespace(id=42)
    guild = SimpleNamespace(id=123)
    user_data = {"wallet": 100, "inventory": ["mystery box"]}
    economy = FakeEconomyCol()

    guild_item = {
        "_id": "123-mystery_box",
        "guild": "123",
        "name": "Mystery Box",
        "name_lower": "mystery box",
        "price": 500,
    }

    monkeypatch.setattr(main, "economy_col", economy)
    monkeypatch.setattr(main, "guild_shop_col", FakeShopCol(guild_item=guild_item))
    monkeypatch.setattr(main, "shop_col", FakeShopCol(default_item=None))

    result = await main.process_shop_refund(member, guild, "mystery box", user_data)

    assert result["ok"] is True
    assert result["refund_amount"] == 250
    assert result["new_wallet"] == 350
    assert economy.calls == [
        ({"_id": "123-42"}, {"$set": {"wallet": 350, "inventory": []}}, False)
    ]


@pytest.mark.asyncio
async def test_process_shop_refund_fails_if_missing_from_inventory(monkeypatch):
    member = SimpleNamespace(id=42)
    guild = SimpleNamespace(id=123)
    user_data = {"wallet": 100, "inventory": []}

    monkeypatch.setattr(main, "guild_shop_col", FakeShopCol(guild_item=None))
    monkeypatch.setattr(main, "shop_col", FakeShopCol(default_item=None))

    result = await main.process_shop_refund(member, guild, "mystery box", user_data)

    assert result["ok"] is False
    assert "not in your inventory" in result["message"].lower()


@pytest.mark.asyncio
async def test_ping_ticket_roles_mentions_staff_with_access(monkeypatch):
    opener = SimpleNamespace(id=1, mention="<@1>", bot=False)
    permitted_member = SimpleNamespace(id=2, mention="<@2>", bot=False)
    category_member = SimpleNamespace(id=4, mention="<@4>", bot=False)
    other_member = SimpleNamespace(id=3, mention="<@3>", bot=False)
    staff_role = SimpleNamespace(id=99, mention="<@&99>")

    class FakeGuild:
        def __init__(self):
            self.id = 123

        def get_role(self, role_id):
            return staff_role if role_id == 99 else None

    guild = FakeGuild()
    sent_messages = []
    sent_message = SimpleNamespace(delete=AsyncMock())

    class FakeChannel:
        def __init__(self):
            self.id = 777
            self.guild = guild
            self.members = [opener, permitted_member, other_member]

        def permissions_for(self, member):
            return SimpleNamespace(view_channel=member.id == 2)

        async def send(self, content=None, allowed_mentions=None):
            sent_messages.append((content, allowed_mentions))
            return sent_message

    channel = FakeChannel()

    monkeypatch.setattr(main.tickets_col, "find_one", AsyncMock(return_value={"category": "support"}))
    monkeypatch.setattr(main.settings_col, "find_one", AsyncMock(return_value={"staff_role": 99}))
    monkeypatch.setattr(main, "get_category_support_members", AsyncMock(return_value=[category_member]))

    await main.ping_ticket_roles(channel, "123", opener_id=1)

    assert len(sent_messages) == 1
    content, _ = sent_messages[0]
    assert "<@&99>" in content
    assert "<@2>" in content
    assert "<@4>" in content
    assert "<@1>" not in content
    sent_message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_fish_resets_cooldown_when_tool_missing(monkeypatch):
    async def fake_check_channel(*args, **kwargs):
        return True

    async def fake_get_user(*args, **kwargs):
        return {"wallet": 0, "bank": 0, "inventory": []}

    sent = []

    async def fake_send(message):
        sent.append(message)

    reset_cooldown = Mock()
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=123),
        author=SimpleNamespace(id=42),
        command=SimpleNamespace(name="fish", reset_cooldown=reset_cooldown),
        send=AsyncMock(side_effect=fake_send),
        interaction=None,
    )

    monkeypatch.setattr(main, "check_channel", fake_check_channel)
    monkeypatch.setattr(main, "get_user", fake_get_user)

    await main.fish.callback(ctx)

    reset_cooldown.assert_called_once_with(ctx)
    assert sent
    assert "need a fishing rod" in sent[0].lower()


@pytest.mark.asyncio
async def test_ensure_badge_role_for_guild_creates_missing_role():
    created_role = SimpleNamespace(id=777, name="🎣 First Cast")
    guild = SimpleNamespace(
        roles=[],
        create_role=AsyncMock(return_value=created_role),
    )
    badge = {"emoji": "🎣", "name": "First Cast"}

    role = await main.ensure_badge_role_for_guild(guild, badge)

    assert role is created_role
    guild.create_role.assert_awaited_once()

