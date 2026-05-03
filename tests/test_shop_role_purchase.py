from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main


class FakeEconomyCol:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update, upsert=False):
        self.calls.append((query, update, upsert))


class FakeGuildShopCol:
    def __init__(self, item):
        self.item = item

    async def find_one(self, query):
        return self.item


@pytest.mark.asyncio
async def test_process_shop_purchase_role_item_grants_role_without_inventory(monkeypatch):
    role = SimpleNamespace(id=555, mention="@PondRoyalty")
    guild = SimpleNamespace(id=123, get_role=lambda role_id: role if role_id == 555 else None)
    member = SimpleNamespace(id=42, roles=[], add_roles=AsyncMock())
    store_item = {
        "_id": "123-pond royalty+",
        "name": "Pond Royalty+",
        "name_lower": "pond royalty+",
        "price": 15_000_000,
        "role_id": 555,
    }

    economy = FakeEconomyCol()
    monkeypatch.setattr(main, "economy_col", economy)

    result = await main.process_shop_purchase(
        member,
        guild,
        store_item,
        {"wallet": 20_000_000, "inventory": []},
    )

    assert result["ok"] is True
    assert result["purchase_type"] == "role"
    assert result["new_wallet"] == 5_000_000
    member.add_roles.assert_awaited_once()
    assert economy.calls == [
        ({"_id": "123-42"}, {"$set": {"wallet": 5_000_000}}, False)
    ]


@pytest.mark.asyncio
async def test_buy_role_item_grants_role_instead_of_inventory(monkeypatch):
    role = SimpleNamespace(id=555, mention="@PondRoyalty")
    guild = SimpleNamespace(id=123, get_role=lambda role_id: role if role_id == 555 else None)
    author = SimpleNamespace(id=42, roles=[], add_roles=AsyncMock())
    sent_messages = []
    store_item = {
        "_id": "123-pond royalty+",
        "guild": "123",
        "name": "Pond Royalty+",
        "name_lower": "pond royalty+",
        "price": 15_000_000,
        "description": "Exclusive premium role for the rich of the rich",
        "role_id": 555,
    }

    async def fake_send(message):
        sent_messages.append(message)

    async def fake_check_channel(*args, **kwargs):
        return True

    async def fake_get_user(*args, **kwargs):
        return {"wallet": 20_000_000, "inventory": []}

    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock(side_effect=fake_send))
    economy = FakeEconomyCol()

    monkeypatch.setattr(main, "check_channel", fake_check_channel)
    monkeypatch.setattr(main, "get_user", fake_get_user)
    monkeypatch.setattr(main, "guild_shop_col", FakeGuildShopCol(store_item))
    monkeypatch.setattr(main, "economy_col", economy)

    await main.buy.callback(ctx, item="Pond Royalty+")

    author.add_roles.assert_awaited_once()
    assert any("got @PondRoyalty" in message for message in sent_messages)
    assert economy.calls == [
        ({"_id": "123-42"}, {"$set": {"wallet": 5_000_000}}, False)
    ]


@pytest.mark.asyncio
async def test_shop_dropdown_role_item_grants_role(monkeypatch):
    role = SimpleNamespace(id=555, mention="@PondRoyalty")
    guild = SimpleNamespace(id=123, get_role=lambda role_id: role if role_id == 555 else None)
    user = SimpleNamespace(id=42, roles=[], add_roles=AsyncMock())
    store_item = {
        "_id": "123-pond royalty+",
        "name": "Pond Royalty+",
        "name_lower": "pond royalty+",
        "price": 15_000_000,
        "role_id": 555,
    }
    sent_payloads = []

    async def fake_send_message(content=None, embed=None, ephemeral=False, view=None):
        sent_payloads.append({"content": content, "embed": embed, "ephemeral": ephemeral, "view": view})

    async def fake_get_user(*args, **kwargs):
        return {"wallet": 20_000_000, "inventory": []}

    response = SimpleNamespace(send_message=AsyncMock(side_effect=fake_send_message))
    followup = SimpleNamespace(edit_message=AsyncMock())
    interaction = SimpleNamespace(
        user=user,
        guild=guild,
        response=response,
        followup=followup,
        message=SimpleNamespace(id=999),
    )

    economy = FakeEconomyCol()
    monkeypatch.setattr(main, "get_user", fake_get_user)
    monkeypatch.setattr(main, "economy_col", economy)

    option = main.discord.SelectOption(label="Pond Royalty+ - 🪙 15000000", value="123-pond royalty+")
    view = main.ShopDropdown(user.id, str(guild.id), [store_item], 20_000_000, [option])
    view.dropdown._values = ["123-pond royalty+"]

    await view.dropdown_callback(interaction)

    user.add_roles.assert_awaited_once()
    assert economy.calls == [
        ({"_id": "123-42"}, {"$set": {"wallet": 5_000_000}}, False)
    ]
    assert sent_payloads, "Expected a response message"
    assert sent_payloads[0]["embed"] is not None
    assert "Role Granted: @PondRoyalty" in sent_payloads[0]["embed"].description


@pytest.mark.asyncio
async def test_process_shop_purchase_durable_tool_gets_uses_left(monkeypatch):
    member = SimpleNamespace(id=42)
    guild = SimpleNamespace(id=123)
    store_item = {
        "_id": "123-fishing rod",
        "name": "Fishing Rod",
        "name_lower": "fishing rod",
        "price": 150,
    }

    economy = FakeEconomyCol()
    monkeypatch.setattr(main, "economy_col", economy)

    result = await main.process_shop_purchase(
        member,
        guild,
        store_item,
        {"wallet": 1000, "inventory": []},
    )

    assert result["ok"] is True
    assert result["purchase_type"] == "inventory"
    assert "Durability" in result["message"]
    assert economy.calls == [
        (
            {"_id": "123-42"},
            {
                "$set": {
                    "wallet": 850,
                    "inventory": [{"_id": "fishing rod", "uses_left": main.TOOL_DURABILITIES["fishing rod"]}],
                }
            },
            False,
        )
    ]
