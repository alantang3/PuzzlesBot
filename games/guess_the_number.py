import asyncio
import random

import discord

LOW = 1
HIGH = 100
MAX_TRIES = 7      # intentionally hard: hot/cold-only has no guaranteed strategy
TURN_TIMEOUT = 60  # seconds to make a guess before forfeit

# Multiplayer: the picker chooses the secret AND how many guesses to grant.
MP_LOW = 1
MP_HIGH = 1000          # picker may choose any secret in this range
MP_MIN_GUESSES = 1      # bounds on the guess budget the picker can set
MP_MAX_GUESSES = 50
MP_TURN_TIMEOUT = 90
MP_SETUP_TIMEOUT = 120

_DNF = 10**9  # sentinel attempt count for a failed/forfeited round


def _temperature(distance: int, span: int, prev_distance: int | None) -> str:
    """No-direction closeness feedback: a symmetric absolute hot/cold band
    plus warmer/cooler vs. the previous guess. The band tightens as you close
    in; warmer/cooler is computed independently of the band, so it still fires
    when you move within the same band. No higher/lower bit, so binary search
    doesn't apply."""
    f = distance / span if span else 0.0
    if f <= 0.03:
        band = "🔥🔥 boiling"
    elif f <= 0.10:
        band = "🔥 hot"
    elif f <= 0.20:
        band = "🙂 warm"
    elif f <= 0.35:
        band = "🌬️ cool"
    elif f <= 0.55:
        band = "❄️ cold"
    else:
        band = "🧊 freezing"
    if prev_distance is None:
        return band
    if distance < prev_distance:
        return f"{band} — getting **warmer**"
    if distance > prev_distance:
        return f"{band} — getting **cooler**"
    return f"{band} — same temperature"


async def start(thread, user, bot):
    secret = random.randint(LOW, HIGH)
    span = HIGH - LOW
    await thread.send(
        f"I'm thinking of a number between **{LOW}** and **{HIGH}**. "
        f"No higher/lower hints — I'll only tell you how **hot or cold** you are, "
        f"and whether you're getting **warmer** or **cooler**. "
        f"You have **{MAX_TRIES}** tries. Type your guess!"
    )

    def is_guess(msg):
        return (
            msg.author.id == user.id
            and msg.channel.id == thread.id
            and msg.content.strip().lstrip("-").isdigit()
        )

    prev_distance: int | None = None
    for attempt in range(1, MAX_TRIES + 1):
        try:
            msg = await bot.wait_for("message", check=is_guess, timeout=TURN_TIMEOUT)
        except asyncio.TimeoutError:
            await thread.send(f"Out of time! The number was **{secret}**.")
            return

        guess = int(msg.content.strip())
        remaining = MAX_TRIES - attempt

        if guess == secret:
            await thread.send(
                f"🎯 You got it in {attempt} {'try' if attempt == 1 else 'tries'}! "
                f"The number was **{secret}**."
            )
            return
        if remaining == 0:
            break
        distance = abs(guess - secret)
        temp = _temperature(distance, span, prev_distance)
        prev_distance = distance
        await thread.send(f"{temp}. {remaining} {'try' if remaining == 1 else 'tries'} left.")

    await thread.send(f"Out of tries! The number was **{secret}**.")


# -----------------------------------------------------------------------------
# Multiplayer (2-player take-turns) — fewer attempts wins
# -----------------------------------------------------------------------------


class _SetupModal(discord.ui.Modal, title="Set up your round"):
    def __init__(self):
        super().__init__()
        self.number_input = discord.ui.TextInput(
            label=f"Secret number ({MP_LOW}-{MP_HIGH})",
            min_length=1,
            max_length=len(str(MP_HIGH)),
            placeholder="e.g. 742",
        )
        self.guesses_input = discord.ui.TextInput(
            label=f"Guesses to allow ({MP_MIN_GUESSES}-{MP_MAX_GUESSES})",
            min_length=1,
            max_length=len(str(MP_MAX_GUESSES)),
            placeholder="e.g. 12",
        )
        self.add_item(self.number_input)
        self.add_item(self.guesses_input)
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()

    async def on_submit(self, interaction: discord.Interaction):
        raw_n = self.number_input.value.strip()
        raw_g = self.guesses_input.value.strip()
        if not raw_n.isdigit() or not (MP_LOW <= int(raw_n) <= MP_HIGH):
            await interaction.response.send_message(
                f"Secret number must be an integer between {MP_LOW} and {MP_HIGH}.",
                ephemeral=True,
            )
            return
        if not raw_g.isdigit() or not (MP_MIN_GUESSES <= int(raw_g) <= MP_MAX_GUESSES):
            await interaction.response.send_message(
                f"Guesses must be an integer between {MP_MIN_GUESSES} and {MP_MAX_GUESSES}.",
                ephemeral=True,
            )
            return
        self.future.set_result((int(raw_n), int(raw_g)))
        await interaction.response.send_message(
            "Locked in. The other player will start guessing.", ephemeral=True
        )


class _SetupView(discord.ui.View):
    def __init__(self, picker_id: int):
        super().__init__(timeout=MP_SETUP_TIMEOUT)
        self.picker_id = picker_id
        self.modal: _SetupModal | None = None

    @discord.ui.button(label="Set Up Round", style=discord.ButtonStyle.primary, emoji="🔢")
    async def set_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id != self.picker_id:
            await interaction.response.send_message(
                "👀 Only the picker can set this round up — you're spectating.",
                ephemeral=True,
            )
            return
        if self.modal is not None and self.modal.future.done():
            await interaction.response.send_message("Already set.", ephemeral=True)
            return
        self.modal = _SetupModal()
        await interaction.response.send_modal(self.modal)


async def _run_round(thread, bot, picker, guesser) -> int:
    """One round: picker sets the number + guess budget, guesser guesses with
    warmer/cooler feedback. Returns the attempt count, or _DNF if failed."""
    view = _SetupView(picker.id)
    setup_msg = await thread.send(
        f"**{picker.display_name}**, click below to set a secret number "
        f"(**{MP_LOW}-{MP_HIGH}**) and how many guesses **{guesser.display_name}** gets.",
        view=view,
    )

    deadline = asyncio.get_event_loop().time() + MP_SETUP_TIMEOUT
    setup = None
    while setup is None:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            await thread.send(f"⏱ {picker.display_name} didn't set up in time. Round skipped.")
            return _DNF
        if view.modal is not None:
            try:
                setup = await asyncio.wait_for(view.modal.future, timeout=remaining)
            except asyncio.TimeoutError:
                continue
        else:
            await asyncio.sleep(0.5)

    secret, guess_limit = setup
    try:
        await setup_msg.edit(view=None)
    except discord.HTTPException:
        pass

    span = MP_HIGH - MP_LOW
    await thread.send(
        f"Number locked in! **{guesser.display_name}**, start guessing a number "
        f"**{MP_LOW}–{MP_HIGH}**. No higher/lower — only **hot/cold** and "
        f"**warmer/cooler**. You have **{guess_limit}** "
        f"{'guess' if guess_limit == 1 else 'guesses'}."
    )

    def is_guess(msg):
        return (
            msg.author.id == guesser.id
            and msg.channel.id == thread.id
            and msg.content.strip().lstrip("-").isdigit()
        )

    prev_distance: int | None = None
    for attempt in range(1, guess_limit + 1):
        try:
            msg = await bot.wait_for("message", check=is_guess, timeout=MP_TURN_TIMEOUT)
        except asyncio.TimeoutError:
            await thread.send(f"⏱ Out of time. The number was **{secret}**.")
            return _DNF

        guess = int(msg.content.strip())
        remaining = guess_limit - attempt

        if guess == secret:
            await thread.send(
                f"🎯 **{guesser.display_name}** got it in **{attempt}** "
                f"{'try' if attempt == 1 else 'tries'}!"
            )
            return attempt
        if remaining == 0:
            break
        distance = abs(guess - secret)
        temp = _temperature(distance, span, prev_distance)
        prev_distance = distance
        await thread.send(
            f"{temp}. {remaining} {'guess' if remaining == 1 else 'guesses'} left."
        )

    await thread.send(f"💀 **{guesser.display_name}** ran out. The number was **{secret}**.")
    return _DNF


def _fmt_attempts(n: int) -> str:
    return "DNF" if n >= _DNF else str(n)


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
        f"{a.display_name} vs {b.display_name}. Each takes a turn picking a secret "
        f"number (**{MP_LOW}-{MP_HIGH}**) and how many guesses the other gets; "
        f"feedback is **hot/cold + warmer/cooler** only. **Fewer attempts wins.**"
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
        f"• {b.display_name} took **{_fmt_attempts(a_attempts)}** attempts\n"
        f"• {a.display_name} took **{_fmt_attempts(b_attempts)}** attempts"
    )
    if a_attempts < b_attempts:
        await thread.send(f"🏆 **{b.display_name}** wins! (fewer guesses)")
    elif b_attempts < a_attempts:
        await thread.send(f"🏆 **{a.display_name}** wins! (fewer guesses)")
    else:
        await thread.send("🤝 Tie!")
