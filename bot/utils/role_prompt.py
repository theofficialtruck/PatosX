"""Interactive role prompt used by the shop and configuration commands."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from bot.utils.state import get_bot


async def prompt_for_role(ctx: commands.Context) -> int | None:
    """Ask the invoker for a role ID or name and return its id, or ``None``."""
    bot = get_bot()

    def check(message: discord.Message) -> bool:
        return message.author == ctx.author and message.channel == ctx.channel

    messages_to_delete: list[discord.Message] = []

    while True:
        bot_msg = await ctx.send(
            "📌 Please enter the **role ID or role name** "
            "(or type `cancel` to skip):"
        )
        messages_to_delete.append(bot_msg)

        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            messages_to_delete.append(msg)
        except asyncio.TimeoutError:
            timeout_msg = await ctx.send("⌛ Cancelled due to timeout.")
            messages_to_delete.append(timeout_msg)
            await asyncio.sleep(3)
            try:
                await ctx.channel.delete_messages(messages_to_delete)
            except Exception:
                pass
            return None

        content = msg.content.strip()
        if content.lower() == "cancel":
            cancel_msg = await ctx.send("❌ Role linking cancelled.")
            messages_to_delete.append(cancel_msg)
            await asyncio.sleep(3)
            try:
                await ctx.channel.delete_messages(messages_to_delete)
            except Exception:
                pass
            return None

        role: discord.Role | None = None
        try:
            role_id = int(content)
            role = ctx.guild.get_role(role_id)
        except ValueError:
            role = discord.utils.get(ctx.guild.roles, name=content)
            if not role:
                role = discord.utils.find(
                    lambda r: r.name.lower() == content.lower(),
                    ctx.guild.roles,
                )

        if not role:
            err_msg = await ctx.send(
                "❌ No role found with that ID or name. Please try again."
            )
            messages_to_delete.append(err_msg)
            continue

        success_msg = await ctx.send(f"✅ Linked role: {role.mention}")
        messages_to_delete.append(success_msg)
        await asyncio.sleep(3)
        try:
            await ctx.channel.delete_messages(messages_to_delete)
        except Exception:
            pass
        return role.id


__all__ = ["prompt_for_role"]
