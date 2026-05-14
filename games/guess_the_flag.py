import asyncio
import json
import random
import re
from pathlib import Path

import discord

NUM_ROUNDS = 10
ROUND_TIMEOUT = 30
INTERMISSION = 2.5
MAX_GUESSES = 2
FLAG_URL = "https://flagcdn.com/w320/{code}.png"
COUNTRIES_PATH = Path(__file__).resolve().parent / "data" / "countries.json"

# Common nicknames / acronyms for countries with one. Skip ambiguous ones
# (e.g., "Korea" or "Congo" could refer to two different countries in our list).
ALIASES: dict[str, list[str]] = {
    "United States": ["usa", "us", "america"],
    "United Kingdom": ["uk", "britain", "england", "greatbritain"],
    "United Arab Emirates": ["uae", "emirates"],
    "Czech Republic": ["czechia"],
    "Myanmar": ["burma"],
    "Netherlands": ["holland"],
    "Eswatini": ["swaziland"],
    "Cape Verde": ["caboverde"],
    "Bosnia and Herzegovina": ["bosnia"],
    "Trinidad and Tobago": ["trinidad"],
    "Antigua and Barbuda": ["antigua"],
}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _load_countries() -> list[dict]:
    with open(COUNTRIES_PATH, encoding="utf-8") as f:
        return json.load(f)["countries"]


def _valid_answers(name: str) -> set[str]:
    answers = {_normalize(name)}
    for alias in ALIASES.get(name, []):
        answers.add(_normalize(alias))
    answers.discard("")
    return answers


async def start(thread, user, bot):
    pool = _load_countries()
    if not pool:
        await thread.send("No countries in the dataset.")
        return

    chosen_pool = random.sample(pool, k=min(NUM_ROUNDS, len(pool)))
    total = len(chosen_pool)
    score = 0

    await thread.send(
        f"**Guess the Flag** — {total} rounds. Type the country's name. "
        f"Case + special characters don't matter, and common aliases like `USA`, `UK`, `UAE` work too. "
        f"You get **{MAX_GUESSES}** guesses per round, **{ROUND_TIMEOUT}s** each."
    )

    def is_guess(msg):
        return (
            msg.author.id == user.id
            and msg.channel.id == thread.id
            and bool(msg.content.strip())
        )

    for i, correct in enumerate(chosen_pool, 1):
        valid = _valid_answers(correct["name"])

        embed = discord.Embed(title=f"Round {i}/{total}", description="Whose flag is this?")
        embed.set_image(url=FLAG_URL.format(code=correct["code"]))
        await thread.send(embed=embed)

        for attempt in range(1, MAX_GUESSES + 1):
            try:
                msg = await bot.wait_for("message", check=is_guess, timeout=ROUND_TIMEOUT)
            except asyncio.TimeoutError:
                await thread.send(f"⏱ Out of time. That was **{correct['name']}**. Score: **{score}/{i}**.")
                break

            if _normalize(msg.content) in valid:
                score += 1
                await thread.send(f"✅ Correct! That was **{correct['name']}**. Score: **{score}/{i}**.")
                break

            remaining = MAX_GUESSES - attempt
            if remaining == 0:
                await thread.send(f"❌ Out of guesses. That was **{correct['name']}**. Score: **{score}/{i}**.")
            else:
                await thread.send(f"Not quite. **{remaining}** {'guess' if remaining == 1 else 'guesses'} left.")

        if i < total:
            await asyncio.sleep(INTERMISSION)

    pct = round(100 * score / total)
    await thread.send(f"**Final score: {score}/{total} ({pct}%)** 🏳️")


# -----------------------------------------------------------------------------
# Multiplayer race mode
# -----------------------------------------------------------------------------

MP_ROUND_TIMEOUT = 30


class _FlagGuessModal(discord.ui.Modal, title="Guess the country"):
    def __init__(self, view: "_MPFlagView"):
        super().__init__()
        self.view_ref = view
        self.guess = discord.ui.TextInput(label="Country name", max_length=64, placeholder="e.g. France")
        self.add_item(self.guess)

    async def on_submit(self, interaction: discord.Interaction):
        await self.view_ref.handle_guess(interaction, self.guess.value)


class _MPFlagView(discord.ui.View):
    def __init__(self, player_ids: set[int], correct_name: str, valid: set[str]):
        super().__init__(timeout=MP_ROUND_TIMEOUT)
        self.player_ids = player_ids
        self.correct_name = correct_name
        self.valid = valid
        self.attempts: dict[int, int] = {}
        self.winner_id: int | None = None
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary, emoji="✏️")
    async def guess_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id not in self.player_ids:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        if self.attempts.get(interaction.user.id, 0) >= MAX_GUESSES:
            await interaction.response.send_message("You're out of guesses for this round.", ephemeral=True)
            return
        if self.finished.is_set():
            await interaction.response.send_message("Round's over.", ephemeral=True)
            return
        await interaction.response.send_modal(_FlagGuessModal(self))

    async def handle_guess(self, interaction: discord.Interaction, raw: str):
        async with self._lock:
            if self.finished.is_set():
                await interaction.response.send_message("Too late — someone got it.", ephemeral=True)
                return
            if _normalize(raw) in self.valid:
                self.winner_id = interaction.user.id
                await interaction.response.send_message("✅ You got it first!", ephemeral=True)
                for child in self.children:
                    child.disabled = True
                self.finished.set()
                return
            self.attempts[interaction.user.id] = self.attempts.get(interaction.user.id, 0) + 1
            left = MAX_GUESSES - self.attempts[interaction.user.id]
            if left <= 0:
                await interaction.response.send_message(
                    f"❌ Wrong — out of guesses.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"❌ Wrong — **{left}** {'guess' if left == 1 else 'guesses'} left.", ephemeral=True
                )


async def start_multi(thread, players, bot):
    pool = _load_countries()
    chosen_pool = random.sample(pool, k=min(NUM_ROUNDS, len(pool)))
    total = len(chosen_pool)

    player_ids = {p.id for p in players}
    players_by_id = {p.id: p for p in players}
    scores = {pid: 0 for pid in player_ids}

    names = ", ".join(p.display_name for p in players)
    await thread.send(
        f"**Guess the Flag — MP Race**\n"
        f"{total} rounds. Click **Guess** and type the country. First correct wins the round. "
        f"**{MAX_GUESSES}** guesses each per round, **{MP_ROUND_TIMEOUT}s** per round.\nPlayers: {names}"
    )

    for i, correct in enumerate(chosen_pool, 1):
        valid = _valid_answers(correct["name"])
        scoreboard = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
        embed = discord.Embed(title=f"Round {i}/{total}", description="Whose flag is this?")
        embed.set_image(url=FLAG_URL.format(code=correct["code"]))
        view = _MPFlagView(player_ids, correct["name"], valid)
        await thread.send(content=scoreboard, embed=embed, view=view)
        try:
            await asyncio.wait_for(view.finished.wait(), timeout=MP_ROUND_TIMEOUT)
        except asyncio.TimeoutError:
            pass

        if view.winner_id is not None:
            scores[view.winner_id] += 1
            winner = players_by_id[view.winner_id]
            await thread.send(f"✅ **{winner.display_name}** got it! That was **{correct['name']}**.")
        else:
            await thread.send(f"⏱ No one got it. That was **{correct['name']}**.")

        if i < total:
            await asyncio.sleep(INTERMISSION)

    max_score = max(scores.values())
    winners = [players_by_id[pid] for pid, s in scores.items() if s == max_score]
    final = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
    if len(winners) == 1:
        await thread.send(f"🏆 **{winners[0].display_name}** wins!\n{final}")
    else:
        names = ", ".join(w.display_name for w in winners)
        await thread.send(f"🤝 Tie between **{names}**!\n{final}")
