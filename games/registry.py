from games import guess_the_number, higher_or_lower, rock_paper_scissors, hangman

# Maps the label shown in the game-selection dropdown to its start coroutine.
# Each start function takes (thread, user, bot) and runs the game in the thread.
GAMES = {
    "Guess the Number!": guess_the_number.start,
    "Higher or Lower?": higher_or_lower.start,
    "Rock Paper Scissors": rock_paper_scissors.start,
    "Hangman": hangman.start,
}


async def start_game(name, thread, user, bot):
    handler = GAMES.get(name)
    if handler is None:
        await thread.send(f"`{name}` isn't implemented yet — coming soon!")
        return
    await handler(thread, user, bot)
