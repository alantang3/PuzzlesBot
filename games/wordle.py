import asyncio
import json
import random
from pathlib import Path

MAX_GUESSES = 6
GUESS_TIMEOUT = 180
WORD_LEN = 5
WORDS_PATH = Path(__file__).resolve().parent / "data" / "wordle_words.json"

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
