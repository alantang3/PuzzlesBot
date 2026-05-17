import asyncio
import json
import random
from pathlib import Path

import discord

MAX_WRONG = 6
GUESS_TIMEOUT = 90
WORDS_PATH = Path(__file__).resolve().parent / "data" / "hangman_words.json"
MP_SETUP_TIMEOUT = 180
MP_GUESS_TIMEOUT = 90


def _load_words() -> dict[str, list[str]]:
    with open(WORDS_PATH, encoding="utf-8") as f:
        return json.load(f)["categories"]

WORDS = _load_words()

STAGES = [
    """
     +---+
         |
         |
         |
        ===""",
    """
     +---+
     O   |
         |
         |
        ===""",
    """
     +---+
     O   |
     |   |
         |
        ===""",
    """
     +---+
     O   |
    /|   |
         |
        ===""",
    """
     +---+
     O   |
    /|\\  |
         |
        ===""",
    """
     +---+
     O   |
    /|\\  |
    /    |
        ===""",
    """
     +---+
     O   |
    /|\\  |
    / \\  |
        ===""",
]


def render(word, guessed):
    # Non-letters (spaces, punctuation, digits) are always shown; only
    # alphabetic characters have to be guessed. Single words are unaffected.
    return " ".join(
        c if (not c.isalpha() or c in guessed) else "_" for c in word
    )


async def start(thread, user, bot):
    category = random.choice(list(WORDS.keys()))
    word = random.choice(WORDS[category]).lower()
    guessed = set()
    wrong = set()

    await thread.send(
        f"Category: **{category}**\n"
        f"Guess the word, one letter at a time. You get **{MAX_WRONG}** wrong guesses.\n"
        f"```{STAGES[0]}```\nWord: `{render(word, guessed)}`  ({len(word)} letters)"
    )

    def is_guess(msg):
        if msg.author.id != user.id or msg.channel.id != thread.id:
            return False
        content = msg.content.strip().lower()
        return len(content) == 1 and content.isalpha()

    while True:
        try:
            msg = await bot.wait_for("message", check=is_guess, timeout=GUESS_TIMEOUT)
        except asyncio.TimeoutError:
            await thread.send(f"Out of time! The word was **{word}**.")
            return

        letter = msg.content.strip().lower()
        if letter in guessed or letter in wrong:
            await thread.send(f"You already tried `{letter}`.")
            continue

        if letter in word:
            guessed.add(letter)
            if all(c in guessed for c in word):
                await thread.send(
                    f"```{STAGES[len(wrong)]}```\nYou win! The word was **{word}**. 🎉"
                )
                return
            await thread.send(
                f"Yes — `{letter}` is in the word.\n"
                f"```{STAGES[len(wrong)]}```\nWord: `{render(word, guessed)}`  "
                f"Wrong: `{', '.join(sorted(wrong)) or '—'}`"
            )
        else:
            wrong.add(letter)
            if len(wrong) >= MAX_WRONG:
                await thread.send(
                    f"```{STAGES[MAX_WRONG]}```\nYou lose! The word was **{word}**."
                )
                return
            await thread.send(
                f"Nope — `{letter}` isn't in the word.\n"
                f"```{STAGES[len(wrong)]}```\nWord: `{render(word, guessed)}`  "
                f"Wrong: `{', '.join(sorted(wrong))}`"
            )


# -----------------------------------------------------------------------------
# Multiplayer (2-player take-turns) — fewer wrong guesses wins
# -----------------------------------------------------------------------------

MIN_WORD_LEN = 3
# MP allows full phrases/sentences, not just single words. Deliberate cap —
# surfaced, not silently chosen; adjust freely.
MAX_PHRASE_LEN = 100


MAX_CATEGORY_LEN = 40


class _SetWordModal(discord.ui.Modal, title="Pick your word"):
    def __init__(self, view: "_SetWordView"):
        super().__init__()
        self.view_ref = view
        self.category_input = discord.ui.TextInput(
            label="Category / hint",
            min_length=2,
            max_length=MAX_CATEGORY_LEN,
            placeholder="e.g. Animals, Movies, Food...",
        )
        self.word_input = discord.ui.TextInput(
            label="Word or phrase",
            style=discord.TextStyle.paragraph,
            min_length=MIN_WORD_LEN,
            max_length=MAX_PHRASE_LEN,
            placeholder="e.g. the quick brown fox",
        )
        self.add_item(self.category_input)
        self.add_item(self.word_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Collapse whitespace/newlines; allow spaces & punctuation, but there
        # must be at least one letter to actually guess.
        raw = " ".join(self.word_input.value.split()).lower()
        category = self.category_input.value.strip()
        if not any(c.isalpha() for c in raw):
            await interaction.response.send_message(
                "Needs at least one letter to guess.", ephemeral=True
            )
            return
        if self.view_ref.done.is_set():
            await interaction.response.send_message("Already set.", ephemeral=True)
            return
        letters = sum(1 for c in raw if c.isalpha())
        # Result lives on the VIEW, not this modal — so a re-opened/replaced
        # modal still resolves the same waiter the round is listening on.
        self.view_ref.result = (raw, category)
        self.view_ref.done.set()
        self.view_ref.stop()
        await interaction.response.send_message(
            f"Locked in (**{letters}** letters to guess, category **{category}**). "
            f"The other player will start guessing.",
            ephemeral=True,
        )


class _SetWordView(discord.ui.View):
    def __init__(self, picker_id: int):
        super().__init__(timeout=MP_SETUP_TIMEOUT)
        self.picker_id = picker_id
        self.result: tuple[str, str] | None = None
        self.done = asyncio.Event()

    @discord.ui.button(label="Set Word", style=discord.ButtonStyle.primary, emoji="✏️")
    async def set_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id != self.picker_id:
            await interaction.response.send_message(
                "👀 Only the picker can set the word — you're spectating.", ephemeral=True
            )
            return
        if self.done.is_set():
            await interaction.response.send_message("Already set.", ephemeral=True)
            return
        await interaction.response.send_modal(_SetWordModal(self))


async def _run_mp_round(thread, bot, picker, guesser) -> int:
    """One round: picker sets word, guesser guesses letters. Returns wrong-guess count (or MAX_WRONG+1 if failed)."""
    view = _SetWordView(picker.id)
    setup_msg = await thread.send(
        f"**{picker.display_name}**, click below and type your word in the modal "
        f"(it stays hidden from {guesser.display_name}).",
        view=view,
    )

    try:
        await asyncio.wait_for(view.done.wait(), timeout=MP_SETUP_TIMEOUT)
    except asyncio.TimeoutError:
        await thread.send(f"⏱ {picker.display_name} didn't pick a word. Round skipped.")
        try:
            await setup_msg.edit(view=None)
        except discord.HTTPException:
            pass
        return MAX_WRONG + 1

    word, category = view.result

    try:
        await setup_msg.edit(view=None)
    except discord.HTTPException:
        pass

    guessed: set[str] = set()
    wrong: set[str] = set()

    letters_to_guess = sum(1 for c in word if c.isalpha())
    await thread.send(
        f"Category: **{category}**\n"
        f"**{guesser.display_name}**, guess one letter at a time — "
        f"**{letters_to_guess}** letters (spaces & punctuation are shown).\n"
        f"```{STAGES[0]}```\nWord: `{render(word, guessed)}`"
    )

    def is_guess(msg):
        if msg.author.id != guesser.id or msg.channel.id != thread.id:
            return False
        c = msg.content.strip().lower()
        return len(c) == 1 and c.isalpha()

    while True:
        try:
            msg = await bot.wait_for("message", check=is_guess, timeout=MP_GUESS_TIMEOUT)
        except asyncio.TimeoutError:
            await thread.send(f"⏱ Out of time. The word was **{word}**.")
            return MAX_WRONG + 1

        letter = msg.content.strip().lower()
        if letter in guessed or letter in wrong:
            await thread.send(f"Already tried `{letter}`.")
            continue

        if letter in word:
            guessed.add(letter)
            if all(c in guessed for c in word if c.isalpha()):
                await thread.send(
                    f"```{STAGES[len(wrong)]}```\n🎯 **{guesser.display_name}** got it with "
                    f"**{len(wrong)}** wrong {'guess' if len(wrong) == 1 else 'guesses'}! Word: **{word}**."
                )
                return len(wrong)
            await thread.send(
                f"✓ `{letter}` is in. Category: **{category}**\n"
                f"```{STAGES[len(wrong)]}```\nWord: `{render(word, guessed)}`  "
                f"Wrong: `{', '.join(sorted(wrong)) or '—'}`"
            )
        else:
            wrong.add(letter)
            if len(wrong) >= MAX_WRONG:
                await thread.send(
                    f"```{STAGES[MAX_WRONG]}```\n💀 **{guesser.display_name}** lost! The word was **{word}**."
                )
                return MAX_WRONG + 1
            await thread.send(
                f"✗ `{letter}` isn't in. Category: **{category}**\n"
                f"```{STAGES[len(wrong)]}```\nWord: `{render(word, guessed)}`  "
                f"Wrong: `{', '.join(sorted(wrong))}`"
            )


async def start_multi(thread, players, bot):
    if len(players) < 2:
        await thread.send("Hangman MP needs 2 players. Falling back to single-player.")
        await start(thread, players[0], bot)
        return
    active = players[:2]
    spectators = players[2:]
    a, b = active

    header = (
        f"**Hangman — Multiplayer**\n"
        f"{a.display_name} vs {b.display_name}. Each picks a word; the other tries to guess it. "
        f"**Fewer wrong letters wins.** Lose = {MAX_WRONG + 1} (treated as DNF for scoring)."
    )
    if spectators:
        names = ", ".join(s.display_name for s in spectators)
        header += f"\n👀 Spectating: {names}"
    await thread.send(header)

    await thread.send(f"**Round 1** — {a.display_name} picks, {b.display_name} guesses.")
    a_wrong = await _run_mp_round(thread, bot, picker=a, guesser=b)

    await thread.send(f"**Round 2** — {b.display_name} picks, {a.display_name} guesses.")
    b_wrong = await _run_mp_round(thread, bot, picker=b, guesser=a)

    def fmt(n: int) -> str:
        return f"{n} wrong" if n <= MAX_WRONG else "DNF"

    await thread.send(
        f"**Results:**\n"
        f"• {b.display_name}: **{fmt(a_wrong)}**\n"
        f"• {a.display_name}: **{fmt(b_wrong)}**"
    )
    if a_wrong < b_wrong:
        await thread.send(f"🏆 **{b.display_name}** wins!")
    elif b_wrong < a_wrong:
        await thread.send(f"🏆 **{a.display_name}** wins!")
    else:
        await thread.send("🤝 Tie!")