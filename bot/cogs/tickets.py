"""Ticket commands. The bulk of the logic lives in views.tickets."""

from __future__ import annotations

import io
import traceback
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import (
    settings_col,
    ticket_panels_col,
    tickets_col,
)
from bot.utils.checks import staff_only, staffperm
from bot.utils.errors import is_prefix, send_hybrid_error
from bot.utils.members import get_category_support_members
from bot.utils.permissions import has_staff_role
from bot.utils.tickets import actually_close_ticket
from bot.views.tickets import (
    TicketAddButtonModal,
    TicketPanelEditView,
    TicketPanelView,
    TicketSetupModal,
)
from bot.views.transcripts import TranscriptPaginationView


class TicketsCog(commands.Cog, name="Tickets"):
    """All slash/prefix tickets commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="ticketaddbutton",
        description="Add a button to an existing ticket panel (form). Staff-only.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def ticketaddbutton(self, ctx: commands.Context) -> None:
        try:
            if is_prefix(ctx):
                def check(message: discord.Message) -> bool:
                    return (
                        message.author.id == ctx.author.id
                        and message.channel.id == ctx.channel.id
                    )

                await ctx.send("📝 Enter the **panel name**:")
                panel_name_msg = await self.bot.wait_for("message", check=check)
                panel_name = panel_name_msg.content

                await ctx.send("🗂 Enter the **ticket category name**:")
                category_msg = await self.bot.wait_for("message", check=check)
                category_name = category_msg.content

                await ctx.send("🔘 Enter the **button label**:")
                label_msg = await self.bot.wait_for("message", check=check)
                button_label = label_msg.content

                await ctx.send("😎 Enter an **emoji** (optional, type `none` to skip):")
                emoji_msg = await self.bot.wait_for("message", check=check)
                emoji = (
                    None if emoji_msg.content.lower() == "none" else emoji_msg.content
                )

                guild = ctx.guild
                panel_data = await ticket_panels_col.find_one(
                    {"guild": str(guild.id), "panel_name": panel_name}
                )
                if not panel_data:
                    return await ctx.send(
                        f"❌ No panel found with name `{panel_name}`."
                    )

                new_button = {
                    "category_name": category_name,
                    "label": button_label,
                    "emoji": emoji,
                }
                await ticket_panels_col.update_one(
                    {"guild": str(guild.id), "panel_name": panel_name},
                    {"$push": {"buttons": new_button}},
                )
                return await ctx.send(
                    f"✅ Added button to panel `{panel_name}`:\n"
                    f"{emoji or ''} **{button_label}** → "
                    f"Category **{category_name}**"
                )

            await ctx.interaction.response.send_modal(TicketAddButtonModal(ctx))
        except Exception as exc:
            print("ticketaddbutton ERROR:", traceback.format_exc())
            if ctx.interaction and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(
                    f"❌ Error:\n```{exc}```", ephemeral=True
                )

    @commands.hybrid_command(
        name="ticketsetup",
        description="Create interactive ticket panel. Staff-only.",
    )
    @app_commands.describe(
        panel_name="Name for the ticket panel (e.g., 'Support', 'Help Desk')"
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def ticketsetup(
        self,
        ctx: commands.Context,
        panel_name: str = "Support",
    ) -> None:
        try:
            data = await settings_col.find_one({"guild": str(ctx.guild.id)})
            staff_role_id = data.get("staff_role") if data else None
            if (
                not staff_role_id
                or staff_role_id not in [r.id for r in ctx.author.roles]
            ):
                msg = "❌ Only staff members can create a panel."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return

            if is_prefix(ctx):
                return await ctx.send(
                    "⚠ This command requires modal interaction. Please use the "
                    "command properly."
                )
            await ctx.interaction.response.send_modal(TicketSetupModal(ctx))
        except Exception as exc:
            print("ticketsetup ERROR:", traceback.format_exc())
            if ctx.interaction and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(
                    f"❌ Error:\n```{exc}```", ephemeral=True
                )

    @commands.hybrid_command(
        name="ticketpanel",
        description="Post a saved ticket panel. Staff-only.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def ticketpanel(
        self, ctx: commands.Context, panel_name: str
    ) -> None:
        try:
            panel_data = await ticket_panels_col.find_one(
                {"guild": str(ctx.guild.id), "panel_name": panel_name}
            )
            if not panel_data:
                msg = f"❌ No ticket panel found with name `{panel_name}`."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return

            embed = discord.Embed(
                title=panel_data.get("ticket_embed_title", "🎫 Ticket Panel"),
                description=panel_data.get(
                    "ticket_embed_desc",
                    "Click a button below to create a ticket.",
                ),
                color=discord.Color(int(panel_data.get("ticket_embed_color", 0x5865F2))),
            )
            view = TicketPanelView(panel_data)

            if ctx.interaction:
                msg = await ctx.interaction.response.send_message(
                    embed=embed, view=view
                )
            else:
                msg = await ctx.send(embed=embed, view=view)

            await ticket_panels_col.update_one(
                {"_id": panel_data["_id"]},
                {
                    "$set": {
                        "message_id": msg.id,
                        "channel_id": str(msg.channel.id),
                    }
                },
            )

        except Exception as exc:
            print("ticketpanel ERROR:", traceback.format_exc())
            if ctx.interaction:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(
                        f"❌ Error:\n```{exc}```", ephemeral=True
                    )
            else:
                await ctx.send(f"❌ Error:\n```{exc}```")

    @commands.hybrid_command(
        name="ticketeditbutton",
        description="Edit a button in a ticket panel. Staff-only.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def ticketeditbutton(
        self, ctx: commands.Context, panel_name: str
    ) -> None:
        try:
            if ctx.interaction and not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer(ephemeral=True)

            panel_data = await ticket_panels_col.find_one(
                {"guild": str(ctx.guild.id), "panel_name": panel_name}
            )
            if not panel_data:
                msg = f"❌ No ticket panel found with name `{panel_name}`."
                if ctx.interaction:
                    await ctx.interaction.followup.send(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return

            embed = discord.Embed(
                title=f"📝 Edit Mode: {panel_name}",
                description="Click a button below to edit or delete it.",
                color=discord.Color.orange(),
            )
            view = TicketPanelEditView(ctx, panel_data)

            if ctx.interaction:
                await ctx.interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                await ctx.send(embed=embed, view=view)
        except Exception as exc:
            print("ticketeditbutton ERROR:", traceback.format_exc())
            if ctx.interaction:
                await ctx.interaction.followup.send(f"❌ Error:\n```{exc}```", ephemeral=True)
            else:
                await ctx.send(f"❌ Error:\n```{exc}```")

    @commands.hybrid_command(
        name="ticketdeletepanel",
        description="Delete a saved ticket panel. Staff-only.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def ticketdeletepanel(
        self, ctx: commands.Context, panel_name: str
    ) -> None:
        try:
            result = await ticket_panels_col.delete_one(
                {"guild": str(ctx.guild.id), "panel_name": panel_name}
            )
            if result.deleted_count == 0:
                msg = f"❌ No panel found with name `{panel_name}`."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return

            msg = f"🗑 Panel `{panel_name}` deleted successfully."
            if ctx.interaction:
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
        except Exception:
            print("ticketdeletepanel ERROR:", traceback.format_exc())

    @commands.hybrid_command(
        name="ticketlist",
        description="List all saved ticket panels. Staff-only.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def ticketlist(self, ctx: commands.Context) -> None:
        try:
            panels = await ticket_panels_col.find(
                {"guild": str(ctx.guild.id)}
            ).to_list(length=50)
            if not panels:
                msg = "❌ No saved ticket panels."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return

            embed = discord.Embed(title="📋 Saved Ticket Panels", color=discord.Color.blurple())
            for panel in panels:
                embed.add_field(
                    name=panel["panel_name"],
                    value=f"{len(panel.get('buttons', []))} button(s)",
                    inline=False,
                )

            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
        except Exception:
            print("ticketlist ERROR:", traceback.format_exc())

    @commands.hybrid_command(
        name="ticketclose",
        description="Request to close the current ticket.",
    )
    async def ticketclose(self, ctx: commands.Context) -> None:
        async def send_public(*, content=None, embed=None):
            if is_prefix(ctx):
                return await ctx.send(content=content, embed=embed)
            if ctx.interaction.response.is_done():
                return await ctx.interaction.followup.send(
                    content=content, embed=embed, ephemeral=False
                )
            return await ctx.interaction.response.send_message(
                content=content, embed=embed, ephemeral=False
            )

        async def local_error_handler(func):
            try:
                return await func()
            except Exception as exc:
                embed = discord.Embed(
                    title="⚠️ Error",
                    description=f"An unexpected error occurred:\n```{exc}```",
                    color=discord.Color.red(),
                )
                await send_hybrid_error(ctx, embed=embed)

        async def inner():
            channel = ctx.channel
            ticket_entry = await tickets_col.find_one(
                {"guild": str(ctx.guild.id), "channel_id": str(channel.id)}
            )
            if not ticket_entry:
                return await send_hybrid_error(
                    ctx,
                    content="❌ This command can only be used inside a ticket channel.",
                )

            opener = (
                channel.guild.get_member(int(ticket_entry.get("owner_id")))
                if ticket_entry.get("owner_id")
                else None
            )
            if not opener:
                return await send_hybrid_error(
                    ctx, content="⚠️ Could not find the ticket opener."
                )

            await tickets_col.update_one(
                {"_id": ticket_entry["_id"]},
                {"$set": {"close_pending": True}},
            )
            await send_public(
                content=(
                    f"{opener.mention}, do you confirm closing this ticket? "
                    "Type `confirm` to close or `cancel` to keep it open. "
                    "(This will wait until you reply, no time limit.)"
                )
            )

        await local_error_handler(inner)

    @commands.hybrid_command(
        name="ticketforceclose",
        description="Force close the current ticket.",
    )
    @staff_only()
    async def ticketforceclose(self, ctx: commands.Context) -> None:
        async def local_error_handler(func):
            try:
                return await func()
            except Exception as exc:
                embed = discord.Embed(
                    title="⚠️ Error",
                    description=f"An unexpected error occurred:\n```{exc}```",
                    color=discord.Color.red(),
                )
                await send_hybrid_error(ctx, embed=embed)

        async def inner():
            channel = ctx.channel
            ticket_entry = await tickets_col.find_one(
                {"guild": str(ctx.guild.id), "channel_id": str(channel.id)}
            )
            if not ticket_entry:
                return await send_hybrid_error(
                    ctx,
                    content="❌ This command can only be used inside a ticket channel.",
                )

            opener = (
                channel.guild.get_member(int(ticket_entry.get("owner_id")))
                if ticket_entry.get("owner_id")
                else None
            )
            await actually_close_ticket(ctx, opener, forced=True)
            await tickets_col.delete_one({"_id": ticket_entry["_id"]})

        await local_error_handler(inner)

    @commands.hybrid_command(
        name="transcript",
        description="Fetch a ticket transcript. Staff-only.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def transcript(
        self, ctx: commands.Context, ticket_id: str
    ) -> None:
        try:
            ticket = await tickets_col.find_one(
                {"ticket_id": ticket_id, "guild_id": str(ctx.guild.id)}
            )
            if not ticket:
                msg = "❌ No ticket found with that ID."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return

            def format_time(dt, style: str = "both") -> str:
                if not dt:
                    return "Unknown"
                if isinstance(dt, str):
                    try:
                        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                    except ValueError:
                        return str(dt)
                if not isinstance(dt, datetime):
                    return "Unknown"
                ts = int(dt.timestamp())
                if style == "full":
                    return f"<t:{ts}:F>"
                if style == "short":
                    return f"<t:{ts}:f>"
                if style == "relative":
                    return f"<t:{ts}:R>"
                return f"<t:{ts}:f> • <t:{ts}:R>"

            embed = discord.Embed(
                title=f"🎟 Transcript for {ticket_id}",
                color=0x5865F2,
                timestamp=datetime.utcnow(),
            )
            embed.add_field(name="Opened by", value=f"<@{ticket['opener_id']}>", inline=True)
            embed.add_field(name="Closed by", value=f"<@{ticket['closer_id']}>", inline=True)
            embed.add_field(name="Opened at", value=format_time(ticket.get("created_at")), inline=True)
            embed.add_field(name="Closed at", value=format_time(ticket.get("closed_at")), inline=True)

            transcript_file = io.StringIO(ticket["transcript"])
            discord_file = discord.File(
                fp=transcript_file, filename=f"{ticket_id}_transcript.txt"
            )

            if ctx.interaction:
                await ctx.interaction.response.send_message(
                    embed=embed, file=discord_file, ephemeral=True
                )
            else:
                await ctx.send(embed=embed, file=discord_file)
        except Exception:
            print("transcript ERROR:", traceback.format_exc())

    @commands.hybrid_command(
        name="transcriptsearch",
        description="Search tickets by username. Staff-only.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def transcriptsearch(
        self, ctx: commands.Context, username: str
    ) -> None:
        try:
            query = {
                "guild_id": str(ctx.guild.id),
                "$or": [
                    {"opener_name": {"$regex": username, "$options": "i"}},
                    {"closer_name": {"$regex": username, "$options": "i"}},
                ],
            }
            tickets = await tickets_col.find(query).to_list(length=20)
            if not tickets:
                msg = f"❌ No tickets found for username containing `{username}`."
                if ctx.interaction:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                else:
                    await ctx.send(msg)
                return

            embed = discord.Embed(
                title=f"🔍 Tickets matching '{username}'",
                color=0x57F287,
            )
            for ticket in tickets:
                embed.add_field(
                    name=ticket["ticket_id"],
                    value=(
                        f"Opened by: <@{ticket.get('opener_id', 'unknown')}> | "
                        f"Closed by: <@{ticket.get('closer_id', 'unknown')}>"
                    ),
                    inline=False,
                )

            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
        except Exception:
            print("transcriptsearch ERROR:", traceback.format_exc())

    @commands.hybrid_command(
        name="transcriptlist",
        description="List all tickets (open & closed) with details. Staff-only.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def transcriptlist(self, ctx: commands.Context) -> None:
        try:
            tickets = await tickets_col.find(
                {"guild_id": str(ctx.guild.id)}
            ).to_list(length=200)
            if not tickets:
                msg = "❌ No tickets found in this server."
                if is_prefix(ctx):
                    await ctx.send(msg)
                else:
                    await ctx.interaction.response.send_message(msg, ephemeral=True)
                return

            view = TranscriptPaginationView(ctx, tickets)
            embed = await view.build_embed()
            view.children[0].disabled = True
            view.children[1].disabled = view.max_page == 0

            if is_prefix(ctx):
                msg = await ctx.send(embed=embed, view=view)
            else:
                await ctx.interaction.response.send_message(
                    embed=embed, view=view, ephemeral=True
                )
                msg = await ctx.interaction.original_response()
            view.message = msg
        except Exception as exc:
            print("transcriptlist ERROR:", traceback.format_exc())
            if not is_prefix(ctx) and ctx.interaction and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(
                    f"❌ Error:\n```{exc}```", ephemeral=True
                )
            else:
                await ctx.send(f"❌ Error:\n```{exc}```")

    @commands.hybrid_command(
        name="ticketadduser",
        description="Add a user to the current ticket.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def ticketadduser(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        channel = ctx.channel
        ticket_entry = await tickets_col.find_one(
            {"guild": str(ctx.guild.id), "channel_id": str(channel.id)}
        )
        if not ticket_entry:
            return await ctx.send(
                "❌ This command can only be used inside a ticket channel."
            )

        try:
            overwrite = channel.overwrites_for(member)
            overwrite.view_channel = True
            overwrite.send_messages = True
            overwrite.read_message_history = True
            overwrite.embed_links = True
            overwrite.attach_files = True
            await channel.set_permissions(member, overwrite=overwrite)
            await ctx.send(f"✅ {member.mention} has been added to this ticket.")
        except discord.Forbidden:
            await ctx.send("❌ I don’t have permission to edit channel permissions.")
        except Exception as exc:
            await ctx.send(f"⚠️ Failed to add user: `{exc}`")

    @commands.hybrid_command(
        name="ticketremoveuser",
        description="Remove a user from the current ticket.",
    )
    @staffperm("tickets:admin")
    @staff_only()
    async def ticketremoveuser(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        channel = ctx.channel
        ticket_entry = await tickets_col.find_one(
            {"guild": str(ctx.guild.id), "channel_id": str(channel.id)}
        )
        if not ticket_entry:
            return await ctx.send(
                "❌ This command can only be used inside a ticket channel."
            )

        try:
            await channel.set_permissions(member, overwrite=None)
            await ctx.send(f"✅ {member.mention} has been removed from this ticket.")
        except discord.Forbidden:
            await ctx.send("❌ I don’t have permission to edit channel permissions.")
        except Exception as exc:
            await ctx.send(f"⚠️ Failed to remove user: `{exc}`")

    @commands.command()
    @staff_only()
    async def ticketsync(
        self, ctx: commands.Context, scope: str | None = None
    ) -> None:
        try:
            if scope and scope.lower() == "all":
                updated = 0
                docs = await tickets_col.find(
                    {"guild": str(ctx.guild.id)}
                ).to_list(length=200)
                for ticket in docs:
                    channel_id = (
                        int(ticket.get("channel_id"))
                        if ticket.get("channel_id")
                        else None
                    )
                    category_name = ticket.get("category")
                    if not channel_id or not category_name:
                        continue
                    channel = ctx.guild.get_channel(channel_id)
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    desired = []
                    for member in await get_category_support_members(
                        ctx.guild, category_name
                    ):
                        if await has_staff_role(member, ctx.guild):
                            desired.append(member)
                    desired_ids = {member.id for member in desired}
                    for target, overwrite in channel.overwrites.items():
                        if isinstance(target, discord.Member):
                            if await has_staff_role(target, ctx.guild):
                                if (
                                    overwrite.view_channel
                                    and target.id not in desired_ids
                                ):
                                    await channel.set_permissions(target, overwrite=None)
                    for member in desired:
                        ow = channel.overwrites_for(member)
                        ow.view_channel = True
                        ow.send_messages = True
                        ow.read_message_history = True
                        ow.embed_links = True
                        ow.attach_files = True
                        await channel.set_permissions(member, overwrite=ow)
                    updated += 1
                return await ctx.send(
                    f"✅ Synced staff access for `{updated}` open tickets."
                )

            channel = ctx.channel
            ticket = await tickets_col.find_one(
                {"guild": str(ctx.guild.id), "channel_id": str(channel.id)}
            )
            if not ticket:
                return await ctx.send(
                    "❌ This command can only be used inside an open ticket "
                    "channel, or use `.ticketsync all`."
                )
            category_name = ticket.get("category")
            if not category_name:
                return await ctx.send(
                    "⚠️ Could not determine ticket category for this channel."
                )

            desired = []
            for member in await get_category_support_members(
                ctx.guild, category_name
            ):
                if await has_staff_role(member, ctx.guild):
                    desired.append(member)
            desired_ids = {member.id for member in desired}
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member):
                    if await has_staff_role(target, ctx.guild):
                        if (
                            overwrite.view_channel
                            and target.id not in desired_ids
                        ):
                            await channel.set_permissions(target, overwrite=None)
            for member in desired:
                ow = channel.overwrites_for(member)
                ow.view_channel = True
                ow.send_messages = True
                ow.read_message_history = True
                ow.embed_links = True
                ow.attach_files = True
                await channel.set_permissions(member, overwrite=ow)
            await ctx.send("✅ Staff access synced for this ticket.")
        except discord.Forbidden:
            await ctx.send("❌ I don’t have permission to edit channel permissions.")
        except Exception as exc:
            await ctx.send(f"⚠️ Error: `{exc}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketsCog(bot))
