from games import (
    cryptograms,
    guess_the_flag,
    guess_the_number,
    hangman,
    higher_or_lower,
    minesweeper,
    rock_paper_scissors,
    sports_trivia,
    sudoku,
    who_sent_the_message,
    whos_that_pokemon,
    word_impostor,
    wordle,
)

# Each entry has:
#   "single" -> async start(thread, user, bot)
#   "multi"  -> async start_multi(thread, players, bot)  (optional)
# If "multi" is None, the game falls back to single-player or refuses to start in MP mode.
def _mp(mod):
    return getattr(mod, "start_multi", None)


GAMES: dict[str, dict] = {
    "Guess the Number!": {"single": guess_the_number.start, "multi": _mp(guess_the_number)},
    "Higher or Lower?": {"single": higher_or_lower.start, "multi": _mp(higher_or_lower)},
    "Rock Paper Scissors": {"single": rock_paper_scissors.start, "multi": _mp(rock_paper_scissors)},
    "Hangman": {"single": hangman.start, "multi": _mp(hangman)},
    "Wordle": {"single": wordle.start, "multi": _mp(wordle)},
    "Cryptograms": {"single": cryptograms.start, "multi": _mp(cryptograms)},
    "Sports Trivia": {"single": sports_trivia.start, "multi": _mp(sports_trivia)},
    "Guess the Flag": {"single": guess_the_flag.start, "multi": _mp(guess_the_flag)},
    "Who's That Pokemon?": {"single": whos_that_pokemon.start, "multi": _mp(whos_that_pokemon)},
    "Who Sent the Message?": {"single": who_sent_the_message.start, "multi": _mp(who_sent_the_message)},
    "Minesweeper": {"single": minesweeper.start, "multi": None},  # MP coming later
    "Word Impostor": {"single": word_impostor.start, "multi": word_impostor.start_multi},  # MP only
    "Sudoku": {"single": sudoku.start, "multi": sudoku.start_multi},  # solo or race
}


async def start_game(name: str, thread, host, bot, mode: str = "Single Player", players=None):
    entry = GAMES.get(name)
    if entry is None:
        await thread.send(f"`{name}` isn't implemented yet — coming soon!")
        return

    if mode == "Multiplayer":
        handler = entry.get("multi")
        if handler is None:
            await thread.send(
                f"**{name}** doesn't have a multiplayer mode yet — running single-player instead."
            )
            await entry["single"](thread, host, bot)
            return
        await handler(thread, players or [host], bot)
        return

    await entry["single"](thread, host, bot)
