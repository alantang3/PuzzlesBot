import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
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
GAME_CMD_COOLDOWN = 30  # seconds between /game uses per user

# Per-user cooldown tracker for /game. user_id -> last-used unix time.
_game_cmd_last_used: dict[int, float] = {}

intents: Intents = Intents.default()
intents.message_content = True
# Block @everyone, @here, role pings, and arbitrary user pings from any bot message.
# Replies still ping the replied-to user. Prevents mass-mention injection via games like
# Who Sent the Message or the "i'm" easter egg.
SAFE_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=False, replied_user=True)
bot = commands.Bot(command_prefix="!", intents=intents, allowed_mentions=SAFE_MENTIONS)

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
            discord.SelectOption(label="Cryptograms", description="Decode the cryptograms."),
            discord.SelectOption(label="Wordle", description="Guess the 5-letter word."),
            discord.SelectOption(label="Sports Trivia", description="Test your sports knowledge!"),
            discord.SelectOption(label="Guess the Flag", description="Guess the country flag."),
            discord.SelectOption(label="Who's That Pokemon?", description="Guess the Pokemon."),
            discord.SelectOption(label="Who Sent the Message?", description="Guess the message sender."),
            discord.SelectOption(label="Minesweeper", description="Classic minesweeper, 9x9 with 10 mines."),
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
                f"**Ping your friends to add them to this thread**, then type `!start` to begin **{selected}**. "
                f"(Pinging a server member auto-adds them.)\n"
                f"_Some games don't have multiplayer yet — they'll fall back to single-player if started in MP mode._"
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


@bot.command(name="refresh_trends")
@commands.is_owner()
async def refresh_trends_cmd(ctx: commands.Context):
    """Force-refresh the Google Trends dataset now. Bot owner only."""
    age_hours = _trends_age_hours()
    age_str = "never" if age_hours == float("inf") else f"{age_hours:.1f}h ago"
    msg = await ctx.send(f"Refreshing trends data (last refresh: {age_str})…")
    ok, detail = await _run_trends_refresh()
    prefix = "✅" if ok else "⚠️"
    await msg.edit(content=f"{prefix} {detail}")


@refresh_trends_cmd.error
async def _refresh_trends_err(ctx: commands.Context, error):
    # Silently ignore unauthorized attempts; don't reveal the command exists.
    if isinstance(error, commands.NotOwner):
        return
    raise error


@bot.tree.command(
    name="game",
    description="Start a game",
    guild=discord.Object(id=GUILD_ID),
)
async def game(interaction: discord.Interaction):
    # Per-user rate limit: 1 lobby every 30 seconds.
    now = time.time()
    last = _game_cmd_last_used.get(interaction.user.id, 0)
    if now - last < GAME_CMD_COOLDOWN:
        wait = int(GAME_CMD_COOLDOWN - (now - last)) + 1
        await interaction.response.send_message(
            f"Slow down — wait **{wait}s** before starting another game.",
            ephemeral=True,
        )
        return
    _game_cmd_last_used[interaction.user.id] = now

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


async def _collect_thread_players(thread, host: discord.Member) -> list[discord.Member]:
    """Return all non-bot members currently in the thread, with the host first."""
    players: list[discord.Member] = [host]
    seen = {host.id}
    try:
        thread_members = await thread.fetch_members()
    except discord.HTTPException:
        return players
    for tm in thread_members:
        if tm.id in seen:
            continue
        member = thread.guild.get_member(tm.id) if thread.guild else None
        if member is None and thread.guild is not None:
            try:
                member = await thread.guild.fetch_member(tm.id)
            except discord.HTTPException:
                continue
        if member is None or member.bot:
            continue
        players.append(member)
        seen.add(tm.id)
    return players


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

    mode = lobby.get("mode", "Single Player")
    players = await _collect_thread_players(ctx.channel, ctx.author) if mode == "Multiplayer" else [ctx.author]
    if mode == "Multiplayer" and len(players) < 2:
        await ctx.send(
            "Multiplayer needs at least 2 players — ping someone into this thread first, then `!start` again."
        )
        lobby["started"] = False
        return
    if mode == "Multiplayer":
        names = ", ".join(p.display_name for p in players)
        await ctx.send(f"Multiplayer game starting with: **{names}**")

    try:
        while True:
            try:
                await start_game(lobby["game"], ctx.channel, ctx.author, bot, mode=mode, players=players)
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


def _trends_age_hours() -> float:
    """How long since the scores were last actually fetched, per the JSON itself."""
    try:
        with open(TRENDS_DATA_PATH, encoding="utf-8") as f:
            ts = json.load(f).get("last_refreshed")
        if not ts:
            return float("inf")  # never been refreshed with real data
        last = datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return float("inf")


async def _run_trends_refresh() -> tuple[bool, str]:
    """Refresh in a worker thread (pytrends is sync). Returns (ok, message)."""
    try:
        from scripts.build_trends_dataset import refresh_trends_dataset
    except ImportError as e:
        msg = f"skipped — {e}"
        print(f"[trends] {msg}")
        return False, msg
    try:
        count = await asyncio.to_thread(refresh_trends_dataset, False)
        msg = f"refreshed {count} terms"
        print(f"[trends] {msg}")
        return True, msg
    except ImportError:
        msg = "pytrends not installed (pip install pytrends)"
        print(f"[trends] skipped — {msg}")
        return False, msg
    except Exception as e:
        msg = f"refresh failed: {e}"
        print(f"[trends] {msg}")
        return False, msg


@tasks.loop(hours=TRENDS_REFRESH_HOURS)
async def refresh_trends_loop() -> None:
    await _run_trends_refresh()


@refresh_trends_loop.before_loop
async def _before_trends_loop() -> None:
    await bot.wait_until_ready()
    age_hours = _trends_age_hours()
    age_str = "never" if age_hours == float("inf") else f"{age_hours:.1f}h ago"
    if age_hours >= TRENDS_REFRESH_HOURS:
        print(f"[trends] last refresh: {age_str} — refreshing on startup")
        await _run_trends_refresh()
    else:
        print(f"[trends] last refresh: {age_str} — next refresh in {TRENDS_REFRESH_HOURS}h")


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
