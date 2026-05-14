import json
import random
from pathlib import Path

import discord

TURN_TIMEOUT = 30
DATA_PATH = Path(__file__).resolve().parent / "data" / "trends.json"


def _load_terms():
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)["terms"]


class HoLView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=TURN_TIMEOUT)
        self.user_id = user_id
        self.choice = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your game.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Higher ⬆", style=discord.ButtonStyle.success)
    async def higher(self, interaction, button):
        self.choice = "higher"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Lower ⬇", style=discord.ButtonStyle.danger)
    async def lower(self, interaction, button):
        self.choice = "lower"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.secondary)
    async def quit(self, interaction, button):
        self.choice = "stop"
        await interaction.response.defer()
        self.stop()


async def start(thread, user, bot):
    try:
        terms = _load_terms()
    except FileNotFoundError:
        await thread.send(
            "Trends dataset is missing. Run `python scripts/build_trends_dataset.py` "
            "to generate it, then try again."
        )
        return

    if len(terms) < 2:
        await thread.send("Not enough terms in the dataset to play.")
        return

    pool = list(terms.items())
    streak = 0
    left_term, left_score = random.choice(pool)
    await thread.send(
        "**Higher or Lower — Google Trends edition**\n"
        "Which search term has a **higher** Google Trends score? "
        "Scores are 0–100 (relative average interest, last 12 months).\n"
        "Press **Higher** if the right term is searched more than the left, **Lower** if less.\n"
        "Ties count as wrong. Press **Stop** to cash out."
    )

    while True:
        # pick a distinct second term
        right_term, right_score = random.choice(pool)
        while right_term == left_term:
            right_term, right_score = random.choice(pool)

        view = HoLView(user.id)
        msg = await thread.send(
            f"Streak: **{streak}**\n"
            f"**{left_term}**  →  score **{left_score}**\n"
            f"**{right_term}**  →  score **???**",
            view=view,
        )
        timed_out = await view.wait()

        for child in view.children:
            child.disabled = True
        try:
            await msg.edit(view=view)
        except discord.HTTPException:
            pass

        if timed_out or view.choice is None:
            await thread.send(
                f"Out of time! `{right_term}` was **{right_score}**. Final streak: **{streak}**."
            )
            return
        if view.choice == "stop":
            await thread.send(f"Cashed out with a streak of **{streak}**! 🎉")
            return

        correct = (
            (view.choice == "higher" and right_score > left_score)
            or (view.choice == "lower" and right_score < left_score)
        )

        if not correct:
            verdict = "a tie" if right_score == left_score else "wrong"
            await thread.send(
                f"`{right_term}` was **{right_score}** — {verdict}! Final streak: **{streak}**."
            )
            return

        streak += 1
        await thread.send(f"`{right_term}` was **{right_score}** — correct! ✅")
        # right term becomes the new left
        left_term, left_score = right_term, right_score
