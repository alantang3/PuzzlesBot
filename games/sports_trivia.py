import asyncio
import html
import random

import aiohttp
import discord

CATEGORY_ID = 21  # Open Trivia DB "Sports" category
NUM_QUESTIONS = 10
QUESTION_TIMEOUT = 25
API_URL = "https://opentdb.com/api.php"
INTERMISSION = 2.5  # seconds between questions


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


async def _fetch_questions(amount: int) -> list[dict]:
    params = {"amount": amount, "category": CATEGORY_ID, "type": "multiple"}
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL, params=params, timeout=15) as resp:
            data = await resp.json()
    if data.get("response_code") != 0 or not data.get("results"):
        raise RuntimeError("Open Trivia DB returned no results")
    return data["results"]


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
        super().__init__(timeout=MP_ROUND_TIMEOUT)
        self.player_ids = player_ids
        self.correct = correct
        self.locked_out: set[int] = set()
        self.winner_id: int | None = None
        self.finished = asyncio.Event()
        for i, opt in enumerate(options):
            self.add_item(_MPTriviaButton(opt, i))

    async def handle_click(self, interaction: discord.Interaction, value: str, btn: _MPTriviaButton):
        if interaction.user.id not in self.player_ids:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        if interaction.user.id in self.locked_out:
            await interaction.response.send_message("You're locked out of this question.", ephemeral=True)
            return
        if self.finished.is_set():
            await interaction.response.send_message("Too late — someone got it.", ephemeral=True)
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
        question = html.unescape(q["question"])
        correct = html.unescape(q["correct_answer"])
        wrongs = [html.unescape(a) for a in q["incorrect_answers"]]
        options = wrongs + [correct]
        random.shuffle(options)

        scoreboard = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
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

        if view.winner_id is not None:
            scores[view.winner_id] += 1
            winner = players_by_id[view.winner_id]
            await thread.send(f"✅ **{winner.display_name}** got it! Correct answer: **{correct}**.")
        else:
            await thread.send(f"⏱ No one got it. Correct answer: **{correct}**.")

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
