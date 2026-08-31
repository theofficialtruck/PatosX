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

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main


class FakeTicketPanelsCol:
    """In-memory stand-in for the `ticket_panels` Mongo collection, single panel doc."""

    def __init__(self, panel=None):
        self.panel = dict(panel) if panel else None
        self.update_one = AsyncMock(side_effect=self._update_one)

    async def find_one(self, query):
        if self.panel and self.panel.get("panel_name") == query.get("panel_name"):
            return dict(self.panel)
        return None

    async def _update_one(self, query, update):
        if "$pull" in update and self.panel:
            pull = update["$pull"].get("buttons", {})
            target_label = pull.get("label")
            self.panel["buttons"] = [b for b in self.panel.get("buttons", []) if b.get("label") != target_label]


def make_panel(buttons):
    return {"guild": "123", "panel_name": "Support", "buttons": buttons}


@pytest.mark.asyncio
async def test_ticketremovebutton_sends_select_menu_for_existing_panel(monkeypatch):
    panel = make_panel([{"category_name": "General", "label": "Open Ticket", "emoji": "🎫"}])
    monkeypatch.setattr(main, "ticket_panels_col", FakeTicketPanelsCol(panel))
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=1)
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    await main.ticketremovebutton.callback(ctx, panel_name="Support")

    ctx.send.assert_awaited_once()
    view = ctx.send.await_args.kwargs["view"]
    assert isinstance(view, main.TicketRemoveButtonView)
    select = view.children[0]
    assert len(select.options) == 1
    assert select.options[0].label == "Open Ticket"


@pytest.mark.asyncio
async def test_ticketremovebutton_rejects_unknown_panel(monkeypatch):
    monkeypatch.setattr(main, "ticket_panels_col", FakeTicketPanelsCol(None))
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=1)
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    await main.ticketremovebutton.callback(ctx, panel_name="Ghost")

    ctx.send.assert_awaited_once_with("❌ No panel found with name `Ghost`.")


@pytest.mark.asyncio
async def test_ticketremovebutton_rejects_panel_with_no_buttons(monkeypatch):
    monkeypatch.setattr(main, "ticket_panels_col", FakeTicketPanelsCol(make_panel([])))
    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=1)
    ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())

    await main.ticketremovebutton.callback(ctx, panel_name="Support")

    ctx.send.assert_awaited_once_with("❌ Panel `Support` has no buttons to remove.")


@pytest.mark.asyncio
async def test_ticket_remove_button_select_removes_only_the_chosen_button(monkeypatch):
    panel = make_panel(
        [
            {"category_name": "General", "label": "Open Ticket", "emoji": "🎫"},
            {"category_name": "Billing", "label": "Billing Help", "emoji": "💳"},
        ]
    )
    panels_col = FakeTicketPanelsCol(panel)
    monkeypatch.setattr(main, "ticket_panels_col", panels_col)

    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=1)
    ctx = SimpleNamespace(guild=guild, author=author)

    select = main.TicketRemoveButtonSelect(ctx, panel)
    select._values = ["1"]  # select the second button, "Billing Help"

    interaction = SimpleNamespace(
        user=author,
        response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
    )

    await select.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    assert "Billing Help" in embed.description
    remaining_labels = [b["label"] for b in panels_col.panel["buttons"]]
    assert remaining_labels == ["Open Ticket"]


@pytest.mark.asyncio
async def test_ticket_remove_button_select_blocks_other_users(monkeypatch):
    panel = make_panel([{"category_name": "General", "label": "Open Ticket", "emoji": "🎫"}])
    panels_col = FakeTicketPanelsCol(panel)
    monkeypatch.setattr(main, "ticket_panels_col", panels_col)

    guild = SimpleNamespace(id=123)
    author = SimpleNamespace(id=1)
    other_user = SimpleNamespace(id=999)
    ctx = SimpleNamespace(guild=guild, author=author)

    select = main.TicketRemoveButtonSelect(ctx, panel)
    select._values = ["0"]

    interaction = SimpleNamespace(
        user=other_user,
        response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
    )

    await select.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    assert "Only the staff member" in interaction.response.send_message.await_args.args[0]
    interaction.response.edit_message.assert_not_awaited()
    assert len(panels_col.panel["buttons"]) == 1
