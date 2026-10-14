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

"""Claimable roles (``.roles``) regression tests.

Covers the live embed: it must follow roleadd / roleremove, role deletions and renames without an
admin re-posting it, and pressing the button of a role the user already has must ask for
confirmation instead of silently removing the role. Also covers the staff only ``.poll`` command."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs import giveaways_polls as gp
from core import state

GUILD_ID = 999
CHANNEL_ID = 777
MESSAGE_ID = 555


class RolesCol:
    """In-memory single document stand in for ``state.roles_col`` (supports $set, $pull and $unset)."""

    def __init__(self, doc):
        self.doc = copy.deepcopy(doc)

    def _matches(self, query):
        if query.get("_id") != self.doc["_id"]:
            return False
        return "roles" not in query or query["roles"] in self.doc.get("roles", [])

    async def find_one(self, query):
        return copy.deepcopy(self.doc) if self._matches(query) else None

    async def update_one(self, query, update, upsert=False):
        if not self._matches(query):
            return SimpleNamespace(matched_count=0, modified_count=0)
        self.doc.update(copy.deepcopy(update.get("$set", {})))
        for key, value in update.get("$pull", {}).items():
            gone = value["$in"] if isinstance(value, dict) else [value]
            self.doc[key] = [item for item in self.doc.get(key, []) if item not in gone]
        for key in update.get("$unset", {}):
            self.doc.pop(key, None)
        return SimpleNamespace(matched_count=1, modified_count=1)


def make_role(role_id, name=None):
    return SimpleNamespace(id=role_id, name=name or f"role{role_id}", mention=f"<@&{role_id}>")


def make_roles_message():
    return SimpleNamespace(id=MESSAGE_ID, edit=AsyncMock())


def make_guild(roles, message=None, channel=None):
    """A guild whose live ``.roles`` message lives in channel 777."""
    channel = channel or SimpleNamespace(id=CHANNEL_ID, fetch_message=AsyncMock(return_value=message))
    by_id = {role.id: role for role in roles}
    return SimpleNamespace(
        id=GUILD_ID,
        get_role=lambda rid: by_id.get(rid),
        get_channel_or_thread=lambda cid: channel if cid == CHANNEL_ID else None,
    )


def roles_doc(role_ids, **extra):
    return {"_id": GUILD_ID, "roles": list(role_ids), "message_id": MESSAGE_ID, "channel_id": CHANNEL_ID, **extra}


def button_labels(view):
    return [child.label for child in view.children]


# --- live embed --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_edits_the_live_message_with_the_current_roles(monkeypatch):
    monkeypatch.setattr(state, "roles_col", RolesCol(roles_doc([1, 2])))
    message = make_roles_message()
    guild = make_guild([make_role(1, "Gamers"), make_role(2, "Artists")], message)

    assert await gp.refresh_roles_embed(guild) is True

    kwargs = message.edit.await_args.kwargs
    assert kwargs["embed"].description == "<@&1>\n<@&2>"
    assert button_labels(kwargs["view"]) == ["Gamers", "Artists"]


@pytest.mark.asyncio
async def test_refresh_does_not_need_the_command_channel(monkeypatch):
    """The embed is found through the stored channel, so roleadd can run in any other channel."""
    monkeypatch.setattr(state, "roles_col", RolesCol(roles_doc([1])))
    message = make_roles_message()
    other_channel = SimpleNamespace(id=1, fetch_message=AsyncMock())
    guild = make_guild([make_role(1)], message)

    assert await gp.refresh_roles_embed(guild, other_channel) is True

    other_channel.fetch_message.assert_not_awaited()
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_skips_roles_that_no_longer_exist(monkeypatch):
    monkeypatch.setattr(state, "roles_col", RolesCol(roles_doc([1, 2])))
    message = make_roles_message()
    guild = make_guild([make_role(1)], message)

    await gp.refresh_roles_embed(guild)

    kwargs = message.edit.await_args.kwargs
    assert kwargs["embed"].description == "<@&1>"
    assert len(kwargs["view"].children) == 1


@pytest.mark.asyncio
async def test_refresh_with_no_valid_roles_removes_the_buttons(monkeypatch):
    monkeypatch.setattr(state, "roles_col", RolesCol(roles_doc([1])))
    message = make_roles_message()
    guild = make_guild([], message)

    await gp.refresh_roles_embed(guild)

    kwargs = message.edit.await_args.kwargs
    assert "No valid roles" in kwargs["embed"].description
    assert kwargs["view"] is None


@pytest.mark.asyncio
async def test_refresh_falls_back_to_the_command_channel_for_old_messages(monkeypatch):
    """Messages posted before channel_id was stored are looked up in the channel the admin used."""
    doc = roles_doc([1])
    del doc["channel_id"]
    monkeypatch.setattr(state, "roles_col", RolesCol(doc))
    message = make_roles_message()
    fallback = SimpleNamespace(id=1, fetch_message=AsyncMock(return_value=message))
    guild = make_guild([make_role(1)], channel=SimpleNamespace(id=2, fetch_message=AsyncMock()))

    assert await gp.refresh_roles_embed(guild, fallback) is True
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_forgets_a_deleted_message(monkeypatch):
    col = RolesCol(roles_doc([1]))
    monkeypatch.setattr(state, "roles_col", col)
    not_found = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Message")
    channel = SimpleNamespace(id=CHANNEL_ID, fetch_message=AsyncMock(side_effect=not_found))
    guild = make_guild([make_role(1)], channel=channel)

    assert await gp.refresh_roles_embed(guild) is False
    assert "message_id" not in col.doc
    assert col.doc["roles"] == [1]


@pytest.mark.asyncio
async def test_refresh_without_a_posted_embed_does_nothing(monkeypatch):
    monkeypatch.setattr(state, "roles_col", RolesCol({"_id": GUILD_ID, "roles": [1]}))
    guild = make_guild([make_role(1)])
    assert await gp.refresh_roles_embed(guild) is False


@pytest.mark.asyncio
async def test_role_deleted_by_an_admin_updates_the_embed_immediately(monkeypatch, giveaways_cog):
    col = RolesCol(roles_doc([1, 2]))
    monkeypatch.setattr(state, "roles_col", col)
    message = make_roles_message()
    guild = make_guild([make_role(1, "Gamers")], message)  # role 2 is already gone from the guild

    await giveaways_cog.on_guild_role_delete(SimpleNamespace(id=2, guild=guild))

    assert col.doc["roles"] == [1]
    kwargs = message.edit.await_args.kwargs
    assert kwargs["embed"].description == "<@&1>"
    assert button_labels(kwargs["view"]) == ["Gamers"]


@pytest.mark.asyncio
async def test_roles_deleted_while_offline_are_pruned_on_startup(monkeypatch):
    col = RolesCol(roles_doc([1, 2]))
    monkeypatch.setattr(state, "roles_col", col)
    message = make_roles_message()
    guild = make_guild([make_role(1, "Gamers")], message)

    remaining = await gp.prune_deleted_roles(guild, [1, 2])

    assert remaining == [1]
    assert col.doc["roles"] == [1]
    assert button_labels(message.edit.await_args.kwargs["view"]) == ["Gamers"]


@pytest.mark.asyncio
async def test_startup_prune_leaves_an_intact_list_alone(monkeypatch):
    col = RolesCol(roles_doc([1]))
    monkeypatch.setattr(state, "roles_col", col)
    message = make_roles_message()
    guild = make_guild([make_role(1)], message)

    assert await gp.prune_deleted_roles(guild, [1]) == [1]
    message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleting_an_unrelated_role_leaves_the_embed_alone(monkeypatch, giveaways_cog):
    monkeypatch.setattr(state, "roles_col", RolesCol(roles_doc([1])))
    message = make_roles_message()
    guild = make_guild([make_role(1)], message)

    await giveaways_cog.on_guild_role_delete(SimpleNamespace(id=42, guild=guild))

    message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_renaming_a_claimable_role_updates_its_button(monkeypatch, giveaways_cog):
    monkeypatch.setattr(state, "roles_col", RolesCol(roles_doc([1])))
    message = make_roles_message()
    renamed = make_role(1, "Players")
    guild = make_guild([renamed], message)
    renamed.guild = guild

    await giveaways_cog.on_guild_role_update(make_role(1, "Gamers"), renamed)

    assert button_labels(message.edit.await_args.kwargs["view"]) == ["Players"]


@pytest.mark.asyncio
async def test_role_update_without_a_rename_is_ignored(monkeypatch, giveaways_cog):
    monkeypatch.setattr(state, "roles_col", RolesCol(roles_doc([1])))
    message = make_roles_message()
    guild = make_guild([make_role(1)], message)
    after = make_role(1)
    after.guild = guild

    await giveaways_cog.on_guild_role_update(make_role(1), after)

    message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_roleadd_updates_the_live_embed_from_any_channel(monkeypatch, giveaways_cog):
    col = RolesCol(roles_doc([1]))

    async def update_one(query, update, upsert=False):
        # roleadd pushes; the in-memory fake only needs to apply it
        for role_id in [update.get("$push", {}).get("roles")]:
            if role_id is not None:
                col.doc["roles"].append(role_id)
        return SimpleNamespace(matched_count=1, modified_count=1)

    col.update_one = update_one
    monkeypatch.setattr(state, "roles_col", col)
    message = make_roles_message()
    guild = make_guild([make_role(1, "Gamers"), make_role(2, "Artists")], message)
    ctx = SimpleNamespace(guild=guild, channel=SimpleNamespace(id=1, fetch_message=AsyncMock()), send=AsyncMock())

    await giveaways_cog.roleadd.callback(giveaways_cog, ctx, make_role(2, "Artists"))

    assert button_labels(message.edit.await_args.kwargs["view"]) == ["Gamers", "Artists"]
    assert "live roles embed was updated" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_roleremove_updates_the_live_embed(monkeypatch, giveaways_cog):
    col = RolesCol(roles_doc([1, 2]))
    monkeypatch.setattr(state, "roles_col", col)
    message = make_roles_message()
    guild = make_guild([make_role(1, "Gamers"), make_role(2, "Artists")], message)
    ctx = SimpleNamespace(guild=guild, channel=SimpleNamespace(id=1), send=AsyncMock())

    await giveaways_cog.roleremove.callback(giveaways_cog, ctx, make_role(2, "Artists"))

    assert col.doc["roles"] == [1]
    assert button_labels(message.edit.await_args.kwargs["view"]) == ["Gamers"]


@pytest.mark.asyncio
async def test_roles_command_stores_the_channel_with_the_message(monkeypatch, giveaways_cog):
    col = RolesCol({"_id": GUILD_ID, "roles": [1]})
    monkeypatch.setattr(state, "roles_col", col)
    guild = make_guild([make_role(1)])
    ctx = SimpleNamespace(
        guild=guild, channel=SimpleNamespace(id=CHANNEL_ID), send=AsyncMock(return_value=SimpleNamespace(id=MESSAGE_ID))
    )

    await giveaways_cog.roles.callback(giveaways_cog, ctx)

    assert col.doc["message_id"] == MESSAGE_ID
    assert col.doc["channel_id"] == CHANNEL_ID


# --- role button: confirm before removing -------------------------------------------------------


def make_member(user_id, roles):
    return SimpleNamespace(id=user_id, roles=list(roles), add_roles=AsyncMock(), remove_roles=AsyncMock())


def make_interaction(member, guild):
    return SimpleNamespace(
        user=member,
        guild=guild,
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_pressing_a_role_you_have_asks_before_removing_it():
    role = make_role(1, "Gamers")
    guild = make_guild([role])
    member = make_member(7, [role])
    interaction = make_interaction(member, guild)
    view = gp.RoleButtons([1], GUILD_ID, guild)

    await view.children[0].callback(interaction)

    member.remove_roles.assert_not_awaited()
    member.add_roles.assert_not_awaited()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], gp.RoleRemoveConfirmView)
    assert "already have" in interaction.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_pressing_a_role_you_lack_still_claims_it():
    role = make_role(1, "Gamers")
    guild = make_guild([role])
    member = make_member(7, [])
    interaction = make_interaction(member, guild)
    view = gp.RoleButtons([1], GUILD_ID, guild)

    await view.children[0].callback(interaction)

    member.add_roles.assert_awaited_once_with(role)
    assert "claimed" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_confirming_removes_the_role():
    role = make_role(1, "Gamers")
    member = make_member(7, [role])
    view = gp.RoleRemoveConfirmView(role, 7)
    interaction = make_interaction(member, make_guild([role]))

    await view.confirm.callback(interaction)

    member.remove_roles.assert_awaited_once_with(role)
    assert "Removed" in interaction.response.edit_message.await_args.kwargs["content"]
    assert all(child.disabled for child in view.children)


@pytest.mark.asyncio
async def test_keeping_the_role_does_not_remove_it():
    role = make_role(1, "Gamers")
    member = make_member(7, [role])
    view = gp.RoleRemoveConfirmView(role, 7)
    interaction = make_interaction(member, make_guild([role]))

    await view.keep.callback(interaction)

    member.remove_roles.assert_not_awaited()
    assert "still have" in interaction.response.edit_message.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_only_the_original_user_can_confirm():
    role = make_role(1, "Gamers")
    intruder = make_member(8, [role])
    view = gp.RoleRemoveConfirmView(role, 7)
    interaction = make_interaction(intruder, make_guild([role]))

    await view.confirm.callback(interaction)

    intruder.remove_roles.assert_not_awaited()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_confirming_reports_missing_permission():
    role = make_role(1, "Gamers")
    member = make_member(7, [role])
    member.remove_roles = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="no"), "no"))
    view = gp.RoleRemoveConfirmView(role, 7)
    interaction = make_interaction(member, make_guild([role]))

    await view.confirm.callback(interaction)

    assert "permission" in interaction.response.edit_message.await_args.kwargs["content"]


# --- poll is staff only -------------------------------------------------------------------------


def make_check_ctx(bot, member, staff_role_id=55):
    role = make_role(staff_role_id, "Staff")
    guild = SimpleNamespace(id=1, owner_id=100, get_role=lambda rid: role if rid == staff_role_id else None)
    return SimpleNamespace(bot=bot, guild=guild, author=member, command=None, cog=None, interaction=None), role


def poll_member(user_id, roles=(), administrator=False):
    return SimpleNamespace(
        id=user_id, roles=list(roles), guild_permissions=SimpleNamespace(administrator=administrator)
    )


@pytest.mark.asyncio
async def test_poll_is_blocked_for_members_without_the_staff_role(monkeypatch, giveaways_cog, bot):
    monkeypatch.setattr(state, "settings_col", SimpleNamespace(find_one=AsyncMock(return_value={"staff_role": 55})))
    ctx, _ = make_check_ctx(bot, poll_member(7))
    assert await giveaways_cog.poll.can_run(ctx) is False


@pytest.mark.asyncio
async def test_poll_is_blocked_when_no_staff_role_is_configured(monkeypatch, giveaways_cog, bot):
    monkeypatch.setattr(state, "settings_col", SimpleNamespace(find_one=AsyncMock(return_value=None)))
    ctx, _ = make_check_ctx(bot, poll_member(7))
    assert await giveaways_cog.poll.can_run(ctx) is False


@pytest.mark.asyncio
async def test_poll_is_allowed_for_staff_and_administrators(monkeypatch, giveaways_cog, bot):
    monkeypatch.setattr(state, "settings_col", SimpleNamespace(find_one=AsyncMock(return_value={"staff_role": 55})))
    ctx, role = make_check_ctx(bot, poll_member(7))
    ctx.author.roles = [role]
    assert await giveaways_cog.poll.can_run(ctx) is True

    admin_ctx, _ = make_check_ctx(bot, poll_member(8, administrator=True))
    assert await giveaways_cog.poll.can_run(admin_ctx) is True


@pytest.mark.asyncio
async def test_poll_is_blocked_in_direct_messages(giveaways_cog, bot):
    ctx = SimpleNamespace(bot=bot, guild=None, author=poll_member(7), command=None, cog=None, interaction=None)
    assert await giveaways_cog.poll.can_run(ctx) is False


def test_poll_check_is_registered_on_the_command(giveaways_cog):
    assert giveaways_cog.poll.checks, "poll must carry a permission check"
