from typing import Final
import os
import discord
from dotenv import load_dotenv
from discord import Intents, Client, Message, TextChannel, app_commands, ChannelType
from discord.ext import commands
from discord.ui import View, Select, Button
from responses import get_response
import random
import asyncio

load_dotenv()
TOKEN: Final[str] = os.getenv("DISCORD_TOKEN")

intents: Intents = Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Track active RPS games: thread_id -> game state dict
active_rps_games = {}


async def send_message(message: Message, user_message: str) -> None:
    if not user_message:
        print("(Message was empty because intents were not enabled")
        return


# ─────────────────────────────────────────────────────────────────────────────
# Game Mode / Game Selection UI
# ─────────────────────────────────────────────────────────────────────────────


class GameModeSelectView(View):
    def __init__(self):
        super().__init__()
        self.add_item(GameModeSelect(self))


class GameModeSelect(Select):
    def __init__(self, parent_view: GameModeSelectView):
        self.parent_view = parent_view
        self.has_selected = False
        options = [
            discord.SelectOption(label="Single Player", description="Play alone"),
            discord.SelectOption(label="Multiplayer", description="Play with friends"),
        ]

        super().__init__(
            placeholder="Choose a game mode...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.has_selected:
            await interaction.response.send_message(
                "You have already selected a game mode. Please restart the command to choose a new game mode",
                ephemeral=True,
            )
            return
        self.has_selected = True
        mode = self.values[0]
        await interaction.response.send_message(
            f"Game mode set to {mode}. Now choose a game to play:",
            view=GameSelectView(mode),
            ephemeral=True,
        )


class GameSelectView(View):
    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode
        self.add_item(GameSelect(mode))


class GameSelect(Select):
    def __init__(self, mode: str):
        self.mode = mode
        self.has_selected = False
        options = [
            discord.SelectOption(
                label="Cryptograms", description="Decode the cryptograms."
            ),
            discord.SelectOption(label="Wordle", description="Guess the word"),
            discord.SelectOption(
                label="Sports Trivia", description="Test your sports knowledge!"
            ),
            discord.SelectOption(
                label="Guess the Flag", description="Guess the country flag."
            ),
            discord.SelectOption(
                label="Who's That Pokemon?", description="Guess the Pokemon."
            ),
            discord.SelectOption(
                label="Higher or Lower?", description="Higher or lower?"
            ),
            discord.SelectOption(label="Hangman", description="Guess the word."),
            discord.SelectOption(
                label="Who Sent the Message?", description="Guess the message sender."
            ),
            discord.SelectOption(
                label="Guess the Number!", description="Guess the number."
            ),
            discord.SelectOption(
                label="Rock Paper Scissors", description="Play Rock Paper Scissors."
            ),
        ]

        super().__init__(
            placeholder="Choose a game to play...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.has_selected:
            await interaction.response.send_message(
                "You have already selected a game. Please restart the command to choose a new game",
                ephemeral=True,
            )
            return

        self.has_selected = True
        selected_game = self.values[0]

        if self.mode == "Single Player":
            if isinstance(interaction.channel, TextChannel):
                thread = await interaction.channel.create_thread(
                    name=f"{interaction.user.name}'s {selected_game} Lobby",
                    type=discord.ChannelType.private_thread,
                    invitable=True,
                )
                await thread.add_user(interaction.user)
                await thread.send(
                    f"{interaction.user.mention}, Welcome to your private game lobby! Type `!start` to begin!"
                )

                async def delete_thread_after_delay():
                    await asyncio.sleep(1200)
                    await thread.delete()

                asyncio.create_task(delete_thread_after_delay())
            else:
                await interaction.response.send_message(
                    "This command must be used in a regular text channel.",
                    ephemeral=True,
                )

        elif self.mode == "Multiplayer":
            if isinstance(interaction.channel, TextChannel):
                thread = await interaction.channel.create_thread(
                    name=f"{interaction.user.name}'s {selected_game} Lobby",
                    type=discord.ChannelType.private_thread,
                    invitable=True,
                )
                await thread.add_user(interaction.user)
                await thread.send(
                    f"{interaction.user.mention}, Welcome to your private game lobby! Ping your friends to join. Once everyone's here type `!start` to begin!"
                )

                async def delete_thread_after_delay():
                    await asyncio.sleep(1200)
                    await thread.delete()

                asyncio.create_task(delete_thread_after_delay())
            else:
                await interaction.response.send_message(
                    "This command must be used in a regular text channel.",
                    ephemeral=True,
                )


# ─────────────────────────────────────────────────────────────────────────────
# Rock Paper Scissors Logic
# ─────────────────────────────────────────────────────────────────────────────

CHOICES = ["Rock", "Paper", "Scissors"]
EMOJI = {"Rock": "🪨", "Paper": "📄", "Scissors": "✂️"}

BEATS = {
    "Rock": "Scissors",
    "Paper": "Rock",
    "Scissors": "Paper",
}


def rps_outcome(a: str, b: str) -> str:
    """Return 'a', 'b', or 'tie' based on which choice wins."""
    if a == b:
        return "tie"
    if BEATS[a] == b:
        return "a"
    return "b"


class RPSSingleView(View):
    """Buttons for a single-player RPS game (player vs bot)."""

    def __init__(self, player: discord.Member):
        super().__init__(timeout=60)
        self.player = player
        self.done = False

    async def _handle(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                "This isn't your game!", ephemeral=True
            )
            return
        if self.done:
            await interaction.response.send_message(
                "You already picked!", ephemeral=True
            )
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
    """Buttons for a multiplayer RPS game. Each player picks secretly."""

    def __init__(self, players: list[discord.Member], thread_id: int):
        super().__init__(timeout=120)
        self.players = {p.id: p for p in players}
        self.choices: dict[int, str] = {}
        self.thread_id = thread_id

    async def _handle(self, interaction: discord.Interaction, choice: str):
        uid = interaction.user.id
        if uid not in self.players:
            await interaction.response.send_message(
                "You're not part of this game!", ephemeral=True
            )
            return
        if uid in self.choices:
            await interaction.response.send_message(
                "You already made your pick!", ephemeral=True
            )
            return

        self.choices[uid] = choice
        remaining = len(self.players) - len(self.choices)
        await interaction.response.send_message(
            f"Got it! You chose {EMOJI[choice]} **{choice}**. "
            + (f"Waiting for {remaining} more player(s)..." if remaining else ""),
            ephemeral=True,
        )

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
            c1, c2 = self.choices[p1.id], self.choices[p2.id]
            result = rps_outcome(c1, c2)
            if result == "tie":
                lines.append("🤝 It's a **tie**!")
            elif result == "a":
                lines.append(f"🎉 **{p1.display_name}** wins!")
            else:
                lines.append(f"🎉 **{p2.display_name}** wins!")
        else:
            # More than 2 players — find if there's a single winner
            unique_choices = set(self.choices.values())
            if len(unique_choices) == 1 or len(unique_choices) == 3:
                lines.append("🤝 It's a **tie** — everyone cancels out!")
            else:
                winning_choice = None
                for c in unique_choices:
                    if all(
                        rps_outcome(c, other) != "b"
                        for other in unique_choices
                        if other != c
                    ):
                        winning_choice = c
                        break
                if winning_choice:
                    winners = [
                        self.players[uid].display_name
                        for uid, ch in self.choices.items()
                        if ch == winning_choice
                    ]
                    lines.append(
                        f"🎉 **{', '.join(winners)}** win with {EMOJI[winning_choice]} {winning_choice}!"
                    )
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
            missing = [
                self.players[uid].display_name
                for uid in self.players
                if uid not in self.choices
            ]
            await thread.send(
                f"⏰ Game timed out. The following players didn't pick in time: **{', '.join(missing)}**.\nType `!start` to try again."
            )


# ─────────────────────────────────────────────────────────────────────────────
# !start command
# ─────────────────────────────────────────────────────────────────────────────


@bot.command(name="start")
async def start_game(ctx: commands.Context):
    channel = ctx.channel

    # Must be used inside a private thread lobby
    if not isinstance(channel, discord.Thread):
        await ctx.send("This command can only be used inside a game lobby thread.")
        return

    thread_name: str = channel.name

    # Detect game from thread name
    if "Rock Paper Scissors" in thread_name:
        await _start_rps(ctx, channel, thread_name)
    else:
        await ctx.send("This game hasn't been implemented yet. Stay tuned!")


async def _start_rps(ctx: commands.Context, thread: discord.Thread, thread_name: str):
    """Start a Rock Paper Scissors game in the given thread."""

    if thread.id in active_rps_games:
        await ctx.send(
            "A game is already in progress! Finish it first or wait for it to time out."
        )
        return

    # Detect mode by player count — 1 human = vs bot, multiple = multiplayer
    await thread.fetch_members()  # ensure members list is fresh
    members = [m async for m in thread.fetch_members()]
    human_members = []
    for tm in members:
        member = thread.guild.get_member(tm.id)
        if member and not member.bot:
            human_members.append(member)

    if len(human_members) == 0:
        await ctx.send("No players found in this thread!")
        return

    if len(human_members) == 1:
        # Single player vs bot
        player = human_members[0]
        active_rps_games[thread.id] = {"mode": "single", "players": [player.id]}
        view = RPSSingleView(player)
        await ctx.send(
            f"🪨📄✂️ **Rock Paper Scissors** — {player.mention} vs the Bot!\nMake your pick:",
            view=view,
        )
    else:
        # Multiplayer
        active_rps_games[thread.id] = {
            "mode": "multi",
            "players": [m.id for m in human_members],
        }
        view = RPSMultiView(human_members, thread.id)
        mentions = " ".join(m.mention for m in human_members)
        await ctx.send(
            f"🪨📄✂️ **Rock Paper Scissors** — {mentions}\nEach player: click your choice (only you can see your pick):",
            view=view,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Slash command: /game
# ─────────────────────────────────────────────────────────────────────────────


@bot.tree.command(
    name="game",
    description="Start a game",
    guild=discord.Object(id=1353035066082721896),
)
async def game(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Choose a gamemode:", view=GameModeSelectView(), ephemeral=True
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bot events
# ─────────────────────────────────────────────────────────────────────────────


@bot.event
async def on_ready() -> None:
    await bot.wait_until_ready()
    test_guild = discord.Object(id=1353035066082721896)
    await bot.tree.sync(guild=test_guild)
    print(f"{bot.user} is now running!")


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
                await message.channel.send(
                    "Hi " + message.content[4::] + ", I'm Alan Tang, the goat"
                )
            if pick == 2:
                await message.channel.send(
                    "Hi " + message.content[4::] + ", I'm David Tan, the fraud"
                )

    await bot.process_commands(message)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    bot.run(token=TOKEN)


if __name__ == "__main__":
    main()
