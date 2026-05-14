import asyncio
import json
import random
from pathlib import Path

import discord

MAX_GUESSES = 6
GUESS_TIMEOUT = 180
WORD_LEN = 5
WORDS_PATH = Path(__file__).resolve().parent / "data" / "wordle_words.json"
MP_INACTIVITY_TIMEOUT = 600  # 10 minutes of no guesses ends the round

EMOJI = {"green": "🟩", "yellow": "🟨", "gray": "⬛"}


def _load_words() -> list[str]:
    with open(WORDS_PATH, encoding="utf-8") as f:
        return json.load(f)["words"]


def score(guess: str, answer: str) -> list[str]:
    """Wordle scoring with correct duplicate-letter handling."""
    result = ["gray"] * WORD_LEN
    remaining = list(answer)
    # First pass: exact-position matches (greens)
    for i in range(WORD_LEN):
        if guess[i] == answer[i]:
            result[i] = "green"
            remaining[i] = ""
    # Second pass: wrong-position matches (yellows)
    for i in range(WORD_LEN):
        if result[i] == "green":
            continue
        if guess[i] in remaining:
            result[i] = "yellow"
            remaining[remaining.index(guess[i])] = ""
    return result


def render(guess: str, statuses: list[str]) -> str:
    emojis = "".join(EMOJI[s] for s in statuses)
    letters = " ".join(c.upper() for c in guess)
    return f"`{letters}`\n{emojis}"


async def start(thread, user, bot):
    words = _load_words()
    word_set = set(words)
    answer = random.choice(words)
    history: list[tuple[str, list[str]]] = []

    await thread.send(
        f"**Wordle** — guess the {WORD_LEN}-letter word in **{MAX_GUESSES}** tries.\n"
        f"🟩 right letter, right spot  •  🟨 right letter, wrong spot  •  ⬛ not in word\n"
        f"Type a 5-letter word to guess."
    )

    def is_guess(msg):
        if msg.author.id != user.id or msg.channel.id != thread.id:
            return False
        c = msg.content.strip().lower()
        return len(c) == WORD_LEN and c.isalpha()

    attempt = 0
    while attempt < MAX_GUESSES:
        try:
            msg = await bot.wait_for("message", check=is_guess, timeout=GUESS_TIMEOUT)
        except asyncio.TimeoutError:
            await thread.send(f"Out of time! The word was **{answer.upper()}**.")
            return

        guess = msg.content.strip().lower()
        if guess not in word_set:
            await thread.send(f"`{guess.upper()}` isn't in my dictionary — try a different word.")
            continue

        attempt += 1
        statuses = score(guess, answer)
        history.append((guess, statuses))
        board = "\n\n".join(render(g, s) for g, s in history)

        if guess == answer:
            await thread.send(
                f"{board}\n\nYou got it in {attempt} {'try' if attempt == 1 else 'tries'}! 🎉"
            )
            return

        remaining = MAX_GUESSES - attempt
        if remaining == 0:
            await thread.send(f"{board}\n\nOut of guesses! The word was **{answer.upper()}**.")
            return
        await thread.send(
            f"{board}\n\n{remaining} {'guess' if remaining == 1 else 'guesses'} left."
        )


# -----------------------------------------------------------------------------
# Multiplayer race mode
# -----------------------------------------------------------------------------


class _GuessModal(discord.ui.Modal, title="Wordle guess"):
    def __init__(self, game: "_MPGame", player_id: int):
        super().__init__()
        self.game = game
        self.player_id = player_id
        self.guess_input = discord.ui.TextInput(
            label=f"5-letter word ({MAX_GUESSES - len(game.histories.get(player_id, []))} left)",
            min_length=WORD_LEN,
            max_length=WORD_LEN,
            placeholder="e.g. CRANE",
        )
        self.add_item(self.guess_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.game.handle_guess(interaction, self.player_id, self.guess_input.value)


class _MPView(discord.ui.View):
    """Public button that any player can click to open a private guess modal."""

    def __init__(self, game: "_MPGame"):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Make a guess", style=discord.ButtonStyle.primary, emoji="✏️")
    async def make_guess(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.game.open_modal(interaction)


class _MPGame:
    def __init__(self, thread, players, bot, words: list[str], answer: str):
        self.thread = thread
        self.players = {p.id: p for p in players}
        self.bot = bot
        self.word_set = set(words)
        self.answer = answer
        self.histories: dict[int, list[tuple[str, list[str]]]] = {p.id: [] for p in players}
        self.solved_by: int | None = None
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()

    async def open_modal(self, interaction: discord.Interaction):
        if interaction.user.id not in self.players:
            await interaction.response.send_message(
                "You're not in this game — join the lobby and start a new one.", ephemeral=True
            )
            return
        if self.finished.is_set():
            await interaction.response.send_message("This round is already over.", ephemeral=True)
            return
        used = len(self.histories[interaction.user.id])
        if used >= MAX_GUESSES:
            await interaction.response.send_message(
                f"You've used all {MAX_GUESSES} guesses.", ephemeral=True
            )
            return
        await interaction.response.send_modal(_GuessModal(self, interaction.user.id))

    async def handle_guess(self, interaction: discord.Interaction, player_id: int, raw: str):
        guess = raw.strip().lower()
        if len(guess) != WORD_LEN or not guess.isalpha():
            await interaction.response.send_message(
                "Guesses must be exactly 5 letters.", ephemeral=True
            )
            return
        if guess not in self.word_set:
            await interaction.response.send_message(
                f"`{guess.upper()}` isn't in my dictionary — try another word.", ephemeral=True
            )
            return

        async with self._lock:
            if self.finished.is_set():
                await interaction.response.send_message(
                    "Too late — someone already solved it.", ephemeral=True
                )
                return
            statuses = score(guess, self.answer)
            history = self.histories[player_id]
            history.append((guess, statuses))
            board = "\n\n".join(render(g, s) for g, s in history)
            remaining = MAX_GUESSES - len(history)
            player = self.players[player_id]

            if guess == self.answer:
                self.solved_by = player_id
                self.finished.set()
                await interaction.response.send_message(
                    f"{board}\n\n🎉 You solved it!", ephemeral=True
                )
                await self.thread.send(
                    f"🏆 **{player.display_name}** solved it in **{len(history)}** "
                    f"{'guess' if len(history) == 1 else 'guesses'}! The word was **{self.answer.upper()}**."
                )
                return

            await interaction.response.send_message(
                f"{board}\n\n{remaining} {'guess' if remaining == 1 else 'guesses'} left.",
                ephemeral=True,
            )

            if remaining == 0:
                await self.thread.send(
                    f"💀 **{player.display_name}** is out of guesses."
                )
                # Check if everyone is out
                if all(len(h) >= MAX_GUESSES for h in self.histories.values()):
                    self.finished.set()
                    await self.thread.send(
                        f"All players exhausted their guesses. The word was **{self.answer.upper()}**."
                    )
                    return
            else:
                await self.thread.send(
                    f"✏️ **{player.display_name}** made guess #{len(history)} ({remaining} left)."
                )


async def start_multi(thread, players, bot):
    words = _load_words()
    answer = random.choice(words)
    game = _MPGame(thread, players, bot, words, answer)

    names = ", ".join(p.display_name for p in players)
    view = _MPView(game)
    await thread.send(
        f"**Wordle — Race Mode**\nAll {len(players)} players are solving the same word "
        f"({WORD_LEN} letters, **{MAX_GUESSES}** guesses each).\n"
        f"Click the button below to guess privately. First to solve wins!\n"
        f"Players: {names}",
        view=view,
    )

    try:
        await asyncio.wait_for(game.finished.wait(), timeout=MP_INACTIVITY_TIMEOUT)
    except asyncio.TimeoutError:
        await thread.send(
            f"⏱ Round timed out. The word was **{answer.upper()}**."
        )
    finally:
        view.stop()
