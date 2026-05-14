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
