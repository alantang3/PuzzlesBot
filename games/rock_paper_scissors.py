import asyncio
import random

import discord

BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
ROUND_TIMEOUT = 30


def decide(p1, p2):
    if p1 == p2:
        return "tie"
    return "p1" if BEATS[p1] == p2 else "p2"


class RPSView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=ROUND_TIMEOUT)
        self.user_id = user_id
        self.choice = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your game.", ephemeral=True
            )
            return False
        return True

    async def _pick(self, interaction, choice):
        self.choice = choice
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        self.stop()

    @discord.ui.button(label="Rock 🪨", style=discord.ButtonStyle.primary)
    async def rock(self, interaction, button):
        await self._pick(interaction, "rock")

    @discord.ui.button(label="Paper 📄", style=discord.ButtonStyle.primary)
    async def paper(self, interaction, button):
        await self._pick(interaction, "paper")

    @discord.ui.button(label="Scissors ✂️", style=discord.ButtonStyle.primary)
    async def scissors(self, interaction, button):
        await self._pick(interaction, "scissors")


async def start(thread, user, bot):
    wins = losses = ties = 0
    rounds = 3
    await thread.send(f"Best of {rounds * 2 - 1} rounds. First to **{rounds}** wins!")

    while wins < rounds and losses < rounds:
        view = RPSView(user.id)
        await thread.send(
            f"Score — You: **{wins}** | Bot: **{losses}** | Ties: **{ties}**\nMake your move:",
            view=view,
        )
        timed_out = await view.wait()
        if timed_out or view.choice is None:
            await thread.send("Out of time! Game ended.")
            return

        bot_choice = random.choice(list(BEATS))
        result = decide(view.choice, bot_choice)
        line = f"You: {EMOJI[view.choice]}  |  Bot: {EMOJI[bot_choice]}  —  "
        if result == "tie":
            ties += 1
            line += "Tie!"
        elif result == "p1":
            wins += 1
            line += "You win the round!"
        else:
            losses += 1
            line += "Bot wins the round."
        await thread.send(line)

    if wins > losses:
        await thread.send(f"🏆 You took it **{wins}–{losses}**! GG.")
    else:
        await thread.send(f"Bot wins **{losses}–{wins}**. Better luck next time.")


# -----------------------------------------------------------------------------
# Multiplayer (2-player) — best-of-5
# -----------------------------------------------------------------------------

MP_TARGET_WINS = 3
MP_ROUND_TIMEOUT = 45


class _MPRPSView(discord.ui.View):
    def __init__(self, players):
        # Outlive the round so late clicks get a friendly message instead of
        # Discord's generic "interaction failed".
        super().__init__(timeout=MP_ROUND_TIMEOUT + 20)
        self.player_ids = {p.id for p in players}
        self.players_by_id = {p.id: p for p in players}
        self.picks: dict[int, str] = {}
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()

    async def _pick(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id not in self.player_ids:
            await interaction.response.send_message(
                "👀 You're spectating this match — only the two players can pick.", ephemeral=True
            )
            return
        async with self._lock:
            if self.finished.is_set():
                await interaction.response.send_message(
                    "This round is already over.", ephemeral=True
                )
                return
            if interaction.user.id in self.picks:
                await interaction.response.send_message(
                    f"You already picked **{self.picks[interaction.user.id]}** — locked in.", ephemeral=True
                )
                return
            self.picks[interaction.user.id] = choice
            await interaction.response.send_message(
                f"You picked **{choice}** {EMOJI[choice]}. Waiting for opponent…", ephemeral=True
            )
            if len(self.picks) == len(self.player_ids):
                for child in self.children:
                    child.disabled = True
                try:
                    await interaction.message.edit(view=self)
                except discord.HTTPException:
                    pass
                self.finished.set()

    @discord.ui.button(label="Rock 🪨", style=discord.ButtonStyle.primary)
    async def rock(self, interaction, _): await self._pick(interaction, "rock")

    @discord.ui.button(label="Paper 📄", style=discord.ButtonStyle.primary)
    async def paper(self, interaction, _): await self._pick(interaction, "paper")

    @discord.ui.button(label="Scissors ✂️", style=discord.ButtonStyle.primary)
    async def scissors(self, interaction, _): await self._pick(interaction, "scissors")


async def start_multi(thread, players, bot):
    if len(players) < 2:
        await thread.send("RPS multiplayer needs 2 players. Falling back to single-player.")
        await start(thread, players[0], bot)
        return
    active = players[:2]
    spectators = players[2:]
    p1, p2 = active
    wins = {p1.id: 0, p2.id: 0}

    header = (
        f"**Rock Paper Scissors — Multiplayer**\n"
        f"{p1.display_name} vs {p2.display_name} — first to **{MP_TARGET_WINS}** wins."
    )
    if spectators:
        names = ", ".join(s.display_name for s in spectators)
        header += f"\n👀 Spectating: {names}"
    await thread.send(header)

    round_num = 0
    consecutive_timeouts = 0
    MAX_CONSECUTIVE_TIMEOUTS = 3
    while max(wins.values()) < MP_TARGET_WINS:
        round_num += 1
        try:
            view = _MPRPSView(active)
            await thread.send(
                f"**Round {round_num}** — score {p1.display_name} **{wins[p1.id]}** | "
                f"**{wins[p2.id]}** {p2.display_name}\nBoth players: pick your move.",
                view=view,
            )
            try:
                await asyncio.wait_for(view.finished.wait(), timeout=MP_ROUND_TIMEOUT)
            except asyncio.TimeoutError:
                pass

            # Atomically close the round and snapshot picks.
            async with view._lock:
                view.finished.set()
                picks = dict(view.picks)

            if len(picks) < 2:
                consecutive_timeouts += 1
                missing = [pl.display_name for pl in active if pl.id not in picks]
                await thread.send(
                    f"⏱ Out of time. Waiting on: **{', '.join(missing)}**. Round skipped."
                )
                if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    await thread.send(
                        f"Too many skipped rounds in a row — ending the match. "
                        f"Final score **{wins[p1.id]}–{wins[p2.id]}**."
                    )
                    return
                continue

            consecutive_timeouts = 0
            c1, c2 = picks[p1.id], picks[p2.id]
            line = f"{p1.display_name}: {EMOJI[c1]}  vs  {EMOJI[c2]} :{p2.display_name}  —  "
            result = decide(c1, c2)
            if result == "tie":
                line += "Tie!"
            elif result == "p1":
                wins[p1.id] += 1
                line += f"**{p1.display_name}** wins the round."
            else:
                wins[p2.id] += 1
                line += f"**{p2.display_name}** wins the round."
            await thread.send(line)
        except Exception as e:
            print(f"[rock_paper_scissors MP] round {round_num} error: {e!r}")
            try:
                await thread.send(f"⚠️ Round {round_num} hit a snag — replaying it.")
            except discord.HTTPException:
                pass
            round_num -= 1  # don't consume a round number on an errored round

    winner = p1 if wins[p1.id] > wins[p2.id] else p2
    await thread.send(
        f"🏆 **{winner.display_name}** wins the match **{wins[winner.id]}–{wins[(p1 if winner is p2 else p2).id]}**!"
    )
