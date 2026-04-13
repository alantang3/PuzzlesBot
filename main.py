from typing import Final
import os
import discord
from dotenv import load_dotenv
from discord import Intents, Message, TextChannel
from discord.ext import commands
from discord.ui import View, Select, Button
import random
import asyncio

load_dotenv()
TOKEN: Final[str] = os.getenv('DISCORD_TOKEN')

intents: Intents = Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

active_rps_games = {}


async def send_message(message: Message, user_message: str) -> None:
    if not user_message:
        print('(Message was empty because intents were not enabled')
        return


# ── Game Mode Select ──────────────────────────────────────────────────────────

class GameModeSelectView(View):
    def __init__(self):
        super().__init__()
        self.add_item(GameModeSelect())


class GameModeSelect(Select):
    def __init__(self):
        self.has_selected = False
        options = [
            discord.SelectOption(label="Single Player", description="Play alone"),
            discord.SelectOption(label="Multiplayer", description="Play with friends"),
        ]
        super().__init__(placeholder="Choose a game mode...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if self.has_selected:
            await interaction.response.send_message(
                "You have already selected a game mode. Please restart the command to choose a new game mode.",
                ephemeral=True)
            return
        self.has_selected = True
        mode = self.values[0]
        await interaction.response.send_message(
            f"Game mode set to **{mode}**. Now choose a game to play:",
            view=GameSelectView(mode),
            ephemeral=True)


# ── Game Select ───────────────────────────────────────────────────────────────

class GameSelectView(View):
    def __init__(self, mode: str):
        super().__init__()
        self.add_item(GameSelect(mode))


class GameSelect(Select):
    def __init__(self, mode: str):
        self.mode = mode
        self.has_selected = False
        options = [
            discord.SelectOption(label="Cryptograms", description="Decode the cryptograms."),
            discord.SelectOption(label="Wordle", description="Guess the word"),
            discord.SelectOption(label="Sports Trivia", description="Test your sports knowledge!"),
            discord.SelectOption(label="Guess the Flag", description="Guess the country flag."),
            discord.SelectOption(label="Who's That Pokemon?", description="Guess the Pokemon."),
            discord.SelectOption(label="Higher or Lower?", description="Higher or lower?"),
            discord.SelectOption(label="Hangman", description="Guess the word."),
            discord.SelectOption(label="Who Sent the Message?", description="Guess the message sender."),
            discord.SelectOption(label="Guess the Number!", description="Guess the number."),
            discord.SelectOption(label="Rock Paper Scissors", description="Play Rock Paper Scissors."),
        ]
        super().__init__(placeholder="Choose a game to play...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if self.has_selected:
            await interaction.response.send_message(
                "You have already selected a game. Please restart the command to choose a new game.",
                ephemeral=True)
            return

        if not isinstance(interaction.channel, TextChannel):
            await interaction.response.send_message(
                "This command must be used in a regular text channel.", ephemeral=True)
            return

        self.has_selected = True
        selected_game = self.values[0]

        # Defer immediately so Discord doesn't time out while we create the thread
        await interaction.response.defer(ephemeral=True)

        thread = await interaction.channel.create_thread(
            name=f"{interaction.user.name}'s {selected_game} Lobby",
            type=discord.ChannelType.private_thread,
            invitable=True)
        await thread.add_user(interaction.user)

        if self.mode == "Single Player":
            await thread.send(
                f"{interaction.user.mention}, welcome to your private game lobby! "
                f"Type `!start` to begin.")
        else:
            await thread.send(
                f"{interaction.user.mention}, welcome to your private game lobby! "
                f"Ping your friends to join, then type `!start` to begin.")

        await interaction.followup.send(
            f"Your lobby is ready! Head to {thread.mention}.", ephemeral=True)

        async def delete_thread_after_delay():
            await asyncio.sleep(1200)
            try:
                await thread.delete()
            except Exception:
                pass

        asyncio.create_task(delete_thread_after_delay())


# ── Rock Paper Scissors ───────────────────────────────────────────────────────

CHOICES = ["Rock", "Paper", "Scissors"]
EMOJI = {"Rock": "🪨", "Paper": "📄", "Scissors": "✂️"}
BEATS = {"Rock": "Scissors", "Paper": "Rock", "Scissors": "Paper"}


def rps_outcome(a: str, b: str) -> str:
    if a == b:
        return "tie"
    return "a" if BEATS[a] == b else "b"


class RPSLobbyView(View):
    """Lobby: friends click Join, then host clicks Start."""

    def __init__(self, host: discord.Member, thread_id: int):
        super().__init__(timeout=300)
        self.host = host
        self.thread_id = thread_id
        self.players: list[discord.Member] = [host]
        self.started = False

    @discord.ui.button(label="Join Game", style=discord.ButtonStyle.success, emoji="✋")
    async def join(self, interaction: discord.Interaction, button: Button):
        if self.started:
            await interaction.response.send_message("The game has already started!", ephemeral=True)
            return
        if any(p.id == interaction.user.id for p in self.players):
            await interaction.response.send_message("You're already in the lobby!", ephemeral=True)
            return
        self.players.append(interaction.user)
        names = ", ".join(f"**{p.display_name}**" for p in self.players)
        await interaction.response.send_message(
            f"{interaction.user.mention} joined! Players: {names}")

    @discord.ui.button(label="Start Game", style=discord.ButtonStyle.primary, emoji="▶️")
    async def start(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.host.id:
            await interaction.response.send_message("Only the host can start the game!", ephemeral=True)
            return
        if self.started:
            await interaction.response.send_message("Game already started!", ephemeral=True)
            return
        self.started = True
        self.stop()

        if len(self.players) == 1:
            player = self.players[0]
            view = RPSSingleView(player)
            await interaction.response.edit_message(
                content=f"🪨📄✂️ **Rock Paper Scissors** — {player.mention} vs the Bot!\nMake your pick:",
                view=view)
        else:
            view = RPSMultiView(list(self.players), self.thread_id)
            mentions = " ".join(p.mention for p in self.players)
            await interaction.response.edit_message(
                content=f"🪨📄✂️ **Rock Paper Scissors** — {mentions}\n"
                        f"Each player: click your choice (only you can see your pick):",
                view=view)

    async def on_timeout(self):
        self.stop()
        if self.thread_id in active_rps_games:
            del active_rps_games[self.thread_id]


class RPSSingleView(View):
    """Single player vs bot."""

    def __init__(self, player: discord.Member):
        super().__init__(timeout=60)
        self.player = player
        self.done = False

    async def _handle(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("This isn't your game!", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message("You already picked!", ephemeral=True)
            return
        self.done = True
        self.stop()

        bot_choice = random.choice(CHOICES)
        result = rps_outcome(choice, bot_choice)

        lines = [
            f"**{interaction.user.display_name}** chose {EMOJI[choice]} **{choice}**",
            f"**Bot** chose {EMOJI[bot_choice]} **{bot_choice}**",
            "",
        ]
        if result == "tie":
            lines.append("🤝 It's a **tie**!")
        elif result == "a":
            lines.append(f"🎉 **{interaction.user.display_name}** wins!")
        else:
            lines.append("🤖 The **Bot** wins!")
        lines.append("\nType `!start` to play again.")
        await interaction.response.edit_message(content="\n".join(lines), view=None)

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.primary, emoji="🪨")
    async def rock(self, interaction: discord.Interaction, button: Button):
        await self._handle(interaction, "Rock")

    @discord.ui.button(label="Paper", style=discord.ButtonStyle.primary, emoji="📄")
    async def paper(self, interaction: discord.Interaction, button: Button):
        await self._handle(interaction, "Paper")

    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.danger, emoji="✂️")
    async def scissors(self, interaction: discord.Interaction, button: Button):
        await self._handle(interaction, "Scissors")

    async def on_timeout(self):
        self.stop()


class RPSMultiView(View):
    """Multiplayer — each player picks secretly."""

    def __init__(self, players: list[discord.Member], thread_id: int):
        super().__init__(timeout=120)
        self.players = {p.id: p for p in players}
        self.choices: dict[int, str] = {}
        self.thread_id = thread_id

    async def _handle(self, interaction: discord.Interaction, choice: str):
        uid = interaction.user.id
        if uid not in self.players:
            await interaction.response.send_message("You're not part of this game!", ephemeral=True)
            return
        if uid in self.choices:
            await interaction.response.send_message("You already made your pick!", ephemeral=True)
            return

        self.choices[uid] = choice
        remaining = len(self.players) - len(self.choices)
        msg = f"Got it! You chose {EMOJI[choice]} **{choice}**."
        if remaining:
            msg += f" Waiting for {remaining} more player(s)..."
        await interaction.response.send_message(msg, ephemeral=True)

        if len(self.choices) == len(self.players):
            self.stop()
            await self._reveal(interaction)

    async def _reveal(self, interaction: discord.Interaction):
        thread = interaction.guild.get_channel(self.thread_id)
        if thread is None:
            return

        player_list = list(self.players.values())
        lines = ["## 🪨📄✂️ Results\n"]
        for p in player_list:
            c = self.choices[p.id]
            lines.append(f"**{p.display_name}**: {EMOJI[c]} {c}")
        lines.append("")

        if len(player_list) == 2:
            p1, p2 = player_list
            result = rps_outcome(self.choices[p1.id], self.choices[p2.id])
            if result == "tie":
                lines.append("🤝 It's a **tie**!")
            elif result == "a":
                lines.append(f"🎉 **{p1.display_name}** wins!")
            else:
                lines.append(f"🎉 **{p2.display_name}** wins!")
        else:
            unique = set(self.choices.values())
            if len(unique) == 1 or len(unique) == 3:
                lines.append("🤝 It's a **tie** — everyone cancels out!")
            else:
                winning_choice = next(
                    (c for c in unique if all(rps_outcome(c, o) != "b" for o in unique if o != c)),
                    None)
                if winning_choice:
                    winners = [self.players[uid].display_name
                               for uid, ch in self.choices.items() if ch == winning_choice]
                    lines.append(f"🎉 **{', '.join(winners)}** win with {EMOJI[winning_choice]} {winning_choice}!")
                else:
                    lines.append("🤝 It's a **tie**!")

        lines.append("\nType `!start` to play again.")
        await thread.send("\n".join(lines))

        if self.thread_id in active_rps_games:
            del active_rps_games[self.thread_id]

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.primary, emoji="🪨")
    async def rock(self, interaction: discord.Interaction, button: Button):
        await self._handle(interaction, "Rock")

    @discord.ui.button(label="Paper", style=discord.ButtonStyle.primary, emoji="📄")
    async def paper(self, interaction: discord.Interaction, button: Button):
        await self._handle(interaction, "Paper")

    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.danger, emoji="✂️")
    async def scissors(self, interaction: discord.Interaction, button: Button):
        await self._handle(interaction, "Scissors")

    async def on_timeout(self):
        self.stop()
        thread = bot.get_channel(self.thread_id)
        if thread and self.thread_id in active_rps_games:
            del active_rps_games[self.thread_id]
            missing = [self.players[uid].display_name
                       for uid in self.players if uid not in self.choices]
            await thread.send(
                f"⏰ Game timed out. These players didn't pick in time: **{', '.join(missing)}**.\n"
                f"Type `!start` to try again.")


# ── !start command ────────────────────────────────────────────────────────────

@bot.command(name="start")
async def start_game(ctx: commands.Context):
    channel = ctx.channel
    if not isinstance(channel, discord.Thread):
        await ctx.send("This command can only be used inside a game lobby thread.")
        return

    if "Rock Paper Scissors" in channel.name:
        if channel.id in active_rps_games:
            await ctx.send("A game is already in progress! Finish it first or wait for it to time out.")
            return
        active_rps_games[channel.id] = True
        host = ctx.author
        view = RPSLobbyView(host, channel.id)
        await ctx.send(
            f"🪨📄✂️ **Rock Paper Scissors Lobby**\n"
            f"{host.mention} is the host.\n"
            f"Friends: click **Join Game** to enter.\n"
            f"Host: click **Start Game** when everyone's ready.\n"
            f"*(Solo vs bot? Just click Start Game now!)*",
            view=view)
    else:
        await ctx.send("This game hasn't been implemented yet. Stay tuned!")


# ── Slash command: /game ──────────────────────────────────────────────────────

@bot.tree.command(name="game",
                  description="Start a game",
                  guild=discord.Object(id=1353035066082721896))
async def game(interaction: discord.Interaction):
    await interaction.response.send_message("Choose a gamemode:",
                                            view=GameModeSelectView(),
                                            ephemeral=True)


# ── Bot events ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    await bot.wait_until_ready()
    test_guild = discord.Object(id=1353035066082721896)
    await bot.tree.sync(guild=test_guild)
    print(f'{bot.user} is now running!')


@bot.event
async def on_message(message: Message) -> None:
    if message.author == bot.user:
        return

    username: str = str(message.author)
    user_message: str = message.content
    channel: str = str(message.channel)

    print(f'[{channel}] {username}: "{user_message}"')

    await send_message(message, user_message)

    if message.content.strip():
        temp = message.content.lower()
        if temp.startswith("i'm "):
            pick = random.randint(1, 2)
            if pick == 1:
                await message.channel.send("Hi " + message.content[4:] + ", I'm Alan Tang, the goat")
            if pick == 2:
                await message.channel.send("Hi " + message.content[4:] + ", I'm David Tan, the fraud")

    await bot.process_commands(message)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    bot.run(token=TOKEN)


if __name__ == '__main__':
    main()
