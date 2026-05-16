import asyncio
import html
import json
import os
import random
from pathlib import Path

import aiohttp
import discord

NUM_QUESTIONS = 10
QUESTION_TIMEOUT = 25
API_URL = "https://the-trivia-api.com/v2/questions"
TRIVIA_CATEGORY = "sport_and_leisure"  # The Trivia API sports category
INTERMISSION = 2.5  # seconds between questions

# Growing, deduplicated local question bank. The API only seeds it; every
# fetch tops it up with questions not already stored. Matches the data/
# JSON convention used by cryptograms.
BANK_PATH = Path(__file__).resolve().parent / "data" / "sports_trivia.json"
_BANK_LOCK = asyncio.Lock()  # serialise read-merge-write across concurrent games


class TriviaView(discord.ui.View):
    def __init__(self, user_id: int, options: list[str], correct: str):
        super().__init__(timeout=QUESTION_TIMEOUT)
        self.user_id = user_id
        self.correct = correct
        self.chosen: str | None = None
        for i, opt in enumerate(options):
            self.add_item(_OptionButton(opt, i))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    async def resolve(self, interaction, choice: str):
        self.chosen = choice
        for child in self.children:
            child.disabled = True
            if isinstance(child, _OptionButton):
                if child.value == self.correct:
                    child.style = discord.ButtonStyle.success
                elif child.value == choice:
                    child.style = discord.ButtonStyle.danger
        await interaction.response.edit_message(view=self)
        self.stop()


class _OptionButton(discord.ui.Button):
    def __init__(self, value: str, idx: int):
        # Discord button labels max out at 80 chars
        label = value if len(value) <= 80 else value[:77] + "..."
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=idx // 2)
        self.value = value

    async def callback(self, interaction: discord.Interaction):
        view: TriviaView = self.view
        await view.resolve(interaction, self.value)


def _dedup_key(q: dict) -> str:
    """Stable identity for a question. Prefer the API's own id; fall back to
    the normalised prompt text so older/keyless entries still dedupe."""
    qid = q.get("id")
    if qid:
        return f"id:{qid}"
    return "q:" + " ".join(str(q.get("question", "")).lower().split())


def _load_bank() -> list[dict]:
    try:
        with open(BANK_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("questions", []) if isinstance(data, dict) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_bank(questions: list[dict]) -> None:
    BANK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BANK_PATH.parent / (BANK_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"questions": questions}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, BANK_PATH)  # atomic: never leaves a half-written bank


async def _fetch_from_api(amount: int) -> list[dict]:
    params = {"categories": TRIVIA_CATEGORY, "limit": amount}
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL, params=params, timeout=15) as resp:
            resp.raise_for_status()
            data = await resp.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError("The Trivia API returned no results")

    # Normalise v2 shape into the internal dict the game loops expect.
    # (v2 nests the prompt under question.text; v1 used a plain string.)
    questions: list[dict] = []
    for item in data:
        q = item.get("question")
        text = q.get("text") if isinstance(q, dict) else q
        correct = item.get("correctAnswer")
        wrongs = item.get("incorrectAnswers") or []
        if not text or correct is None or not wrongs:
            continue
        questions.append({
            "id": item.get("id"),
            "question": text,
            "correct_answer": correct,
            "incorrect_answers": list(wrongs),
            "difficulty": item.get("difficulty", "?"),
        })
    return questions


async def _refresh_bank(amount: int) -> list[dict]:
    """Fetch from the API and merge any new, non-duplicate questions into the
    JSON bank. Returns the full bank. If the API is unreachable, fall back to
    whatever is already banked instead of failing the game."""
    async with _BANK_LOCK:
        bank = _load_bank()
        seen = {_dedup_key(q) for q in bank}
        try:
            fetched = await _fetch_from_api(amount)
        except Exception as e:
            if bank:
                print(f"[sports_trivia] API fetch failed ({e!r}); "
                      f"using {len(bank)} banked questions")
                return bank
            raise

        added = 0
        for q in fetched:
            key = _dedup_key(q)
            if key in seen:
                continue
            seen.add(key)
            bank.append(q)
            added += 1
        if added:
            _save_bank(bank)
            print(f"[sports_trivia] bank +{added} new question(s) "
                  f"(total {len(bank)})")
        return bank


async def _fetch_questions(amount: int) -> list[dict]:
    """Top up the bank from the API, then draw this game's questions from the
    accumulated bank. Same signature/contract as before, so the game loops
    are unchanged."""
    bank = await _refresh_bank(amount)
    if not bank:
        raise RuntimeError("No trivia questions available")
    pool = bank[:]
    random.shuffle(pool)
    return pool[:amount]


async def start(thread, user, bot):
    try:
        questions = await _fetch_questions(NUM_QUESTIONS)
    except Exception as e:
        await thread.send(f"Couldn't reach the trivia service: `{e}`. Try again later.")
        return

    score = 0
    total = len(questions)
    await thread.send(
        f"**Sports Trivia** — {total} questions, {QUESTION_TIMEOUT}s each. Click your answer."
    )

    for i, q in enumerate(questions, 1):
        question = html.unescape(q["question"])
        correct = html.unescape(q["correct_answer"])
        wrongs = [html.unescape(a) for a in q["incorrect_answers"]]
        options = wrongs + [correct]
        random.shuffle(options)

        view = TriviaView(user.id, options, correct)
        difficulty = q.get("difficulty", "?").capitalize()
        await thread.send(
            f"**Q{i}/{total}**  _(difficulty: {difficulty})_\n{question}",
            view=view,
        )
        timed_out = await view.wait()

        if timed_out or view.chosen is None:
            await thread.send(f"⏱ Out of time. Correct answer: **{correct}**.")
        elif view.chosen == correct:
            score += 1
            await thread.send(f"✅ Correct! Score: **{score}/{i}**.")
        else:
            await thread.send(f"❌ Wrong. Correct answer: **{correct}**. Score: **{score}/{i}**.")

        if i < total:
            await asyncio.sleep(INTERMISSION)

    pct = round(100 * score / total)
    await thread.send(f"**Final score: {score}/{total} ({pct}%)** 🏆")


# -----------------------------------------------------------------------------
# Multiplayer click-first race
# -----------------------------------------------------------------------------

MP_ROUND_TIMEOUT = 20


class _MPTriviaButton(discord.ui.Button):
    def __init__(self, value: str, idx: int):
        label = value if len(value) <= 80 else value[:77] + "..."
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=idx // 2)
        self.value = value

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle_click(interaction, self.value, self)


class _MPTriviaView(discord.ui.View):
    def __init__(self, player_ids: set[int], options: list[str], correct: str):
        # Outlive the round so late clicks get a friendly message instead of
        # Discord's generic "interaction failed".
        super().__init__(timeout=MP_ROUND_TIMEOUT + 20)
        self.player_ids = player_ids
        self.correct = correct
        self.locked_out: set[int] = set()
        self.winner_id: int | None = None
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()
        for i, opt in enumerate(options):
            self.add_item(_MPTriviaButton(opt, i))

    async def handle_click(self, interaction: discord.Interaction, value: str, btn: _MPTriviaButton):
        if interaction.user.id not in self.player_ids:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        async with self._lock:
            if interaction.user.id in self.locked_out:
                await interaction.response.send_message("You're locked out of this question.", ephemeral=True)
                return
            if self.finished.is_set():
                await interaction.response.send_message("Too late — round's over.", ephemeral=True)
                return

            if value == self.correct:
                self.winner_id = interaction.user.id
                for child in self.children:
                    child.disabled = True
                    if isinstance(child, _MPTriviaButton) and child.value == self.correct:
                        child.style = discord.ButtonStyle.success
                await interaction.response.edit_message(view=self)
                self.finished.set()
            else:
                self.locked_out.add(interaction.user.id)
                await interaction.response.send_message("❌ Wrong — you're out for this question.", ephemeral=True)
                if self.locked_out >= self.player_ids:
                    self.finished.set()


async def start_multi(thread, players, bot):
    try:
        questions = await _fetch_questions(NUM_QUESTIONS)
    except Exception as e:
        await thread.send(f"Couldn't reach trivia service: `{e}`.")
        return

    player_ids = {p.id for p in players}
    players_by_id = {p.id: p for p in players}
    scores = {pid: 0 for pid in player_ids}
    total = len(questions)

    names = ", ".join(p.display_name for p in players)
    await thread.send(
        f"**Sports Trivia — MP Race**\n"
        f"{total} questions. First to click the correct answer scores. "
        f"Wrong click = locked out for that question.\nPlayers: {names}"
    )

    for i, q in enumerate(questions, 1):
        try:
            question = html.unescape(q["question"])
            correct = html.unescape(q["correct_answer"])
            wrongs = [html.unescape(a) for a in q["incorrect_answers"]]
            options = wrongs + [correct]
            random.shuffle(options)

            scoreboard = " | ".join(
                f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids
            )
            view = _MPTriviaView(player_ids, options, correct)
            difficulty = q.get("difficulty", "?").capitalize()
            await thread.send(
                f"**Q{i}/{total}** _({difficulty})_ — {scoreboard}\n{question}",
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
                await thread.send(f"✅ **{winner.display_name}** got it! Correct answer: **{correct}**.")
            else:
                await thread.send(f"⏱ No one got it. Correct answer: **{correct}**.")
        except Exception as e:
            print(f"[sports_trivia MP] question {i} error: {e!r}")
            try:
                await thread.send(f"⚠️ Question {i} hit a snag — skipping ahead.")
            except discord.HTTPException:
                pass

        if i < total:
            await asyncio.sleep(INTERMISSION)

    max_score = max(scores.values())
    winners = [players_by_id[pid] for pid, s in scores.items() if s == max_score]
    final = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
    if len(winners) == 1:
        await thread.send(f"🏆 **{winners[0].display_name}** wins!\n{final}")
    else:
        names = ", ".join(w.display_name for w in winners)
        await thread.send(f"🤝 Tie between **{names}**!\n{final}")
