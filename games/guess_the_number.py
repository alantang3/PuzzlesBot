import asyncio
import random

LOW = 1
HIGH = 100
MAX_TRIES = 7
TURN_TIMEOUT = 60  # seconds to make a guess before forfeit


async def start(thread, user, bot):
    secret = random.randint(LOW, HIGH)
    await thread.send(
        f"I'm thinking of a number between **{LOW}** and **{HIGH}**. "
        f"You have **{MAX_TRIES}** tries. Type your guess!"
    )

    def is_guess(msg):
        return (
            msg.author.id == user.id
            and msg.channel.id == thread.id
            and msg.content.strip().lstrip("-").isdigit()
        )

    for attempt in range(1, MAX_TRIES + 1):
        try:
            msg = await bot.wait_for("message", check=is_guess, timeout=TURN_TIMEOUT)
        except asyncio.TimeoutError:
            await thread.send(
                f"Out of time! The number was **{secret}**."
            )
            return

        guess = int(msg.content.strip())
        remaining = MAX_TRIES - attempt

        if guess == secret:
            await thread.send(
                f"You got it in {attempt} {'try' if attempt == 1 else 'tries'}! "
                f"The number was **{secret}**."
            )
            return
        if remaining == 0:
            break
        hint = "higher" if guess < secret else "lower"
        await thread.send(f"Try **{hint}**. {remaining} {'try' if remaining == 1 else 'tries'} left.")

    await thread.send(f"Out of tries! The number was **{secret}**.")
