"""Per-message side effects: quack counter, AFK, sticky notes, ticket close, DuckGPT."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import discord
from dateutil import parser as date_parser
from discord.ext import commands

from bot.database import (
    afk_col,
    config_col,
    settings_col,
    sticky_col,
    tickets_col,
)
from bot.utils.ai import ask_duck_gpt
from bot.utils.permissions import has_staff_role
from bot.utils.stickies import last_sticky_msg, onetime_channels
from bot.utils.tickets import actually_close_ticket


class _DummyCtx:
    """Minimal ctx stub passed into ``actually_close_ticket`` from on_message."""

    def __init__(self, channel: discord.abc.Messageable, message: discord.Message) -> None:
        self.channel = channel
        self.author = message.author
        self.guild = message.guild


class MessageEventsCog(commands.Cog, name="MessageEvents"):
    """Single ``on_message`` handler that routes to per-feature helpers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        await self.bot.process_commands(message)

        if not message.guild:
            return

        # Boost system messages — handled here so we thank per-event.
        try:
            if message.type in {
                discord.MessageType.premium_guild_subscription,
                discord.MessageType.premium_guild_tier_1,
                discord.MessageType.premium_guild_tier_2,
                discord.MessageType.premium_guild_tier_3,
            }:
                await self._handle_boost_message(message)
                return
        except Exception as exc:
            print(f"[boost message handler error] {exc}")

        await self._handle_quack_counter(message)
        await self._handle_ticket_close_pending(message)
        await self._handle_duck_gpt(message)
        await self._handle_sticky(message)
        await self._handle_one_time_channel(message)
        await self._handle_afk(message)

    # ------------------------------------------------------------------
    # Sub-handlers
    # ------------------------------------------------------------------

    async def _handle_boost_message(self, message: discord.Message) -> None:
        guild = message.guild
        config = await config_col.find_one({"guild": str(guild.id)})
        if not config:
            return

        boost_channel_id = config.get("boost_channel")
        boost_message_template = config.get("boost_message")
        channel = (
            guild.get_channel(boost_channel_id)
            if boost_channel_id
            else message.channel
        )
        if not channel or not boost_message_template:
            return

        booster = message.author
        text = (
            boost_message_template.replace("{username}", booster.name)
            .replace("{mention}", booster.mention)
            .replace("{server}", guild.name)
            .replace(
                "{boostcount}", str(guild.premium_subscription_count or 0)
            )
        )

        embed = discord.Embed(
            description=text,
            color=discord.Color.fuchsia(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name="Boost Alert!", icon_url=booster.display_avatar.url)
        embed.set_thumbnail(url=booster.display_avatar.url)

        try:
            sent = await channel.send(embed=embed)
            emoji = config.get("boost_react_emoji")
            if emoji:
                try:
                    await sent.add_reaction(emoji)
                except Exception:
                    pass
        except Exception as exc:
            print(f"⚠️ Error sending boost thank-you in {guild.name}: {exc}")

    async def _handle_quack_counter(self, message: discord.Message) -> None:
        try:
            guild_id = str(message.guild.id)
            doc = await settings_col.find_one({"guild": guild_id})
            prefix = doc.get("prefix", "?") if doc else "?"
            if message.content.lower().startswith(prefix.lower()):
                return

            await config_col.update_one(
                {"guild": guild_id},
                {
                    "$setOnInsert": {
                        "quack_count": 0,
                        "quacks": {},
                        "QUACK_CHANNELS": "all",
                    }
                },
                upsert=True,
            )

            config = await config_col.find_one({"guild": guild_id})
            quack_channels = config.get("QUACK_CHANNELS", [])
            counts_everywhere = quack_channels == "all" or quack_channels == []
            in_quack_channel = counts_everywhere or (
                isinstance(quack_channels, list)
                and message.channel.id in quack_channels
            )

            content_lower = message.content.lower()
            occurrences = len(re.findall(r"\bquack\b", content_lower))

            if in_quack_channel and occurrences > 0:
                user_id = str(message.author.id)
                await config_col.update_one(
                    {"guild": guild_id},
                    {
                        "$inc": {
                            "quack_count": occurrences,
                            f"quacks.{user_id}": occurrences,
                        }
                    },
                )
        except Exception as exc:
            print(f"[Quack Counter Error] {exc}")

    async def _handle_ticket_close_pending(self, message: discord.Message) -> None:
        try:
            ticket_entry = await tickets_col.find_one(
                {
                    "guild": str(message.guild.id),
                    "channel_id": str(message.channel.id),
                    "close_pending": True,
                }
            )
            if not ticket_entry:
                return

            opener_id = int(ticket_entry.get("owner_id"))
            if message.author.id != opener_id:
                return

            if message.content.lower() == "cancel":
                await tickets_col.update_one(
                    {"_id": ticket_entry["_id"]},
                    {"$set": {"close_pending": False}},
                )
                await message.channel.send("❌ Ticket close request canceled.")
                return

            if message.content.lower() == "confirm":
                opener = message.guild.get_member(opener_id)
                if opener:
                    ctx = _DummyCtx(message.channel, message)
                    await actually_close_ticket(ctx, opener, forced=False)
                    await tickets_col.delete_one({"_id": ticket_entry["_id"]})
                    await message.channel.send("✅ Ticket has been closed.")
                else:
                    await message.channel.send(
                        "⚠️ Could not find ticket opener in server."
                    )
        except Exception as exc:
            print(f"[ticket confirmation error] {exc}")

    async def _handle_duck_gpt(self, message: discord.Message) -> None:
        if self.bot.user not in message.mentions:
            return
        prompt = message.clean_content.replace(f"<@{self.bot.user.id}>", "").strip()
        if not prompt:
            prompt = "Quack!"

        ctx = await self.bot.get_context(message)
        if not ctx.guild:
            return await message.reply("🦆 I can only assist you in servers, not in DMs!")

        await message.channel.typing()
        reply = await ask_duck_gpt(ctx, prompt)
        await message.reply(reply)

    async def _handle_sticky(self, message: discord.Message) -> None:
        try:
            doc = await sticky_col.find_one(
                {
                    "guild": str(message.guild.id),
                    "channel": str(message.channel.id),
                }
            )
            if not doc:
                return

            old_id = last_sticky_msg.get(message.channel.id)
            if old_id:
                try:
                    old = await message.channel.fetch_message(old_id)
                    await old.delete()
                except discord.NotFound:
                    print(
                        f"[sticky note] Previous message {old_id} not found, "
                        "creating new one"
                    )
                except discord.Forbidden:
                    print(
                        f"[sticky note] No permission to delete message {old_id}"
                    )
                except Exception as exc:
                    print(f"[sticky note delete error] {exc}")

            sent = await message.channel.send(doc["text"])
            last_sticky_msg[message.channel.id] = sent.id

            await sticky_col.update_one(
                {
                    "guild": str(message.guild.id),
                    "channel": str(message.channel.id),
                },
                {"$set": {"message": sent.id}},
            )
        except Exception as exc:
            print(f"[sticky repost error] {exc}")

    async def _handle_one_time_channel(self, message: discord.Message) -> None:
        try:
            guild_id = str(message.guild.id)
            channel_id = str(message.channel.id)

            if (
                guild_id in onetime_channels
                and channel_id in onetime_channels[guild_id]
                and not await has_staff_role(message.author, message.guild)
            ):
                user_id = str(message.author.id)
                if user_id not in onetime_channels[guild_id][channel_id]:
                    onetime_channels[guild_id][channel_id][user_id] = datetime.now(
                        timezone.utc
                    )
                    await settings_col.update_one(
                        {"guild": guild_id},
                        {
                            "$set": {
                                f"onetime_channels.{channel_id}.{user_id}": (
                                    datetime.now(timezone.utc)
                                )
                            }
                        },
                        upsert=True,
                    )
                    try:
                        await message.channel.set_permissions(
                            message.author,
                            send_messages=False,
                            reason="One-time message used",
                        )
                        await message.channel.send(
                            f"⚠️ {message.author.mention} has used their one-time "
                            "message in this channel. Staff can restore permissions "
                            "with `.restore`."
                        )
                    except Exception as exc:
                        print(f"[One-time permission error] {exc}")
        except Exception as exc:
            print(f"[One-time message error] {exc}")

    async def _handle_afk(self, message: discord.Message) -> None:
        try:
            for user in message.mentions:
                doc = await afk_col.find_one(
                    {"_id": f"{message.guild.id}-{user.id}"}
                )
                if not doc:
                    continue
                reason = doc.get("reason", "AFK")
                timestamp = doc.get("timestamp")
                if timestamp:
                    dt = date_parser.isoparse(timestamp)
                    elapsed = datetime.now(timezone.utc) - dt.replace(
                        tzinfo=timezone.utc
                    )
                    mins = int(elapsed.total_seconds() // 60)
                    hours, mins = divmod(mins, 60)
                    time_str = (
                        f"{hours}h {mins}m ago" if hours else f"{mins} minutes ago"
                    )
                    await message.channel.send(
                        f"📨 {user.display_name} is AFK ({reason}) - set {time_str}."
                    )
                else:
                    await message.channel.send(
                        f"📨 {user.display_name} is AFK: {reason}"
                    )

            content_lower = message.content.lower()
            if content_lower.startswith(".afk") or content_lower.startswith("/afk"):
                return

            afk_key = f"{message.guild.id}-{message.author.id}"
            doc = await afk_col.find_one({"_id": afk_key})
            if not doc:
                return

            await afk_col.delete_one({"_id": afk_key})
            original_nick = doc.get("original_nick")
            current_nick = message.author.display_name

            try:
                if current_nick.startswith("[AFK]"):
                    await message.author.edit(nick=original_nick)
            except discord.Forbidden:
                await message.channel.send(
                    "⚠️ I couldn't restore your nickname due to role hierarchy, "
                    "but AFK is removed.",
                    delete_after=5,
                )
            except discord.HTTPException:
                await message.channel.send(
                    "⚠️ Something went wrong while restoring your nickname, "
                    "but AFK is removed."
                )

            await message.channel.send(
                f"✅ Welcome back, {message.author.mention}! AFK removed.",
                delete_after=5,
            )
        except Exception as exc:
            print(f"[afk error] {exc}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageEventsCog(bot))
