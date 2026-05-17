import asyncio
import datetime
import random
import re

import discord

ROUNDS = 10
ROUND_TIMEOUT = 45
INTERMISSION = 2.5
MAX_GUESSES = 2
MIN_LEN = 15
MAX_LEN = 500
# Sample messages from a fixed number of random "markers". Each marker is a
# random readable channel + a random point in that channel's history. Cost is
# the marker count, NOT markers x channels, so it stays flat on big servers
# while still pulling messages randomly across channels and time.
HISTORY_WINDOW = 100   # max messages per marker (1 API page = 1 request); fewer is fine
POOL_MARKERS = 5       # total history reads per game, regardless of server size
READ_CONCURRENCY = 8   # max history reads in flight at once
# Not a server-size gate: with only 1 distinct sender "guess who sent it" has
# exactly one answer every round, which isn't a game. 2 is the true minimum.
MIN_AUTHORS = 2


def _normalize(text: str | None) -> str:
    """Lowercase + strip non-alphanumeric, so 'Alan ✨' and 'alan' both reduce to 'alan'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


async def _build_pool(guild: discord.Guild, exclude_channel_ids: set[int]) -> list[discord.Message]:
    """Collect eligible messages from POOL_MARKERS random markers. Each marker
    is a random readable channel at a random point in its history, read for up
    to HISTORY_WINDOW messages (fewer is fine — short/sparse spots just
    contribute less). Total cost is the marker count, independent of how many
    channels the server has, while staying random across channels and time."""
    now = discord.utils.utcnow()

    channels: list[discord.TextChannel] = []
    for ch in guild.text_channels:
        if ch.id in exclude_channel_ids:
            continue
        perms = ch.permissions_for(guild.me)
        if perms.read_message_history and perms.view_channel:
            channels.append(ch)
    if not channels:
        return []

    sem = asyncio.Semaphore(READ_CONCURRENCY)

    async def _read_marker() -> list[discord.Message]:
        channel = random.choice(channels)
        created = channel.created_at or now
        span = (now - created).total_seconds()
        before: datetime.datetime | None = None
        if span > 0:
            before = created + datetime.timedelta(seconds=random.uniform(0, span))
        out: list[discord.Message] = []
        async with sem:
            try:
                async for msg in channel.history(limit=HISTORY_WINDOW, before=before):
                    if msg.author.bot or msg.is_system():
                        continue
                    content = (msg.content or "").strip()
                    if not (MIN_LEN <= len(content) <= MAX_LEN):
                        continue
                    if content.startswith(("http://", "https://", "/", "!")):
                        continue
                    out.append(msg)
            except (discord.Forbidden, discord.HTTPException):
                pass
        return out

    results = await asyncio.gather(*[_read_marker() for _ in range(POOL_MARKERS)])

    pool: list[discord.Message] = []
    seen_ids: set[int] = set()
    for batch in results:
        for msg in batch:
            if msg.id in seen_ids:
                continue
            seen_ids.add(msg.id)
            pool.append(msg)
    return pool


def _valid_names(member: discord.Member) -> set[str]:
    """Names the player can type to identify this member."""
    candidates = {member.display_name, member.name}
    global_name = getattr(member, "global_name", None)
    if global_name:
        candidates.add(global_name)
    return {n for n in (_normalize(c) for c in candidates) if n}


async def start(thread, user, bot):
    guild: discord.Guild | None = getattr(thread, "guild", None)
    if guild is None:
        await thread.send("This game needs to be played inside a server.")
        return

    notice = await thread.send("Collecting recent server messages…")
    pool = await _build_pool(guild, exclude_channel_ids={thread.id})

    if not pool:
        await notice.edit(
            content="I couldn't find any eligible messages. Either the server is too quiet "
                    "or I don't have permission to read message history in its channels."
        )
        return

    authors_by_id: dict[int, discord.Member] = {}
    for msg in pool:
        if isinstance(msg.author, discord.Member):
            authors_by_id[msg.author.id] = msg.author

    if len(authors_by_id) < MIN_AUTHORS:
        await notice.edit(
            content="I need messages from at least two different people to make this "
                    "a guessing game, but the chat I sampled is too quiet right now. "
                    "Try again once there's been a bit more conversation."
        )
        return

    rounds_to_play = min(ROUNDS, len(pool))
    chosen_messages = random.sample(pool, rounds_to_play)
    score = 0

    await notice.edit(
        content=(
            f"**Who Sent the Message?** — {rounds_to_play} rounds. "
            f"Type the sender's **server name** (display name or username — case and special characters don't matter). "
            f"You get **{MAX_GUESSES}** guesses per round, **{ROUND_TIMEOUT}s** each."
        )
    )

    def is_guess(msg):
        return (
            msg.author.id == user.id
            and msg.channel.id == thread.id
            and bool(msg.content.strip())
        )

    for i, msg in enumerate(chosen_messages, 1):
        correct = msg.author
        valid = _valid_names(correct) if isinstance(correct, discord.Member) else {_normalize(correct.name)}
        valid.discard("")

        content = msg.content
        if len(content) > 1000:
            content = content[:1000] + "…"

        await thread.send(f"**Round {i}/{rounds_to_play}** — Who sent this?\n> {content}")

        for attempt in range(1, MAX_GUESSES + 1):
            try:
                guess_msg = await bot.wait_for("message", check=is_guess, timeout=ROUND_TIMEOUT)
            except asyncio.TimeoutError:
                await thread.send(f"⏱ Out of time. It was **{correct.display_name}**. Score: **{score}/{i}**.")
                break

            if _normalize(guess_msg.content) in valid:
                score += 1
                await thread.send(f"✅ Correct! It was **{correct.display_name}**. Score: **{score}/{i}**.")
                break

            remaining = MAX_GUESSES - attempt
            if remaining == 0:
                await thread.send(f"❌ Out of guesses. It was **{correct.display_name}**. Score: **{score}/{i}**.")
            else:
                await thread.send(f"Not quite. **{remaining}** {'guess' if remaining == 1 else 'guesses'} left.")

        if i < rounds_to_play:
            await asyncio.sleep(INTERMISSION)

    pct = round(100 * score / rounds_to_play) if rounds_to_play else 0
    await thread.send(f"**Final score: {score}/{rounds_to_play} ({pct}%)** 💬")


# -----------------------------------------------------------------------------
# Multiplayer race mode
# -----------------------------------------------------------------------------

MP_ROUND_TIMEOUT = 30


class _WSMGuessModal(discord.ui.Modal, title="Who sent it?"):
    def __init__(self, view: "_MPWSMView"):
        super().__init__()
        self.view_ref = view
        self.guess = discord.ui.TextInput(label="Sender's name", max_length=64)
        self.add_item(self.guess)

    async def on_submit(self, interaction: discord.Interaction):
        await self.view_ref.handle_guess(interaction, self.guess.value)


class _MPWSMView(discord.ui.View):
    def __init__(self, player_ids: set[int], valid: set[str]):
        # Outlive the round so late clicks get a friendly message instead of
        # Discord's generic "interaction failed".
        super().__init__(timeout=MP_ROUND_TIMEOUT + 20)
        self.player_ids = player_ids
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
            await interaction.response.send_message("Out of guesses.", ephemeral=True)
            return
        if self.finished.is_set():
            await interaction.response.send_message("Round's over.", ephemeral=True)
            return
        await interaction.response.send_modal(_WSMGuessModal(self))

    async def handle_guess(self, interaction: discord.Interaction, raw: str):
        async with self._lock:
            if self.finished.is_set():
                await interaction.response.send_message("Too late.", ephemeral=True)
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
                await interaction.response.send_message("❌ Out of guesses.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"❌ Wrong — **{left}** {'guess' if left == 1 else 'guesses'} left.", ephemeral=True
                )


async def start_multi(thread, players, bot):
    guild = getattr(thread, "guild", None)
    if guild is None:
        await thread.send("Needs to be played in a server.")
        return

    notice = await thread.send("Collecting recent server messages…")
    pool = await _build_pool(guild, exclude_channel_ids={thread.id})
    if not pool:
        await notice.edit(content="No eligible messages found.")
        return
    authors_by_id = {m.author.id: m.author for m in pool if isinstance(m.author, discord.Member)}
    if len(authors_by_id) < MIN_AUTHORS:
        await notice.edit(
            content="Need messages from at least two different people to play — "
                    "the sampled chat is too quiet right now. Try again later."
        )
        return

    rounds_to_play = min(ROUNDS, len(pool))
    chosen_messages = random.sample(pool, rounds_to_play)

    player_ids = {p.id for p in players}
    players_by_id = {p.id: p for p in players}
    scores = {pid: 0 for pid in player_ids}

    names = ", ".join(p.display_name for p in players)
    await notice.edit(
        content=(
            f"**Who Sent the Message? — MP Race**\n"
            f"{rounds_to_play} rounds. Click **Guess** and type the sender's name. "
            f"First correct wins. **{MAX_GUESSES}** guesses each, **{MP_ROUND_TIMEOUT}s** per round.\nPlayers: {names}"
        )
    )

    for i, msg in enumerate(chosen_messages, 1):
        try:
            correct = msg.author
            valid = _valid_names(correct) if isinstance(correct, discord.Member) else {_normalize(correct.name)}
            valid.discard("")

            content = msg.content
            if len(content) > 1000:
                content = content[:1000] + "…"

            scoreboard = " | ".join(
                f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids
            )
            view = _MPWSMView(player_ids, valid)
            await thread.send(
                f"**Round {i}/{rounds_to_play}** — {scoreboard}\n> {content}",
                view=view,
            )
            try:
                await asyncio.wait_for(view.finished.wait(), timeout=MP_ROUND_TIMEOUT)
            except asyncio.TimeoutError:
                pass

            async with view._lock:
                view.finished.set()
                round_winner_id = view.winner_id

            if round_winner_id is not None:
                scores[round_winner_id] += 1
                winner = players_by_id[round_winner_id]
                await thread.send(f"✅ **{winner.display_name}** got it! It was **{correct.display_name}**.")
            else:
                await thread.send(f"⏱ No one got it. It was **{correct.display_name}**.")
        except Exception as e:
            print(f"[who_sent_the_message MP] round {i} error: {e!r}")
            try:
                await thread.send(f"⚠️ Round {i} hit a snag — skipping ahead.")
            except discord.HTTPException:
                pass

        if i < rounds_to_play:
            await asyncio.sleep(INTERMISSION)

    max_score = max(scores.values())
    winners = [players_by_id[pid] for pid, s in scores.items() if s == max_score]
    final = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
    if len(winners) == 1:
        await thread.send(f"🏆 **{winners[0].display_name}** wins!\n{final}")
    else:
        names = ", ".join(w.display_name for w in winners)
        await thread.send(f"🤝 Tie between **{names}**!\n{final}")