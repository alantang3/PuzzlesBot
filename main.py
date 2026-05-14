import asyncio
import os
import random
import time
from pathlib import Path
from typing import Final

import discord
from discord import Intents, Message, TextChannel
from discord.ext import commands, tasks
from discord.ui import Select, View
from dotenv import load_dotenv

from games import GAMES, start_game

load_dotenv()
TOKEN: Final[str] = os.getenv("DISCORD_TOKEN")
GUILD_ID: Final[int] = 1353035066082721896
LOBBY_TTL = 1200  # auto-delete lobby thread after this many seconds if game never starts
POST_GAME_DELAY = 30  # seconds to leave the thread open after a game ends
TRENDS_REFRESH_HOURS = 12
TRENDS_DATA_PATH = Path(__file__).resolve().parent / "games" / "data" / "trends.json"

intents: Intents = Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# thread_id -> {"game": str, "mode": str, "host": int, "started": bool}
active_lobbies: dict[int, dict] = {}


class GameModeSelectView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(GameModeSelect())


class GameModeSelect(Select):
    def __init__(self):
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
        mode = self.values[0]
        await interaction.response.send_message(
            f"Game mode set to **{mode}**. Now choose a game:",
            view=GameSelectView(mode),
            ephemeral=True,
        )


class GameSelectView(View):
    def __init__(self, mode: str):
        super().__init__(timeout=120)
        self.add_item(GameSelect(mode))


class GameSelect(Select):
    def __init__(self, mode: str):
        self.mode = mode
        implemented = set(GAMES.keys())
        options = [
            discord.SelectOption(label="Guess the Number!", description="Guess the number."),
            discord.SelectOption(label="Higher or Lower?", description="Higher or lower?"),
            discord.SelectOption(label="Rock Paper Scissors", description="Play Rock Paper Scissors."),
            discord.SelectOption(label="Hangman", description="Guess the word."),
            discord.SelectOption(label="Cryptograms", description="Decode the cryptograms. (coming soon)"),
            discord.SelectOption(label="Wordle", description="Guess the word. (coming soon)"),
            discord.SelectOption(label="Sports Trivia", description="Test your sports knowledge! (coming soon)"),
            discord.SelectOption(label="Guess the Flag", description="Guess the country flag. (coming soon)"),
            discord.SelectOption(label="Who's That Pokemon?", description="Guess the Pokemon. (coming soon)"),
            discord.SelectOption(label="Who Sent the Message?", description="Guess the message sender. (coming soon)"),
        ]
        super().__init__(
            placeholder="Choose a game to play...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self._implemented = implemented

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected not in self._implemented:
            await interaction.response.send_message(
                f"**{selected}** isn't ready yet — try one of the games without a *(coming soon)* tag.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, TextChannel):
            await interaction.response.send_message(
                "This command must be used in a regular text channel.",
                ephemeral=True,
            )
            return

        thread = await interaction.channel.create_thread(
            name=f"{interaction.user.name}'s {selected} Lobby",
            type=discord.ChannelType.private_thread,
            invitable=True,
        )
        await thread.add_user(interaction.user)

        if self.mode == "Single Player":
            intro = (
                f"{interaction.user.mention}, welcome to your private game lobby! "
                f"Type `!start` to begin **{selected}**."
            )
        else:
            intro = (
                f"{interaction.user.mention}, welcome to your private game lobby! "
                f"Ping your friends to join. Once everyone's here, type `!start` to begin **{selected}**.\n"
                f"_(Note: multiplayer support is being added — for now the game will run as single player.)_"
            )

        await thread.send(intro)
        async def cleanup():
            await asyncio.sleep(LOBBY_TTL)
            active_lobbies.pop(thread.id, None)
            try:
                await thread.delete()
            except discord.HTTPException:
                pass

        cleanup_task = asyncio.create_task(cleanup())
        active_lobbies[thread.id] = {
            "game": selected,
            "mode": self.mode,
            "host": interaction.user.id,
            "started": False,
            "cleanup_task": cleanup_task,
        }
        await interaction.response.send_message(
            f"Lobby created: {thread.mention}", ephemeral=True
        )


@bot.tree.command(
    name="game",
    description="Start a game",
    guild=discord.Object(id=GUILD_ID),
)
async def game(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Choose a gamemode:",
        view=GameModeSelectView(),
        ephemeral=True,
    )


class PostGameView(View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.choice: str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the lobby host can decide.", ephemeral=True
            )
            return False
        return True

    async def _resolve(self, interaction: discord.Interaction, choice: str) -> None:
        self.choice = choice
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        self.stop()

    @discord.ui.button(label="Play Again", style=discord.ButtonStyle.success)
    async def play_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "again")

    @discord.ui.button(label="End", style=discord.ButtonStyle.danger)
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "end")


@bot.command(name="start")
async def start_cmd(ctx: commands.Context):
    lobby = active_lobbies.get(ctx.channel.id)
    if lobby is None:
        return  # not in a tracked lobby thread; ignore
    if lobby["started"]:
        await ctx.send("This game has already started.")
        return
    if ctx.author.id != lobby["host"]:
        await ctx.send("Only the lobby host can start the game.")
        return

    lobby["started"] = True
    cleanup_task = lobby.get("cleanup_task")
    if cleanup_task is not None:
        cleanup_task.cancel()

    try:
        while True:
            try:
                await start_game(lobby["game"], ctx.channel, ctx.author, bot)
            except Exception as e:
                print(f"[game] {lobby['game']} crashed: {e!r}")
                await ctx.send("The game ran into an error — closing this lobby.")
                break

            view = PostGameView(ctx.author.id)
            await ctx.send(
                f"Game over! Play **{lobby['game']}** again, or end the lobby?",
                view=view,
            )
            timed_out = await view.wait()
            if timed_out or view.choice != "again":
                break
    finally:
        active_lobbies.pop(ctx.channel.id, None)
        try:
            await ctx.send(f"Closing this lobby in {POST_GAME_DELAY}s.")
        except discord.HTTPException:
            pass
        await asyncio.sleep(POST_GAME_DELAY)
        try:
            await ctx.channel.delete()
        except discord.HTTPException:
            pass


async def _run_trends_refresh() -> None:
    """Refresh the Google Trends dataset in a worker thread (pytrends is sync)."""
    try:
        from scripts.build_trends_dataset import refresh_trends_dataset
    except ImportError as e:
        print(f"[trends] skipped — {e}")
        return
    try:
        count = await asyncio.to_thread(refresh_trends_dataset, False)
        print(f"[trends] refreshed {count} terms")
    except ImportError:
        print("[trends] skipped — pytrends not installed (pip install pytrends)")
    except Exception as e:
        print(f"[trends] refresh failed: {e}")


@tasks.loop(hours=TRENDS_REFRESH_HOURS)
async def refresh_trends_loop() -> None:
    await _run_trends_refresh()


@refresh_trends_loop.before_loop
async def _before_trends_loop() -> None:
    await bot.wait_until_ready()
    # Self-heal stale data on startup: refresh now if the file is older than the interval.
    try:
        age_hours = (time.time() - TRENDS_DATA_PATH.stat().st_mtime) / 3600
    except FileNotFoundError:
        age_hours = float("inf")
    if age_hours >= TRENDS_REFRESH_HOURS:
        print(f"[trends] dataset is {age_hours:.1f}h old — refreshing on startup")
        await _run_trends_refresh()
    else:
        print(f"[trends] dataset is {age_hours:.1f}h old — next refresh in {TRENDS_REFRESH_HOURS}h")


@bot.event
async def on_ready() -> None:
    await bot.wait_until_ready()
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    if not refresh_trends_loop.is_running():
        refresh_trends_loop.start()
    print(f"{bot.user} is now running!")


@bot.event
async def on_message(message: Message) -> None:
    if message.author == bot.user:
        return

    print(f'[{message.channel}] {message.author}: "{message.content}"')

    # Run command processing (handles !start).
    await bot.process_commands(message)

    # Easter egg — skip in active game threads so it doesn't interrupt gameplay.
    if message.channel.id in active_lobbies:
        return
    content = message.content.strip().lower()
    if content.startswith("i'm "):
        name = message.content[4:]
        pick = random.randint(1, 2)
        if pick == 1:
            await message.channel.send(f"Hi {name}, I'm Alan Tang, the goat")
        else:
            await message.channel.send(f"Hi {name}, I'm David Tan, the fraud")


def main() -> None:
    bot.run(token=TOKEN)


if __name__ == "__main__":
    main()
