"""Member join/remove/update events: invite tracking, welcome, boost, mute carry-over."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from bot.database import (
    boost_col,
    guild_config_col,
    invite_config_col,
    invites_col,
    mutes_col,
)
from bot.utils.invites_cache import invite_cache, get_guild_invites
from bot.utils.moderation import schedule_unmute


class MemberEventsCog(commands.Cog, name="MemberEvents"):
    """Member join/leave/update side effects."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # Boost thanks are handled by on_message via Discord's system message;
        # we keep this listener minimal to avoid duplicates.
        return

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        try:
            doc = await guild_config_col.find_one(
                {"guild_id": str(guild.id)}
            ) or {}

            new_invites = await get_guild_invites(guild)
            old_invites_data = invite_cache.get(guild.id, (time.time(), []))

            if isinstance(old_invites_data, tuple) and len(old_invites_data) == 2:
                _, old_invites = old_invites_data
            else:
                old_invites = (
                    old_invites_data if isinstance(old_invites_data, list) else []
                )

            used_invite = None
            for new_inv in new_invites:
                old = discord.utils.get(old_invites, code=new_inv.code)
                if old and new_inv.uses > old.uses:
                    used_invite = new_inv
                    break

            invite_cache[guild.id] = (time.time(), new_invites)

            if used_invite:
                inviter = used_invite.inviter
                await invites_col.update_one(
                    {"guild_id": str(guild.id), "user_id": str(inviter.id)},
                    {"$inc": {"total": 1, "regular": 1, "joins": 1}},
                    upsert=True,
                )
                await invites_col.update_one(
                    {"guild_id": str(guild.id), "code": used_invite.code},
                    {
                        "$set": {
                            "inviter_id": str(inviter.id),
                            "uses": used_invite.uses,
                        },
                        "$addToSet": {"joined_users": str(member.id)},
                    },
                    upsert=True,
                )
                config = await invite_config_col.find_one(
                    {"guild_id": str(guild.id)}
                )
                if config:
                    channel = guild.get_channel(int(config["channel_id"]))
                    if channel:
                        await channel.send(
                            f"👋 Welcome {member.mention}! "
                            f"Invited by {inviter.mention} "
                            f"(now **{used_invite.uses}** uses)"
                        )

            welcome_ch = guild.get_channel(doc.get("welcome_channel"))
            if welcome_ch:
                welcome_msg = doc.get("welcome_message") or (
                    "⭐ **Quack loud in** <#1370374734037909576> and enjoy the pond! ✨\n"
                    "⭐ **Check** <#1370374725108236379> to equip tag! ✨\n"
                    "⭐ **Boost our pond** and get exclusive "
                    "<@&1370367716892082236> role! ✨"
                )
                embed = discord.Embed(
                    title="Welcome to Duck Paradise 🦆 quack!",
                    description=welcome_msg,
                    color=discord.Color.from_str("#2f3136"),
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_image(
                    url="https://cdn.discordapp.com/attachments/1370374741579534408/1386456926300409939/duckduckgo-welcome.gif"
                )
                embed.set_footer(text=f"You are our {guild.member_count}th member!")
                msg = await welcome_ch.send(
                    f"welcome, {member.mention} 🐥!", embed=embed
                )
                duck_emoji = discord.utils.get(guild.emojis, name="duckwave2")
                if duck_emoji:
                    await msg.add_reaction(duck_emoji)
                else:
                    print("Custom emoji 'duckwave2' not found in guild.")

            if member.premium_since:
                await self._thank_booster(member, doc)

            mute_doc = await mutes_col.find_one(
                {"guild_id": member.guild.id, "user_id": member.id}
            )
            if mute_doc:
                mute_role = discord.utils.get(member.guild.roles, name="Muted")
                if mute_role and mute_role not in member.roles:
                    await member.add_roles(mute_role, reason="Reapplying mute after rejoin")

                mute_end = mute_doc.get("mute_end")
                if mute_end:
                    if isinstance(mute_end, str):
                        try:
                            mute_end = datetime.fromisoformat(mute_end)
                        except Exception:
                            try:
                                mute_end = datetime.strptime(
                                    mute_end, "%Y-%m-%d %H:%M:%S"
                                )
                            except Exception:
                                mute_end = None

                    if mute_end:
                        if mute_end.tzinfo is None:
                            mute_end = mute_end.replace(tzinfo=timezone.utc)
                        now_utc = datetime.now(timezone.utc)
                        if now_utc < mute_end:
                            remaining = (mute_end - now_utc).total_seconds()
                            self.bot.loop.create_task(
                                schedule_unmute(member.guild, member, remaining)
                            )
                        elif now_utc >= mute_end:
                            await mutes_col.delete_one(
                                {
                                    "guild_id": member.guild.id,
                                    "user_id": member.id,
                                }
                            )

        except Exception as exc:
            print("on_member_join ERROR:", exc)

    async def _thank_booster(
        self, member: discord.Member, doc: dict
    ) -> None:
        boost_key = f"{member.guild.id}-{member.id}"
        boost_record = await boost_col.find_one({"_id": boost_key})

        if boost_record and boost_record.get(
            "last_thanked"
        ) == member.premium_since.isoformat():
            return

        boost_ch = member.guild.get_channel(doc.get("boost_channel"))
        if not boost_ch:
            await boost_col.update_one(
                {"_id": boost_key},
                {"$set": {"last_thanked": member.premium_since.isoformat()}},
                upsert=True,
            )
            return

        boost_msg = doc.get("boost_message") or (
            f"{member.mention} just boosted the pond! 🌟\n"
            "Thank you for your support!"
        )
        text = (
            boost_msg.replace("{username}", member.name)
            .replace("{mention}", member.mention)
            .replace("{server}", member.guild.name)
            .replace(
                "{boostcount}",
                str(member.guild.premium_subscription_count or 0),
            )
        )

        boost_embed = discord.Embed(
            title="🚀 Boost Alert!",
            description=text,
            color=discord.Color.fuchsia(),
            timestamp=datetime.now(timezone.utc),
        )
        boost_embed.set_thumbnail(url=member.display_avatar.url)

        sent_msg = await boost_ch.send(embed=boost_embed)
        emoji = doc.get("boost_react_emoji")
        if emoji:
            try:
                await sent_msg.add_reaction(emoji)
            except Exception:
                pass

        await boost_col.update_one(
            {"_id": boost_key},
            {"$set": {"last_thanked": member.premium_since.isoformat()}},
            upsert=True,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        code_doc = await invites_col.find_one(
            {"guild_id": str(guild.id), "joined_users": str(member.id)}
        )
        if not code_doc:
            return

        inviter_id = code_doc.get("inviter_id")
        await invites_col.update_one(
            {"guild_id": str(guild.id), "code": code_doc.get("code")},
            {"$pull": {"joined_users": str(member.id)}},
        )
        if inviter_id:
            stats = await invites_col.find_one(
                {"guild_id": str(guild.id), "user_id": str(inviter_id)}
            )
            joins = (
                stats.get("joins", stats.get("regular", 0)) if stats else 0
            )
            leaves = (
                stats.get("leaves", stats.get("left", 0)) if stats else 0
            )
            await invites_col.update_one(
                {"guild_id": str(guild.id), "user_id": str(inviter_id)},
                {
                    "$inc": {"leaves": 1},
                    "$set": {"total": max(joins - (leaves + 1), 0)},
                },
                upsert=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemberEventsCog(bot))
