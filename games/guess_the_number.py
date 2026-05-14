import asyncio
import random

import discord

LOW = 1
HIGH = 100
MAX_TRIES = 7
TURN_TIMEOUT = 60  # seconds to make a guess before forfeit
MP_MAX_TRIES = 15  # higher cap for MP since attempt count is the scoring metric
MP_TURN_TIMEOUT = 90
MP_SETUP_TIMEOUT = 120


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


# -----------------------------------------------------------------------------
# Multiplayer (2-player take-turns) — fewer attempts wins
# -----------------------------------------------------------------------------


class _SetNumberModal(discord.ui.Modal, title="Set the secret number"):
    def __init__(self):
        super().__init__()
        self.number_input = discord.ui.TextInput(
            label=f"Pick a number {LOW}-{HIGH}",
            min_length=1,
            max_length=3,
            placeholder=f"e.g. 42",
        )
        self.add_item(self.number_input)
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.number_input.value.strip()
        if not raw.isdigit() or not (LOW <= int(raw) <= HIGH):
            await interaction.response.send_message(
                f"Must be an integer between {LOW} and {HIGH}.", ephemeral=True
            )
            return
        self.future.set_result(int(raw))
        await interaction.response.send_message(
            f"Secret number locked in. The other player will start guessing.", ephemeral=True
        )


class _SetNumberView(discord.ui.View):
    def __init__(self, picker_id: int):
        super().__init__(timeout=MP_SETUP_TIMEOUT)
        self.picker_id = picker_id
        self.modal: _SetNumberModal | None = None

    @discord.ui.button(label="Set Number", style=discord.ButtonStyle.primary, emoji="🔢")
    async def set_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id != self.picker_id:
            await interaction.response.send_message(
                "👀 Only the picker can set the number — you're spectating.", ephemeral=True
            )
            return
        if self.modal is not None and self.modal.future.done():
            await interaction.response.send_message("Already set.", ephemeral=True)
            return
        self.modal = _SetNumberModal()
        await interaction.response.send_modal(self.modal)


async def _run_round(thread, bot, picker, guesser) -> int:
    """One round: picker sets number, guesser guesses. Returns attempt count (or MP_MAX_TRIES+1 if failed)."""
    view = _SetNumberView(picker.id)
    setup_msg = await thread.send(
        f"**{picker.display_name}**, click below to set a secret number between **{LOW}** and **{HIGH}**.\n"
        f"**{guesser.display_name}** will then guess it.",
        view=view,
    )
    # Wait until the picker submits
    deadline = asyncio.get_event_loop().time() + MP_SETUP_TIMEOUT
    secret = None
    while secret is None:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            await thread.send(f"⏱ {picker.display_name} didn't set a number in time. Round skipped.")
            return MP_MAX_TRIES + 1
        if view.modal is not None:
            try:
                secret = await asyncio.wait_for(view.modal.future, timeout=remaining)
            except asyncio.TimeoutError:
                continue
        else:
            await asyncio.sleep(0.5)

    try:
        await setup_msg.edit(view=None)
    except discord.HTTPException:
        pass

    await thread.send(
        f"Number locked in! **{guesser.display_name}**, start guessing — type a number {LOW}–{HIGH}. "
        f"You have **{MP_MAX_TRIES}** tries."
    )

    def is_guess(msg):
        return (
            msg.author.id == guesser.id
            and msg.channel.id == thread.id
            and msg.content.strip().lstrip("-").isdigit()
        )

    for attempt in range(1, MP_MAX_TRIES + 1):
        try:
            msg = await bot.wait_for("message", check=is_guess, timeout=MP_TURN_TIMEOUT)
        except asyncio.TimeoutError:
            await thread.send(f"⏱ Out of time. The number was **{secret}**.")
            return MP_MAX_TRIES + 1

        guess = int(msg.content.strip())
        remaining = MP_MAX_TRIES - attempt

        if guess == secret:
            await thread.send(
                f"🎯 **{guesser.display_name}** got it in **{attempt}** "
                f"{'try' if attempt == 1 else 'tries'}!"
            )
            return attempt
        if remaining == 0:
            break
        hint = "higher" if guess < secret else "lower"
        await thread.send(f"Try **{hint}**. {remaining} {'try' if remaining == 1 else 'tries'} left.")

    await thread.send(f"💀 **{guesser.display_name}** ran out. The number was **{secret}**.")
    return MP_MAX_TRIES + 1


async def start_multi(thread, players, bot):
    if len(players) < 2:
        await thread.send("Guess the Number MP needs 2 players. Falling back to single-player.")
        await start(thread, players[0], bot)
        return
    active = players[:2]
    spectators = players[2:]
    a, b = active

    header = (
        f"**Guess the Number — Multiplayer**\n"
        f"{a.display_name} vs {b.display_name}. Each takes a turn setting a secret number; "
        f"the other guesses. **Fewer attempts wins.**"
    )
    if spectators:
        names = ", ".join(s.display_name for s in spectators)
        header += f"\n👀 Spectating: {names}"
    await thread.send(header)

    await thread.send(f"**Round 1** — {a.display_name} sets, {b.display_name} guesses.")
    a_attempts = await _run_round(thread, bot, picker=a, guesser=b)

    await thread.send(f"**Round 2** — {b.display_name} sets, {a.display_name} guesses.")
    b_attempts = await _run_round(thread, bot, picker=b, guesser=a)

    await thread.send(
        f"**Results:**\n"
        f"• {b.display_name} took **{a_attempts if a_attempts <= MP_MAX_TRIES else 'DNF'}** attempts\n"
        f"• {a.display_name} took **{b_attempts if b_attempts <= MP_MAX_TRIES else 'DNF'}** attempts"
    )
    if a_attempts < b_attempts:
        await thread.send(f"🏆 **{b.display_name}** wins! (fewer guesses)")
    elif b_attempts < a_attempts:
        await thread.send(f"🏆 **{a.display_name}** wins! (fewer guesses)")
    else:
        await thread.send("🤝 Tie!")