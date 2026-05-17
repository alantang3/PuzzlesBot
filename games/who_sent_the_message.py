import asyncio
import datetime
import random
import re
from zoneinfo import ZoneInfo

import discord

ROUNDS = 10
ROUND_TIMEOUT = 45
INTERMISSION = 2.5
OPTIONS = 4  # multiple-choice buttons per round (1 correct + up to 3 decoys)
MAX_GUESSES = 2  # typed guesses per round in Free Response mode
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

# Date range is chosen at game start via a modal (M/D/YY, no leading zeros)
# and interpreted in US Central time (DST handled by the IANA zone).
_CENTRAL = ZoneInfo("America/Chicago")
_DATE_SETUP_TIMEOUT = 90  # seconds the host has to pick a range before all-time

# Treat a message as a (any-bot) command if it starts with one of these and
# the next char is a letter — so "!start", ".play", "?help", "$ping", ">say"
# are dropped, but "-5 degrees", "$20", "... hmm" are kept.
_CMD_PREFIXES = "!/.?$;+%&>~=^*"


def _option_label(member, taken: set[str]) -> str:
    """Button label for a candidate sender, disambiguated if a name collides."""
    base = (getattr(member, "display_name", "") or getattr(member, "name", "") or "Unknown").strip()
    label = base[:80] or "Unknown"
    if label.lower() in taken:
        uname = getattr(member, "name", "") or ""
        label = f"{base[:60]} (@{uname})"[:80]
    return label


def _make_options(correct, authors_by_id: dict) -> list[tuple[str, int]]:
    """Correct sender + up to OPTIONS-1 random distinct decoys from the pool's
    authors, shuffled. Returns [(button_label, author_id), ...] (2..OPTIONS)."""
    decoys = [m for aid, m in authors_by_id.items() if aid != correct.id]
    random.shuffle(decoys)
    members = [correct] + decoys[: max(0, OPTIONS - 1)]
    random.shuffle(members)
    taken: set[str] = set()
    opts: list[tuple[str, int]] = []
    for m in members:
        lbl = _option_label(m, taken)
        taken.add(lbl.lower())
        opts.append((lbl, m.id))
    return opts


def _normalize(text) -> str:
    """Lowercase + strip non-alphanumerics so 'Alan ✨' and 'alan' both match
    'alan'. Used by Free Response mode."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _valid_names(member) -> set[str]:
    """Accepted full names for a sender: display name, username, and global
    name (normalised). Full-name match only — no first-name tokens."""
    candidates = {getattr(member, "display_name", ""), getattr(member, "name", "")}
    gn = getattr(member, "global_name", None)
    if gn:
        candidates.add(gn)
    return {n for n in (_normalize(c) for c in candidates) if n}


def _looks_like_command(content: str) -> bool:
    return len(content) >= 2 and content[0] in _CMD_PREFIXES and content[1].isalpha()


def _is_deleted_user(author) -> bool:
    """Discord has no official 'deleted' flag; deleted accounts are renamed to
    the 'deleted_user_<id>' / 'Deleted User' pattern, which is what we match."""
    name = (getattr(author, "name", "") or "").lower()
    disp = (getattr(author, "display_name", "") or "").lower()
    return (
        name.startswith("deleted_user")
        or name.startswith("deleted user")
        or disp == "deleted user"
    )


# User/role mentions + @everyone/@here. Stripped before the length check so a
# message that is *only* pings gets dropped, but a normal message that merely
# contains a ping is kept (and quoted as-is).
_PING_RE = re.compile(r"<@[!&]?\d+>|@everyone|@here")


def _parse_mdy(raw: str) -> tuple[int, int, int]:
    """'M/D/YY' (no leading zeros required) -> (year, month, day)."""
    parts = raw.strip().split("/")
    if len(parts) != 3:
        raise ValueError(f"`{raw}` isn't M/D/YY — e.g. `5/16/26`.")
    try:
        m, d, yy = (int(p) for p in parts)
    except ValueError:
        raise ValueError(f"`{raw}` isn't M/D/YY — e.g. `5/16/26`.")
    year = yy if yy >= 1000 else 2000 + yy
    return year, m, d


def _day_start_ct(raw: str) -> datetime.datetime:
    y, m, d = _parse_mdy(raw)
    try:
        local = datetime.datetime(y, m, d, 0, 0, 0, tzinfo=_CENTRAL)
    except ValueError:
        raise ValueError(f"`{raw}` isn't a real date.")
    return local.astimezone(datetime.timezone.utc)


def _day_end_ct(raw: str) -> datetime.datetime:
    y, m, d = _parse_mdy(raw)
    try:
        local = datetime.datetime(y, m, d, 23, 59, 59, 999999, tzinfo=_CENTRAL)
    except ValueError:
        raise ValueError(f"`{raw}` isn't a real date.")
    return local.astimezone(datetime.timezone.utc)


async def _build_pool(
    guild: discord.Guild,
    exclude_channel_ids: set[int],
    date_start: datetime.datetime | None = None,
    date_end: datetime.datetime | None = None,
) -> list[discord.Message]:
    """Collect eligible messages from POOL_MARKERS random markers. Each marker
    is a random readable channel at a random point in its history, read for up
    to HISTORY_WINDOW messages (fewer is fine — short/sparse spots just
    contribute less). Total cost is the marker count, independent of how many
    channels the server has, while staying random across channels and time.

    date_start/date_end (tz-aware UTC, or None) scope eligible messages to a
    window; passed per game rather than global so concurrent games on
    different servers can't clobber each other's range."""
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
        # Window = overlap of [channel lifetime] and [configured date range].
        lower = channel.created_at or now
        upper = now
        if date_start is not None:
            lower = max(lower, date_start)
        if date_end is not None:
            upper = min(upper, date_end)
        if lower >= upper:
            return []  # channel has nothing in the configured window

        span = (upper - lower).total_seconds()
        anchor = lower + datetime.timedelta(seconds=random.uniform(0, span)) if span > 0 else upper

        out: list[discord.Message] = []
        raw = 0
        async with sem:
            try:
                # `around` (not `before`): `before=anchor` returned everything
                # from the window start up to the anchor capped at the newest
                # 100 — so late anchors yielded the same recent slice and early
                # anchors yielded almost nothing ("4 repeats" / "0 results").
                # `around` grabs ~100 messages centred on the random spot, so
                # each marker is an independent neighbourhood → real variety.
                async for msg in channel.history(limit=HISTORY_WINDOW, around=anchor):
                    raw += 1
                    author = msg.author
                    if author.bot or msg.is_system():
                        continue
                    if _is_deleted_user(author):
                        continue
                    if date_start is not None and msg.created_at < date_start:
                        continue
                    if date_end is not None and msg.created_at > date_end:
                        continue
                    content = (msg.content or "").strip()
                    # Judge length on the text with ping tokens removed: a
                    # message that's only a ping fails, but a real message
                    # that happens to include one passes (kept & quoted as-is).
                    core = _PING_RE.sub("", content).strip()
                    if not (MIN_LEN <= len(core) <= MAX_LEN):
                        continue
                    if content.startswith(("http://", "https://")) or _looks_like_command(content):
                        continue
                    out.append(msg)
            except (discord.Forbidden, discord.HTTPException):
                pass
        return out, raw, channel.name, anchor

    results = await asyncio.gather(*[_read_marker() for _ in range(POOL_MARKERS)])

    pool: list[discord.Message] = []
    seen_ids: set[int] = set()
    marker_dbg: list[str] = []
    for out, raw, chname, anchor in results:
        for msg in out:
            if msg.id in seen_ids:
                continue
            seen_ids.add(msg.id)
            pool.append(msg)
        marker_dbg.append(f"#{chname}@{anchor:%Y-%m-%d}:raw={raw},kept={len(out)}")

    win = ("all" if date_start is None else f"{date_start:%Y-%m-%d}") + ".." + (
        "now" if date_end is None else f"{date_end:%Y-%m-%d}"
    )
    print(
        f"[who_sent_the_message] readable_channels={len(channels)} "
        f"window={win} markers=[{' | '.join(marker_dbg)}] "
        f"unique_pool={len(pool)}"
    )
    return pool


class _DateModal(discord.ui.Modal, title="Date range (US Central)"):
    def __init__(self, view: "_SetupView"):
        super().__init__()
        self.view_ref = view
        self.start_in = discord.ui.TextInput(
            label="Start date  (M/D/YY)", placeholder="e.g. 1/1/25", max_length=10
        )
        self.end_in = discord.ui.TextInput(
            label="End date  (M/D/YY)", placeholder="e.g. 5/16/26", max_length=10
        )
        self.add_item(self.start_in)
        self.add_item(self.end_in)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start = _day_start_ct(self.start_in.value)
            end = _day_end_ct(self.end_in.value)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        if start > end:
            await interaction.response.send_message(
                "⚠️ Start date is after end date.", ephemeral=True
            )
            return
        self.view_ref.result = (start, end)
        await interaction.response.send_message(
            f"📅 Range set: **{self.start_in.value} → {self.end_in.value}** (Central).",
            ephemeral=True,
        )
        self.view_ref.done.set()
        self.view_ref.stop()


class _SetupView(discord.ui.View):
    """One setup step: pick mode (MC/FRQ) then a date range. The date choice
    (Set Date Range submit, or All Time) starts the game."""

    def __init__(self, host_id: int):
        super().__init__(timeout=_DATE_SETUP_TIMEOUT)
        self.host_id = host_id
        self.mode = "mc"  # "mc" = multiple choice, "frq" = free response
        self.result: tuple[datetime.datetime | None, datetime.datetime | None] = (None, None)
        self.done = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.host_id:
            await interaction.response.send_message(
                "Only the host sets this up.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Mode: Multiple Choice",
                       style=discord.ButtonStyle.success, emoji="🔘", row=0)
    async def mode_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = "frq" if self.mode == "mc" else "mc"
        button.label = (
            "Mode: Multiple Choice" if self.mode == "mc" else "Mode: Free Response"
        )
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Set Date Range", style=discord.ButtonStyle.primary,
                       emoji="📅", row=1)
    async def set_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(_DateModal(self))

    @discord.ui.button(label="All Time", style=discord.ButtonStyle.secondary,
                       emoji="♾️", row=1)
    async def all_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.result = (None, None)
        await interaction.response.send_message(
            "♾️ Using **all-time** history.", ephemeral=True
        )
        self.done.set()
        self.stop()


async def _prompt_setup(thread, host, bot):
    """Ask the host for mode + date range. Returns (mode, start, end) where
    start/end are tz-aware UTC or None. Defaults to ("mc", None, None) if no
    choice is made in time."""
    view = _SetupView(host.id)
    msg = await thread.send(
        f"**{host.display_name}**, set up the game:\n"
        f"• **Mode** — toggle Multiple Choice / Free Response (default: Multiple Choice)\n"
        f"• **Date range** — US Central, **M/D/YY** (no leading zeros, e.g. `5/16/26`), "
        f"or **All Time**\n"
        f"_Picking a date option (or All Time) starts the game._",
        view=view,
    )
    try:
        await asyncio.wait_for(view.done.wait(), timeout=_DATE_SETUP_TIMEOUT + 5)
    except asyncio.TimeoutError:
        pass
    try:
        await msg.edit(view=None)
    except discord.HTTPException:
        pass
    return view.mode, view.result[0], view.result[1]


class _WSMOptionButton(discord.ui.Button):
    def __init__(self, label: str, author_id: int, idx: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=idx // 2)
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        await self.view.resolve(interaction, self)


class _WSMChoiceView(discord.ui.View):
    def __init__(self, user_id: int, options: list[tuple[str, int]], correct_id: int):
        super().__init__(timeout=ROUND_TIMEOUT)
        self.user_id = user_id
        self.correct_id = correct_id
        self.picked_id: int | None = None
        self.message: discord.Message | None = None
        for idx, (label, aid) in enumerate(options):
            self.add_item(_WSMOptionButton(label, aid, idx))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    async def resolve(self, interaction: discord.Interaction, btn: _WSMOptionButton):
        self.picked_id = btn.author_id
        for child in self.children:
            child.disabled = True
            if isinstance(child, _WSMOptionButton):
                if child.author_id == self.correct_id:
                    child.style = discord.ButtonStyle.success
                elif child is btn:
                    child.style = discord.ButtonStyle.danger
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def start(thread, user, bot):
    guild: discord.Guild | None = getattr(thread, "guild", None)
    if guild is None:
        await thread.send("This game needs to be played inside a server.")
        return

    mode, date_start, date_end = await _prompt_setup(thread, user, bot)
    notice = await thread.send("Collecting server messages…")
    pool = await _build_pool(
        guild, exclude_channel_ids={thread.id},
        date_start=date_start, date_end=date_end,
    )

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

    if mode == "frq":
        await notice.edit(
            content=(
                f"**Who Sent the Message?** — {rounds_to_play} rounds, "
                f"**Free Response**. Type the sender's name (display name or "
                f"username; case/special characters don't matter). "
                f"**{MAX_GUESSES}** guesses per round, **{ROUND_TIMEOUT}s** each."
            )
        )

        def _is_guess(m):
            return (
                m.author.id == user.id
                and m.channel.id == thread.id
                and bool(m.content.strip())
            )

        for i, msg in enumerate(chosen_messages, 1):
            correct = msg.author
            valid = (
                _valid_names(correct)
                if isinstance(correct, discord.Member)
                else {_normalize(getattr(correct, "name", ""))}
            )
            valid.discard("")

            content = msg.content
            if len(content) > 1000:
                content = content[:1000] + "…"
            await thread.send(
                f"**Round {i}/{rounds_to_play}** — Who sent this? (type their name)\n"
                f"> {content}"
            )

            for attempt in range(1, MAX_GUESSES + 1):
                try:
                    g = await bot.wait_for("message", check=_is_guess, timeout=ROUND_TIMEOUT)
                except asyncio.TimeoutError:
                    await thread.send(
                        f"⏱ Out of time. It was **{correct.display_name}**. "
                        f"Score: **{score}/{i}**."
                    )
                    break
                if _normalize(g.content) in valid:
                    score += 1
                    await thread.send(
                        f"✅ Correct! It was **{correct.display_name}**. "
                        f"Score: **{score}/{i}**."
                    )
                    break
                remaining = MAX_GUESSES - attempt
                if remaining == 0:
                    await thread.send(
                        f"❌ Out of guesses. It was **{correct.display_name}**. "
                        f"Score: **{score}/{i}**."
                    )
                else:
                    await thread.send(
                        f"Not quite. **{remaining}** "
                        f"{'guess' if remaining == 1 else 'guesses'} left."
                    )

            if i < rounds_to_play:
                await asyncio.sleep(INTERMISSION)
    else:
        await notice.edit(
            content=(
                f"**Who Sent the Message?** — {rounds_to_play} rounds, "
                f"**Multiple Choice**. Click who sent each message. One guess, "
                f"**{ROUND_TIMEOUT}s** per round."
            )
        )

        for i, msg in enumerate(chosen_messages, 1):
            correct = msg.author
            options = _make_options(correct, authors_by_id)

            content = msg.content
            if len(content) > 1000:
                content = content[:1000] + "…"

            view = _WSMChoiceView(user.id, options, correct.id)
            view.message = await thread.send(
                f"**Round {i}/{rounds_to_play}** — Who sent this?\n> {content}",
                view=view,
            )
            timed_out = await view.wait()

            if timed_out or view.picked_id is None:
                await thread.send(
                    f"⏱ Out of time. It was **{correct.display_name}**. Score: **{score}/{i}**."
                )
            elif view.picked_id == correct.id:
                score += 1
                await thread.send(
                    f"✅ Correct! It was **{correct.display_name}**. Score: **{score}/{i}**."
                )
            else:
                await thread.send(
                    f"❌ Wrong. It was **{correct.display_name}**. Score: **{score}/{i}**."
                )

            if i < rounds_to_play:
                await asyncio.sleep(INTERMISSION)

    pct = round(100 * score / rounds_to_play) if rounds_to_play else 0
    await thread.send(f"**Final score: {score}/{rounds_to_play} ({pct}%)** 💬")


# -----------------------------------------------------------------------------
# Multiplayer race mode
# -----------------------------------------------------------------------------

MP_ROUND_TIMEOUT = 30


class _MPWSMOptionButton(discord.ui.Button):
    def __init__(self, label: str, author_id: int, idx: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=idx // 2)
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle_click(interaction, self.author_id)


class _MPWSMView(discord.ui.View):
    def __init__(self, player_ids: set[int], options: list[tuple[str, int]], correct_id: int):
        # Outlive the round so late clicks get a friendly message instead of
        # Discord's generic "interaction failed".
        super().__init__(timeout=MP_ROUND_TIMEOUT + 20)
        self.player_ids = player_ids
        self.correct_id = correct_id
        self.locked_out: set[int] = set()
        self.winner_id: int | None = None
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()
        for idx, (label, aid) in enumerate(options):
            self.add_item(_MPWSMOptionButton(label, aid, idx))

    async def handle_click(self, interaction: discord.Interaction, picked_id: int):
        if interaction.user.id not in self.player_ids:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        async with self._lock:
            if interaction.user.id in self.locked_out:
                await interaction.response.send_message(
                    "You're locked out of this round.", ephemeral=True
                )
                return
            if self.finished.is_set():
                await interaction.response.send_message("Round's over.", ephemeral=True)
                return
            if picked_id == self.correct_id:
                self.winner_id = interaction.user.id
                for child in self.children:
                    child.disabled = True
                    if isinstance(child, _MPWSMOptionButton) and child.author_id == self.correct_id:
                        child.style = discord.ButtonStyle.success
                await interaction.response.edit_message(view=self)
                self.finished.set()
            else:
                self.locked_out.add(interaction.user.id)
                await interaction.response.send_message(
                    "❌ Wrong — you're out for this round.", ephemeral=True
                )
                if self.locked_out >= self.player_ids:
                    self.finished.set()


class _WSMTypeModal(discord.ui.Modal, title="Who sent it?"):
    def __init__(self, view: "_MPWSMTypeView"):
        super().__init__()
        self.view_ref = view
        self.guess = discord.ui.TextInput(label="Sender's name", max_length=64)
        self.add_item(self.guess)

    async def on_submit(self, interaction: discord.Interaction):
        await self.view_ref.handle_guess(interaction, self.guess.value)


class _MPWSMTypeView(discord.ui.View):
    """Free-response MP race: click Guess → type a name. First correct wins;
    MAX_GUESSES wrong tries locks you out for the round."""

    def __init__(self, player_ids: set[int], valid: set[str]):
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
        await interaction.response.send_modal(_WSMTypeModal(self))

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
                    f"❌ Wrong — **{left}** {'guess' if left == 1 else 'guesses'} left.",
                    ephemeral=True,
                )


async def start_multi(thread, players, bot):
    guild = getattr(thread, "guild", None)
    if guild is None:
        await thread.send("Needs to be played in a server.")
        return

    mode, date_start, date_end = await _prompt_setup(thread, players[0], bot)
    notice = await thread.send("Collecting server messages…")
    pool = await _build_pool(
        guild, exclude_channel_ids={thread.id},
        date_start=date_start, date_end=date_end,
    )
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
    how = (
        "Click **Guess** and type who sent it"
        if mode == "frq"
        else "Click who sent each message"
    )
    await notice.edit(
        content=(
            f"**Who Sent the Message? — MP Race** "
            f"({'Free Response' if mode == 'frq' else 'Multiple Choice'})\n"
            f"{rounds_to_play} rounds. {how} — first correct wins the round. "
            f"A wrong guess locks you out for that round. "
            f"**{MP_ROUND_TIMEOUT}s** per round.\nPlayers: {names}"
        )
    )

    for i, msg in enumerate(chosen_messages, 1):
        try:
            correct = msg.author

            content = msg.content
            if len(content) > 1000:
                content = content[:1000] + "…"

            scoreboard = " | ".join(
                f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids
            )
            if mode == "frq":
                valid = (
                    _valid_names(correct)
                    if isinstance(correct, discord.Member)
                    else {_normalize(getattr(correct, "name", ""))}
                )
                valid.discard("")
                view = _MPWSMTypeView(player_ids, valid)
            else:
                options = _make_options(correct, authors_by_id)
                view = _MPWSMView(player_ids, options, correct.id)
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