import asyncio
import json
import random
import re
from pathlib import Path

import discord

MIN_PLAYERS = 3            # 1 impostor + 2 crew — true minimum for a meaningful vote
WORDS_PATH = Path(__file__).resolve().parent / "data" / "impostor_words.json"

REVEAL_TIMEOUT = 60        # seconds for everyone to peek at their word
CLUE_TIMEOUT = 45          # seconds per player to give their clue
CLUE_ROUNDS = 1            # clues per player (tunable)
VOTE_TIMEOUT = 60          # seconds for the vote
IMPOSTOR_GUESS_TIMEOUT = 45  # seconds for the caught impostor's one guess


def _load_pairs() -> list[list[str]]:
    with open(WORDS_PATH, encoding="utf-8") as f:
        return json.load(f)["pairs"]


def _normalize(text) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# -----------------------------------------------------------------------------
# Secret word delivery — everyone clicks, each sees only their own word.
# Nobody is told their role: the impostor just has a different (related) word
# and has to work out it's them from the clues.
# -----------------------------------------------------------------------------

class _RevealView(discord.ui.View):
    def __init__(self, word_by_id: dict[int, str]):
        super().__init__(timeout=REVEAL_TIMEOUT)
        self.word_by_id = word_by_id
        self.revealed: set[int] = set()
        self.done = asyncio.Event()

    @discord.ui.button(label="Reveal my word", style=discord.ButtonStyle.primary, emoji="🔍")
    async def reveal_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        word = self.word_by_id.get(interaction.user.id)
        if word is None:
            await interaction.response.send_message(
                "You're not in this game.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"🤫 Your secret word is **{word}**.\n"
            f"Give clues about it without being too obvious. **One player has a "
            f"different (but related) word** — that's the impostor. Find them, "
            f"and if it's you, don't get caught.",
            ephemeral=True,
        )
        self.revealed.add(interaction.user.id)
        if self.revealed >= set(self.word_by_id):
            self.done.set()
            self.stop()


# -----------------------------------------------------------------------------
# Voting
# -----------------------------------------------------------------------------

class _VoteSelect(discord.ui.Select):
    def __init__(self, players):
        options = [
            discord.SelectOption(
                label=p.display_name[:100],
                value=str(p.id),
                description=f"@{getattr(p, 'name', '')}"[:100] or None,
            )
            for p in players[:25]
        ]
        super().__init__(
            placeholder="Vote for who you think the impostor is…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.cast(interaction, int(self.values[0]))


class _VoteView(discord.ui.View):
    def __init__(self, players):
        super().__init__(timeout=VOTE_TIMEOUT)
        self.player_ids = {p.id for p in players}
        self.votes: dict[int, int] = {}  # voter id -> target id
        self.done = asyncio.Event()
        self.add_item(_VoteSelect(players))

    async def cast(self, interaction: discord.Interaction, target_id: int):
        voter = interaction.user.id
        if voter not in self.player_ids:
            await interaction.response.send_message(
                "You're not in this game.", ephemeral=True
            )
            return
        if target_id == voter:
            await interaction.response.send_message(
                "You can't vote for yourself.", ephemeral=True
            )
            return
        self.votes[voter] = target_id
        await interaction.response.send_message(
            "🗳️ Vote recorded (you can change it until everyone's voted).",
            ephemeral=True,
        )
        if set(self.votes) >= self.player_ids:
            self.done.set()
            self.stop()


# -----------------------------------------------------------------------------
# Game
# -----------------------------------------------------------------------------

async def start(thread, user, bot):
    await thread.send(
        "🥸 **Word Impostor** is multiplayer-only — it needs at least "
        f"**{MIN_PLAYERS}** players. Start it in **Multiplayer** mode and ping "
        "some friends into the lobby."
    )


async def start_multi(thread, players, bot):
    if len(players) < MIN_PLAYERS:
        await thread.send(
            f"🥸 **Word Impostor** needs at least **{MIN_PLAYERS}** players "
            f"(only {len(players)} here). Get more people into the lobby and try again."
        )
        return

    pair = list(random.choice(_load_pairs()))
    random.shuffle(pair)
    crew_word, imp_word = pair
    impostor = random.choice(players)
    word_by_id = {
        p.id: (imp_word if p.id == impostor.id else crew_word) for p in players
    }

    names = ", ".join(p.display_name for p in players)
    await thread.send(
        f"🥸 **Word Impostor** — {len(players)} players: {names}\n"
        f"Everyone gets a secret word. **One of you has a different word** and "
        f"doesn't know it. Click below to see yours."
    )

    reveal = _RevealView(word_by_id)
    reveal_msg = await thread.send(
        f"Click to get your secret word ({REVEAL_TIMEOUT}s).", view=reveal
    )
    try:
        await asyncio.wait_for(reveal.done.wait(), timeout=REVEAL_TIMEOUT + 5)
    except asyncio.TimeoutError:
        pass
    try:
        await reveal_msg.edit(view=None)
    except discord.HTTPException:
        pass

    missing = [p for p in players if p.id not in reveal.revealed]
    if missing:
        await thread.send(
            "Heads up — these players never checked their word, but we'll play "
            f"on: {', '.join(m.display_name for m in missing)}"
        )

    # --- Clue phase: each player, in random order, gives a clue ---
    order = random.sample(players, len(players))
    await thread.send(
        f"**Clue phase** — when it's your turn, send **one word** describing "
        f"your secret word. {CLUE_TIMEOUT}s per turn."
        + (f" ({CLUE_ROUNDS} rounds.)" if CLUE_ROUNDS > 1 else "")
    )

    for rnd in range(1, CLUE_ROUNDS + 1):
        if CLUE_ROUNDS > 1:
            await thread.send(f"**— Clue round {rnd}/{CLUE_ROUNDS} —**")
        for p in order:
            await thread.send(f"{p.mention}, your clue? ({CLUE_TIMEOUT}s)")

            def _is_clue(m, _pid=p.id):
                return (
                    m.author.id == _pid
                    and m.channel.id == thread.id
                    and bool(m.content.strip())
                )

            try:
                await bot.wait_for("message", check=_is_clue, timeout=CLUE_TIMEOUT)
            except asyncio.TimeoutError:
                await thread.send(f"⏱ {p.display_name} didn't give a clue.")

    # --- Vote phase ---
    vote = _VoteView(players)
    vote_msg = await thread.send(
        "**Vote!** Who's the impostor? Everyone pick using the menu "
        f"({VOTE_TIMEOUT}s). You can change your vote until everyone has voted.",
        view=vote,
    )
    try:
        await asyncio.wait_for(vote.done.wait(), timeout=VOTE_TIMEOUT + 5)
    except asyncio.TimeoutError:
        pass
    try:
        await vote_msg.edit(view=None)
    except discord.HTTPException:
        pass

    players_by_id = {p.id: p for p in players}
    tally: dict[int, int] = {}
    for target in vote.votes.values():
        tally[target] = tally.get(target, 0) + 1

    if tally:
        top = max(tally.values())
        leaders = [pid for pid, c in tally.items() if c == top]
        breakdown = " | ".join(
            f"{players_by_id[pid].display_name}: {c}"
            for pid, c in sorted(tally.items(), key=lambda kv: -kv[1])
        )
    else:
        leaders = []
        breakdown = "no votes cast"

    await thread.send(f"**Votes:** {breakdown}")

    reveal_line = (
        f"The words were **{crew_word}** (crew) and **{imp_word}** (impostor). "
        f"The impostor was **{impostor.display_name}**."
    )

    # Tie or no votes → impostor escapes.
    if len(leaders) != 1:
        await thread.send(
            f"😼 No clear majority — the impostor slips away. "
            f"**Impostor wins!**\n{reveal_line}"
        )
        return

    ejected_id = leaders[0]
    ejected = players_by_id[ejected_id]

    if ejected_id != impostor.id:
        await thread.send(
            f"❌ **{ejected.display_name}** was ejected — but they were crew! "
            f"**Impostor ({impostor.display_name}) wins!**\n{reveal_line}"
        )
        return

    # Impostor was caught — one guess to steal the win.
    await thread.send(
        f"🎯 **{impostor.display_name}** was the impostor and got caught!\n"
        f"{impostor.mention}, last chance — **what was the crew's word?** "
        f"Type your guess ({IMPOSTOR_GUESS_TIMEOUT}s)."
    )

    def _is_imp_guess(m):
        return (
            m.author.id == impostor.id
            and m.channel.id == thread.id
            and bool(m.content.strip())
        )

    try:
        guess = await bot.wait_for(
            "message", check=_is_imp_guess, timeout=IMPOSTOR_GUESS_TIMEOUT
        )
    except asyncio.TimeoutError:
        await thread.send(f"⏱ No guess in time. **Crew wins!**\n{reveal_line}")
        return

    if _normalize(guess.content) == _normalize(crew_word):
        await thread.send(
            f"🥸 Correct — the word was **{crew_word}**. "
            f"**{impostor.display_name}** steals it. **Impostor wins!**"
        )
    else:
        await thread.send(
            f"🚫 Nope — the word was **{crew_word}**. **Crew wins!**\n"
            f"(The impostor was {impostor.display_name}.)"
        )
