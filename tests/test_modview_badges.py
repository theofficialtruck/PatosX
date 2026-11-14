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

"""The Discord badges shown in the modview "Badges / Flags" field."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from cogs import moderation as mod

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def asset(animated=False):
    return discord.Asset(None, url="https://cdn.discordapp.com/x.png", key="x", animated=animated)


def make_member(flags=0, **overrides):
    member = SimpleNamespace(
        id=1,
        public_flags=discord.PublicUserFlags._from_value(flags),
        premium_since=None,
        avatar=asset(),
        guild_avatar=None,
        guild_banner=None,
        banner=None,
        primary_guild=discord.PrimaryGuild(state=None, data={"identity_enabled": False}),
        avatar_decoration=None,
        collectibles=[],
    )
    for key, value in overrides.items():
        setattr(member, key, value)
    return member


def test_plain_member_has_no_badges():
    assert mod.collect_badges(make_member(), NOW) == []
    assert mod.format_flags(make_member()) == "None"


def test_every_public_flag_discord_py_knows_has_a_named_badge():
    internal = mod.INTERNAL_FLAGS
    known = {flag.name for flag in discord.UserFlags} - internal
    assert known <= set(mod.PUBLIC_FLAG_BADGES)
    every_flag = 0
    for flag in discord.UserFlags:
        if flag.name not in internal:
            every_flag |= flag.value
    badges = mod.collect_badges(make_member(every_flag), NOW)
    assert badges == list(mod.PUBLIC_FLAG_BADGES.values())  # profile order, nothing unstyled


@pytest.mark.parametrize(
    "flag,label",
    [
        (1 << 0, "🛡️ Discord Staff"),
        (1 << 18, "🎓 Moderator Programs Alumni"),
        (1 << 22, "🧑‍💻 Active Developer"),
        (1 << 7, "🟠 HypeSquad Brilliance"),
        (1 << 14, "🐞 Bug Hunter (Level 2)"),
    ],
)
def test_single_flags(flag, label):
    assert mod.collect_badges(make_member(flag), NOW) == [label]


def test_internal_flags_are_not_badges():
    assert mod.collect_badges(make_member((1 << 4) | (1 << 5) | (1 << 13)), NOW) == []


def test_flags_newer_than_discord_py_are_read_from_the_raw_bits():
    badges = mod.collect_badges(make_member((1 << 44) | (1 << 50) | (1 << 51) | 1), NOW)
    assert badges == [
        "🛡️ Discord Staff",
        "⛔ Quarantined Account",
        "🤝 Discord Collaborator",
        "🤝 Discord Collaborator (Restricted)",
    ]


@pytest.mark.parametrize(
    "since,level",
    [
        (datetime(2026, 11, 1, tzinfo=timezone.utc), 1),
        (datetime(2026, 9, 14, tzinfo=timezone.utc), 2),
        (datetime(2026, 9, 15, tzinfo=timezone.utc), 1),  # one day short of two months
        (datetime(2026, 8, 1, tzinfo=timezone.utc), 3),
        (datetime(2026, 5, 1, tzinfo=timezone.utc), 4),
        (datetime(2026, 2, 1, tzinfo=timezone.utc), 5),
        (datetime(2025, 11, 14, tzinfo=timezone.utc), 6),
        (datetime(2025, 8, 1, tzinfo=timezone.utc), 7),
        (datetime(2025, 5, 1, tzinfo=timezone.utc), 8),
        (datetime(2024, 11, 1, tzinfo=timezone.utc), 9),
        (datetime(2020, 1, 1, tzinfo=timezone.utc), 9),
    ],
)
def test_boost_badge_level_follows_discords_evolving_badge(since, level):
    assert mod.boost_badge_level(since, NOW) == level


def test_server_booster_badge():
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    badges = mod.collect_badges(make_member(premium_since=since), NOW)
    assert badges == [f"🚀 Server Booster (Level 5, boosting since <t:{int(since.timestamp())}:D>)"]


@pytest.mark.parametrize(
    "perk",
    [
        {"avatar": asset(animated=True)},
        {"guild_avatar": asset()},
        {"guild_banner": asset()},
        {"banner": asset()},
    ],
)
def test_nitro_is_detected_from_perks_that_need_it(perk):
    assert mod.collect_badges(make_member(**perk), NOW) == ["💠 Nitro (detected from profile perks)"]


def test_server_tag_only_when_displayed():
    shown = discord.PrimaryGuild(state=None, data={"identity_enabled": True, "identity_guild_id": "5", "tag": "DUCK"})
    hidden = discord.PrimaryGuild(state=None, data={"identity_enabled": False, "identity_guild_id": "5", "tag": "DUCK"})
    assert mod.collect_badges(make_member(primary_guild=shown), NOW) == ["🏷️ Server Tag: `DUCK`"]
    assert mod.collect_badges(make_member(primary_guild=hidden), NOW) == []


def test_avatar_decoration_and_nameplate():
    nameplate = discord.Collectible(
        state=None,
        type="nameplate",
        data={"asset": "nameplates/x/", "sku_id": "123", "label": "", "palette": "bubble_gum"},
    )
    badges = mod.collect_badges(make_member(avatar_decoration=asset(), collectibles=[nameplate]), NOW)
    assert badges == ["🖼️ Avatar Decoration", "✨ Nameplate (Bubble Gum)"]


def test_format_flags_lists_one_badge_per_line_and_fits_an_embed_field():
    every = (1 << 23) - 1
    text = mod.format_flags(make_member(every, premium_since=datetime(2025, 1, 1, tzinfo=timezone.utc)))
    assert text.splitlines()[0] == "🛡️ Discord Staff"
    assert "🚀 Server Booster" in text
    assert len(text) <= 1024


def test_format_flags_never_breaks_modview_on_odd_members():
    member = MagicMock()  # nothing is a real discord type: every section is skipped
    assert mod.format_flags(member) == "None"
