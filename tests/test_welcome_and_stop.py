"""Tests for welcome message customisation and stop command authorized-user listing."""

import pytest
from unittest.mock import AsyncMock, MagicMock
import main


# ── Welcome message template substitution ────────────────────────────────────


def _make_member(name="TestUser", mention="<@123>", guild_name="Test Server", member_count=42):
    member = MagicMock()
    member.name = name
    member.mention = mention
    member.display_avatar.url = "https://example.com/avatar.png"
    guild = MagicMock()
    guild.name = guild_name
    guild.member_count = member_count
    member.guild = guild
    return member, guild


def apply_welcome_template(template, member, guild):
    return (
        template.replace("{username}", member.name)
        .replace("{mention}", member.mention)
        .replace("{server}", guild.name)
        .replace("{membercount}", str(guild.member_count))
    )


def test_welcome_template_mention():
    member, guild = _make_member(mention="<@999>")
    result = apply_welcome_template("Hello {mention}!", member, guild)
    assert result == "Hello <@999>!"


def test_welcome_template_username():
    member, guild = _make_member(name="Alice")
    result = apply_welcome_template("Hi {username}!", member, guild)
    assert result == "Hi Alice!"


def test_welcome_template_server():
    member, guild = _make_member(guild_name="Duck Paradise")
    result = apply_welcome_template("Welcome to {server}!", member, guild)
    assert result == "Welcome to Duck Paradise!"


def test_welcome_template_membercount():
    member, guild = _make_member(member_count=100)
    result = apply_welcome_template("You are member #{membercount}!", member, guild)
    assert result == "You are member #100!"


def test_welcome_template_all_placeholders():
    member, guild = _make_member(name="Bob", mention="<@321>", guild_name="Pond", member_count=7)
    tmpl = "👋 Welcome {mention} ({username}) to **{server}**! Member #{membercount}."
    result = apply_welcome_template(tmpl, member, guild)
    assert result == "👋 Welcome <@321> (Bob) to **Pond**! Member #7."


def test_welcome_default_message_no_hardcoded_channels():
    default = "👋 Welcome {mention} to **{server}**! 🎉\nYou are our **{membercount}**th member. We're happy to have you here!"
    assert "<#" not in default, "Default welcome must not contain hardcoded channel mentions"
    assert "<@&" not in default, "Default welcome must not contain hardcoded role mentions"
    assert "cdn.discordapp.com" not in default, "Default welcome must not contain hardcoded CDN URLs"


def test_welcome_default_message_uses_template_vars():
    member, guild = _make_member(mention="<@42>", guild_name="MyServer", member_count=5)
    default = "👋 Welcome {mention} to **{server}**! 🎉\nYou are our **{membercount}**th member. We're happy to have you here!"
    result = apply_welcome_template(default, member, guild)
    assert "<@42>" in result
    assert "MyServer" in result
    assert "5" in result
    assert "{mention}" not in result
    assert "{server}" not in result
    assert "{membercount}" not in result


# ── on_member_join: no hardcoded server-specific strings remain ──────────────


def test_no_hardcoded_channel_ids_in_on_member_join():
    """Regression guard: hardcoded server-specific IDs must not appear in source."""
    import inspect

    source = inspect.getsource(main.on_member_join)
    assert "1370374734037909576" not in source
    assert "1370374725108236379" not in source
    assert "1370367716892082236" not in source


def test_no_hardcoded_cdn_url_in_on_member_join():
    import inspect

    source = inspect.getsource(main.on_member_join)
    assert "1386456926300409939" not in source, "Hardcoded CDN attachment URL should not appear in on_member_join"


# ── stop command: no hardcoded username strings remain ───────────────────────


def test_stop_command_source_has_no_cutebatak():
    import inspect

    source = inspect.getsource(main.stop.callback)
    assert "CuteBatak" not in source


def test_stop_command_source_has_no_hardcoded_ids():
    import inspect

    source = inspect.getsource(main.stop.callback)
    assert "1059882387590365314" not in source
    assert "903123014420406302" not in source


# ── stop command: lists authorized user names from guild ─────────────────────


@pytest.mark.asyncio
async def test_stop_command_lists_guild_members(monkeypatch):
    monkeypatch.setenv("AUTHORIZED_USER_IDS", "111,222")
    # rebuild the set as the module would
    auth_ids = {int(x) for x in "111,222".split(",") if x.strip().isdigit()}
    monkeypatch.setattr(main, "AUTHORIZED_USER_IDS", auth_ids)

    ctx = MagicMock()
    ctx.guild.id = 9999
    ctx.send = AsyncMock()

    member_111 = MagicMock()
    member_111.display_name = "Alice"
    member_222 = MagicMock()
    member_222.display_name = "Bob"

    ctx.guild.get_member = lambda uid: {111: member_111, 222: member_222}.get(uid)
    monkeypatch.setattr(main, "bot_locks", {})

    await main.stop.callback(ctx)

    sent = ctx.send.call_args[0][0]
    assert "Alice" in sent
    assert "Bob" in sent
    assert "<@" not in sent, "Names must not be pings"


@pytest.mark.asyncio
async def test_stop_command_falls_back_to_bot_cache(monkeypatch):
    monkeypatch.setenv("AUTHORIZED_USER_IDS", "555")
    auth_ids = {555}
    monkeypatch.setattr(main, "AUTHORIZED_USER_IDS", auth_ids)

    ctx = MagicMock()
    ctx.guild.id = 8888
    ctx.send = AsyncMock()
    ctx.guild.get_member = lambda uid: None  # not in guild

    cached_user = MagicMock()
    cached_user.name = "CachedUser"
    monkeypatch.setattr(main.bot, "get_user", lambda uid: cached_user)
    monkeypatch.setattr(main, "bot_locks", {})

    await main.stop.callback(ctx)

    sent = ctx.send.call_args[0][0]
    assert "CachedUser" in sent


@pytest.mark.asyncio
async def test_stop_command_generic_message_when_no_users_found(monkeypatch):
    monkeypatch.setattr(main, "AUTHORIZED_USER_IDS", {99999999})

    ctx = MagicMock()
    ctx.guild.id = 7777
    ctx.send = AsyncMock()
    ctx.guild.get_member = lambda uid: None
    monkeypatch.setattr(main.bot, "get_user", lambda uid: None)
    monkeypatch.setattr(main, "bot_locks", {})

    await main.stop.callback(ctx)

    sent = ctx.send.call_args[0][0]
    assert "🔒" in sent
    assert "override" in sent.lower()
