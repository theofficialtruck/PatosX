"""Staff performance dropdown view used by the ``performance`` command."""

from __future__ import annotations

import discord

from bot.utils.analytics import generate_performance_analytics


class PerformanceView(discord.ui.View):
    """Pick a staff member from a dropdown to render their analytics embed."""

    def __init__(self, guild_id, staff_members, days: int = 30) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.staff_members = staff_members
        self.days = days

        options = [
            discord.SelectOption(
                label=member.display_name,
                description=f"Review {member.display_name}'s performance",
                value=str(member.id),
                emoji="👤",
            )
            for member in staff_members[:25]
        ]

        self.dropdown = discord.ui.Select(
            placeholder="📊 Select a staff member to review...",
            options=options,
        )
        self.dropdown.callback = self._dropdown_callback
        self.add_item(self.dropdown)

    async def _dropdown_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id not in [m.id for m in self.staff_members]:
            await interaction.response.send_message(
                "❌ Only staff members can use this!", ephemeral=True
            )
            return

        selected_id = int(self.dropdown.values[0])
        selected_member = interaction.guild.get_member(selected_id)
        if not selected_member:
            await interaction.response.send_message(
                "❌ Staff member not found!", ephemeral=True
            )
            return

        analytics = await generate_performance_analytics(
            self.guild_id, selected_id, days=self.days
        )

        embed = discord.Embed(
            title=f"📊 Performance Review: {selected_member.display_name}",
            description=f"Analytics for {selected_member.mention}",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="📈 Basic Statistics",
            value=(
                f"**Total Actions:** {analytics['total_actions']}\n"
                f"**Messages Sent:** {analytics['total_messages']:,}\n"
                f"**Commands Used:** {analytics['commands_used']:,}\n"
                f"**Staff Since:** {analytics['staff_since']}"
            ),
            inline=False,
        )

        if analytics["punishments"]["total"] > 0:
            punish_text = f"**Total:** {analytics['punishments']['total']}\n"
            for ptype, count in analytics["punishments"].items():
                if ptype != "total" and count > 0:
                    punish_text += f"**{ptype.capitalize()}:** {count}\n"
            embed.add_field(name="⚖️ Punishments", value=punish_text, inline=False)
        else:
            embed.add_field(
                name="⚖️ Punishments",
                value="No punishments recorded",
                inline=False,
            )

        embed.add_field(
            name="🕐 Activity Metrics",
            value=(
                f"**Avg. Actions/Day:** {analytics['avg_actions_per_day']:.1f}\n"
                f"**Most Active Day:** {analytics['most_active_day']}\n"
                f"**Peak Hour:** {analytics['peak_hour']}:00\n"
                f"**Active This Week:** "
                f"{'Yes' if analytics['active_this_week'] else 'No'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Efficiency",
            value=f"**Efficiency:** {analytics['efficiency']:.1f}%",
            inline=False,
        )

        if analytics["recent_activity"]:
            recent_text = "\n".join(
                [f"• {activity}" for activity in analytics["recent_activity"][:5]]
            )
            embed.add_field(
                name="📝 Recent Activity", value=recent_text, inline=False
            )

        embed.set_thumbnail(url=selected_member.display_avatar.url)
        embed.set_footer(text=f"Performance data for last {self.days} days")
        await interaction.response.send_message(embed=embed, ephemeral=True)


__all__ = ["PerformanceView"]
