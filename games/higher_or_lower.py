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
        "Press **Higher** if the right term is searched more than the left, **Lower** if less. "
        "Press **Stop** to cash out."
    )

    while True:
        # only pick terms with a different score so the answer is never a tie
        candidates = [(t, s) for t, s in pool if s != left_score]
        if not candidates:
            await thread.send(
                f"Ran out of eligible terms. Final streak: **{streak}**."
            )
            return
        right_term, right_score = random.choice(candidates)

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
            await thread.send(
                f"`{right_term}` was **{right_score}** — wrong! Final streak: **{streak}**."
            )
            return

        streak += 1
        await thread.send(f"`{right_term}` was **{right_score}** — correct! ✅")
        # right term becomes the new left
        left_term, left_score = right_term, right_score


# -----------------------------------------------------------------------------
# Multiplayer click-first race
# -----------------------------------------------------------------------------

import asyncio as _aio

MP_ROUNDS = 10
MP_ROUND_TIMEOUT = 20


class _MPHoLView(discord.ui.View):
    def __init__(self, player_ids: set[int], correct: str):
        super().__init__(timeout=MP_ROUND_TIMEOUT)
        self.player_ids = player_ids
        self.correct = correct
        self.locked_out: set[int] = set()
        self.winner_id: int | None = None
        self.finished = _aio.Event()

    async def _click(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id not in self.player_ids:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        if interaction.user.id in self.locked_out:
            await interaction.response.send_message(
                "You're locked out of this round. Wait for the next one.", ephemeral=True
            )
            return
        if self.finished.is_set():
            await interaction.response.send_message("Too late — round's over.", ephemeral=True)
            return

        if choice == self.correct:
            self.winner_id = interaction.user.id
            await interaction.response.send_message("✅ You got it first!", ephemeral=True)
            for child in self.children:
                child.disabled = True
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
            self.finished.set()
        else:
            self.locked_out.add(interaction.user.id)
            await interaction.response.send_message(
                f"❌ Wrong — you're out for this round.", ephemeral=True
            )
            # If everyone is locked out, end the round
            if self.locked_out >= self.player_ids:
                self.finished.set()

    @discord.ui.button(label="Higher ⬆", style=discord.ButtonStyle.success)
    async def higher(self, interaction, _): await self._click(interaction, "higher")

    @discord.ui.button(label="Lower ⬇", style=discord.ButtonStyle.danger)
    async def lower(self, interaction, _): await self._click(interaction, "lower")


async def start_multi(thread, players, bot):
    try:
        terms = _load_terms()
    except FileNotFoundError:
        await thread.send("Trends dataset missing.")
        return
    if len(terms) < 2:
        await thread.send("Not enough terms.")
        return

    pool = list(terms.items())
    player_ids = {p.id for p in players}
    players_by_id = {p.id: p for p in players}
    scores: dict[int, int] = {pid: 0 for pid in player_ids}

    names = ", ".join(p.display_name for p in players)
    await thread.send(
        f"**Higher or Lower — MP Race**\n"
        f"{MP_ROUNDS} rounds. First to click the correct direction wins the point. "
        f"Wrong click = locked out for that round.\nPlayers: {names}"
    )

    left_term, left_score = random.choice(pool)

    for round_num in range(1, MP_ROUNDS + 1):
        candidates = [(t, s) for t, s in pool if s != left_score]
        if not candidates:
            await thread.send("Ran out of distinct-score terms. Stopping.")
            break
        right_term, right_score = random.choice(candidates)
        correct = "higher" if right_score > left_score else "lower"

        scoreboard = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
        view = _MPHoLView(player_ids, correct)
        await thread.send(
            f"**Round {round_num}/{MP_ROUNDS}** — {scoreboard}\n"
            f"**{left_term}** → score **{left_score}**\n"
            f"**{right_term}** → score **???**",
            view=view,
        )
        try:
            await _aio.wait_for(view.finished.wait(), timeout=MP_ROUND_TIMEOUT)
        except _aio.TimeoutError:
            pass

        if view.winner_id is not None:
            scores[view.winner_id] += 1
            winner = players_by_id[view.winner_id]
            await thread.send(
                f"`{right_term}` was **{right_score}** — **{winner.display_name}** scores!"
            )
        else:
            await thread.send(f"`{right_term}` was **{right_score}** — no one got it.")

        left_term, left_score = right_term, right_score

    # Final
    max_score = max(scores.values())
    winners = [players_by_id[pid] for pid, s in scores.items() if s == max_score]
    final = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
    if len(winners) == 1:
        await thread.send(f"🏆 **{winners[0].display_name}** wins!\n{final}")
    else:
        names = ", ".join(w.display_name for w in winners)
        await thread.send(f"🤝 Tie between **{names}**!\n{final}")
