import asyncio
import random

MAX_WRONG = 6
GUESS_TIMEOUT = 90

WORDS = [
    "python", "discord", "hangman", "puzzle", "keyboard", "rainbow",
    "elephant", "guitar", "mountain", "volcano", "umbrella", "library",
    "javascript", "octopus", "telescope", "blueprint", "harmony",
    "festival", "bicycle", "wizard", "compass", "treasure", "diamond",
    "penguin", "horizon", "shadow", "thunder", "crystal", "voyage",
]

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
    return " ".join(c if c in guessed else "_" for c in word)


async def start(thread, user, bot):
    word = random.choice(WORDS).lower()
    guessed = set()
    wrong = set()

    await thread.send(
        f"Guess the word, one letter at a time. You get **{MAX_WRONG}** wrong guesses.\n"
        f"```{STAGES[0]}```\nWord: `{render(word, guessed)}`"
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
