"""Views used by the ``duckquiz`` command."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import discord

from bot.config.constants import PASS_PCT
from bot.database import config_col, quiz_col


class AnswerButton(discord.ui.Button):
    """Single answer button — disables siblings on click."""

    def __init__(self, label: str, value: int, parent_view: "QuizView") -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=label,
            custom_id=str(value),
        )
        self.value = value
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_view
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message(
                "This quiz isn't yours.", ephemeral=True
            )

        idx = view.current_index
        if view.answered_ids.get(idx):
            return await interaction.response.send_message(
                "You already answered this question.", ephemeral=True
            )

        view.answered_ids[idx] = True
        correct_answer = view.questions[idx]["answer"]
        if self.value == correct_answer:
            view.score += 1

        view.disable_all_buttons()
        await interaction.response.edit_message(view=view)

        reply = (
            "✅ Correct!"
            if self.value == correct_answer
            else f"❌ Wrong! Answer was: {view.questions[idx]['options'][correct_answer - 1]}"
        )
        await interaction.followup.send(reply, ephemeral=True)

        view.current_index += 1
        await view.show_next(interaction)


class QuizView(discord.ui.View):
    """Hold the four answer buttons and walk through the question list."""

    def __init__(self, ctx, quiz_id, questions_list: list[dict]) -> None:
        super().__init__(timeout=300)
        self.ctx = ctx
        self.user_id = ctx.author.id
        self.quiz_id = quiz_id
        self.questions = questions_list
        self.current_index = 0
        self.score = 0
        self.answered_ids: dict[int, bool] = {}
        for i in range(1, 5):
            self.add_item(AnswerButton(str(i), i, self))

    def disable_all_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    async def show_next(self, interaction: discord.Interaction | None = None) -> None:
        if self.current_index >= len(self.questions):
            await self.finish_quiz(interaction)
            return

        q = self.questions[self.current_index]
        opts = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(q["options"]))
        embed = discord.Embed(
            title=f"Question {self.current_index + 1}/{len(self.questions)}",
            description=q["q"],
            color=discord.Color.teal(),
        )
        embed.add_field(name="Options", value=opts, inline=False)
        embed.set_footer(text="Click a button below to answer.")

        self.clear_items()
        for i in range(1, 5):
            self.add_item(AnswerButton(str(i), i, self))

        if interaction:
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        else:
            await self.ctx.send(embed=embed, view=self, ephemeral=True)

    async def finish_quiz(self, interaction: discord.Interaction | None = None) -> None:
        pct = self.score / len(self.questions) * 100.0
        passed = pct >= PASS_PCT

        await quiz_col.update_one(
            {"_id": self.quiz_id},
            {
                "$set": {
                    "score": self.score,
                    "completed": datetime.now(timezone.utc),
                    "passed": passed,
                }
            },
        )

        result = (
            f"📊 You scored **{self.score}/{len(self.questions)}** = **{pct:.1f}%**"
        )

        if passed:
            config = await config_col.find_one({"guild": str(self.ctx.guild.id)}) or {}
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except Exception:
                    config = {}
            if not isinstance(config, dict):
                config = {}

            role_ids = config.get("ROLE_ID", [])
            if isinstance(role_ids, int):
                role_ids = [role_ids]
            elif isinstance(role_ids, str) and role_ids.isdigit():
                role_ids = [int(role_ids)]
            elif isinstance(role_ids, list):
                role_ids = [int(r) for r in role_ids if str(r).isdigit()]
            else:
                role_ids = []

            roles_to_add = [
                self.ctx.guild.get_role(rid)
                for rid in role_ids
                if self.ctx.guild.get_role(rid)
            ]

            if roles_to_add:
                await self.ctx.author.add_roles(*roles_to_add, reason="Passed duck quiz")
                role_names = ", ".join([r.name for r in roles_to_add])
                result += f"\n🎉 You passed and earned the **{role_names}** role!"
            else:
                result += "\n⚠️ Role configured, but could not find it on the server."

        if interaction:
            await interaction.followup.send(result, ephemeral=True)
        else:
            await self.ctx.send(result)

        self.stop()


__all__ = ["AnswerButton", "QuizView"]
