import asyncio
import io
import json
import random
import re
import time
from pathlib import Path

import discord
from PIL import Image, ImageDraw, ImageFont

SUDOKU_PATH = Path(__file__).resolve().parent / "data" / "sudoku_puzzles.json"
ROW_LABELS = "ABCDEFGHI"

SP_INACTIVITY = 900        # seconds of no input before single-player gives up
MP_TIMEOUT = 2400          # seconds for the multiplayer race (40 min)
DIFF_TIMEOUT = 60          # seconds to pick a difficulty (defaults to easy)
DEFAULT_DIFFICULTY = "easy"

CELL = 54
GUTTER = 30

_FONT_CACHE: dict[tuple[int, bool], ImageFont.ImageFont] = {}
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
]


def _font(size: int) -> ImageFont.ImageFont:
    key = (size, True)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    for path in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _load_bank() -> dict[str, list[dict]]:
    with open(SUDOKU_PATH, encoding="utf-8") as f:
        return json.load(f)


def _grid_from(s: str) -> list[int]:
    return [int(c) for c in s]


_MOVE_RE = re.compile(r"^\s*([a-iA-I])\s*([1-9])\s*[=\s]?\s*([0-9.xX])\s*$")


def _parse_moves(text: str) -> tuple[list[tuple[int, int, int]], list[str]]:
    """Parse 'B4 7', 'b4=7', 'B4 .' (erase). Batch-separated by , ; or newline.
    Returns (moves as (row, col, value0-9; 0 = erase), bad tokens)."""
    moves: list[tuple[int, int, int]] = []
    bad: list[str] = []
    for tok in re.split(r"[,;\n]+", text):
        tok = tok.strip()
        if not tok:
            continue
        m = _MOVE_RE.match(tok)
        if not m:
            bad.append(tok)
            continue
        r = ROW_LABELS.index(m.group(1).upper())
        c = int(m.group(2)) - 1
        raw = m.group(3)
        v = 0 if raw in (".", "0", "x", "X") else int(raw)
        moves.append((r, c, v))
    return moves, bad


def _is_full(grid: list[int]) -> bool:
    return 0 not in grid


def _is_solved(grid: list[int], solution: list[int]) -> bool:
    return grid == solution


def _render(grid: list[int], given_mask: set[int]) -> io.BytesIO:
    w = GUTTER + 9 * CELL
    h = GUTTER + 9 * CELL
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    label_font = _font(20)
    num_font = _font(30)

    for c in range(9):
        bbox = d.textbbox((0, 0), str(c + 1), font=label_font)
        tw = bbox[2] - bbox[0]
        d.text((GUTTER + c * CELL + CELL / 2 - tw / 2, 6), str(c + 1),
               font=label_font, fill=(90, 90, 90))
    for r in range(9):
        d.text((8, GUTTER + r * CELL + CELL / 2 - 11), ROW_LABELS[r],
               font=label_font, fill=(90, 90, 90))

    for i in range(10):
        lw = 3 if i % 3 == 0 else 1
        col = (20, 20, 20) if i % 3 == 0 else (170, 170, 170)
        x = GUTTER + i * CELL
        d.line([(x, GUTTER), (x, GUTTER + 9 * CELL)], fill=col, width=lw)
        y = GUTTER + i * CELL
        d.line([(GUTTER, y), (GUTTER + 9 * CELL, y)], fill=col, width=lw)

    for idx in range(81):
        v = grid[idx]
        if v == 0:
            continue
        r, c = divmod(idx, 9)
        x0 = GUTTER + c * CELL
        y0 = GUTTER + r * CELL
        color = (0, 0, 0) if idx in given_mask else (37, 99, 235)
        s = str(v)
        bbox = d.textbbox((0, 0), s, font=num_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        d.text((x0 + CELL / 2 - tw / 2 - bbox[0], y0 + CELL / 2 - th / 2 - bbox[1]),
               s, font=num_font, fill=color)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _file(grid: list[int], given_mask: set[int]) -> discord.File:
    return discord.File(_render(grid, given_mask), filename="sudoku.png")


def _apply(grid: list[int], given_mask: set[int],
           moves: list[tuple[int, int, int]]) -> int:
    """Apply moves to a grid in place. Returns count rejected (given cells)."""
    rejected = 0
    for r, c, v in moves:
        idx = r * 9 + c
        if idx in given_mask:
            rejected += 1
            continue
        grid[idx] = v
    return rejected


_HELP = (
    "Enter moves like `B4 7` (row B, col 4 → 7). Erase with `B4 .`. "
    "Batch them: `B4 7, C5 2, A1 .`. Type `quit` to give up."
)


# -----------------------------------------------------------------------------
# Difficulty picker
# -----------------------------------------------------------------------------

class _DiffView(discord.ui.View):
    def __init__(self, who_id: int):
        super().__init__(timeout=DIFF_TIMEOUT)
        self.who_id = who_id
        self.choice: str | None = None
        self.done = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.who_id:
            await interaction.response.send_message(
                "Only the person who started picks the difficulty.", ephemeral=True
            )
            return False
        return True

    async def _pick(self, interaction: discord.Interaction, diff: str):
        self.choice = diff
        await interaction.response.send_message(
            f"Difficulty: **{diff}**.", ephemeral=True
        )
        self.done.set()
        self.stop()

    @discord.ui.button(label="Easy", style=discord.ButtonStyle.success)
    async def easy(self, i: discord.Interaction, _: discord.ui.Button):
        await self._pick(i, "easy")

    @discord.ui.button(label="Medium", style=discord.ButtonStyle.primary)
    async def medium(self, i: discord.Interaction, _: discord.ui.Button):
        await self._pick(i, "medium")

    @discord.ui.button(label="Hard", style=discord.ButtonStyle.danger)
    async def hard(self, i: discord.Interaction, _: discord.ui.Button):
        await self._pick(i, "hard")


async def _pick_difficulty(thread, who) -> str:
    view = _DiffView(who.id)
    msg = await thread.send(
        f"**{who.display_name}**, pick a Sudoku difficulty "
        f"(defaults to **{DEFAULT_DIFFICULTY}** in {DIFF_TIMEOUT}s).",
        view=view,
    )
    try:
        await asyncio.wait_for(view.done.wait(), timeout=DIFF_TIMEOUT + 5)
    except asyncio.TimeoutError:
        pass
    try:
        await msg.edit(view=None)
    except discord.HTTPException:
        pass
    return view.choice or DEFAULT_DIFFICULTY


def _pick_puzzle(bank: dict, difficulty: str) -> tuple[list[int], list[int], set[int]]:
    entry = random.choice(bank[difficulty])
    givens = _grid_from(entry["puzzle"])
    solution = _grid_from(entry["solution"])
    given_mask = {i for i, v in enumerate(givens) if v != 0}
    return givens, solution, given_mask


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"


# -----------------------------------------------------------------------------
# Single player — typed input in the (already private) lobby thread
# -----------------------------------------------------------------------------

async def start(thread, user, bot):
    bank = _load_bank()
    difficulty = await _pick_difficulty(thread, user)
    givens, solution, given_mask = _pick_puzzle(bank, difficulty)
    grid = givens[:]

    await thread.send(
        f"**Sudoku — {difficulty}**\n{_HELP}\nRows **A–I**, columns **1–9**. "
        f"Clues are black, your entries blue. You win when the whole grid is "
        f"correct."
    )
    board_msg = await thread.send(file=_file(grid, given_mask))
    started = time.monotonic()

    def is_input(m):
        return (
            m.author.id == user.id
            and m.channel.id == thread.id
            and bool(m.content.strip())
        )

    while True:
        try:
            msg = await bot.wait_for("message", check=is_input, timeout=SP_INACTIVITY)
        except asyncio.TimeoutError:
            await thread.send(
                f"No activity for a while — ending. The solution was:\n"
                f"||`{''.join(str(d) for d in solution)}`||"
            )
            return

        content = msg.content.strip()
        if content.lower() in ("quit", "q", "give up", "giveup"):
            await thread.send(
                "Game over. Solution:\n"
                f"||`{''.join(str(d) for d in solution)}`||"
            )
            return

        moves, bad = _parse_moves(content)
        if not moves:
            await thread.send(f"Didn't catch a move. {_HELP}")
            continue

        rejected = _apply(grid, given_mask, moves)
        try:
            await board_msg.delete()
        except discord.HTTPException:
            pass
        note = ""
        if rejected:
            note = f" ({rejected} skipped — those are clues you can't change)"
        if bad:
            note += f" (ignored: {', '.join(bad[:5])})"
        board_msg = await thread.send(
            content=f"Updated{note}." if note else None,
            file=_file(grid, given_mask),
        )

        if _is_solved(grid, solution):
            await thread.send(
                f"🎉 **Solved in {_fmt_time(time.monotonic() - started)}!** "
                f"({difficulty})"
            )
            return
        if _is_full(grid):
            await thread.send(
                "The grid is full but not correct yet — keep checking your cells."
            )


# -----------------------------------------------------------------------------
# Multiplayer race — same puzzle, private per-player board (cryptograms model)
# -----------------------------------------------------------------------------

class _SudokuMP:
    def __init__(self, players, solution, givens, given_mask):
        self.solution = solution
        self.givens = givens
        self.given_mask = given_mask
        self.player_ids = {p.id for p in players}
        self.players_by_id = {p.id: p for p in players}
        self.grids = {p.id: givens[:] for p in players}
        self.finished = asyncio.Event()
        self.winner_id: int | None = None
        self.started = time.monotonic()

    def grid(self, pid: int) -> list[int]:
        return self.grids[pid]


class _MoveModal(discord.ui.Modal, title="Sudoku — make moves"):
    def __init__(self, game: "_SudokuMP", view: "_PlayerView"):
        super().__init__()
        self.game = game
        self.view_ref = view
        self.moves = discord.ui.TextInput(
            label="Moves",
            style=discord.TextStyle.paragraph,
            max_length=400,
            placeholder="B4 7, C5 2, A1 .   (erase with . )",
        )
        self.add_item(self.moves)

    async def on_submit(self, interaction: discord.Interaction):
        pid = interaction.user.id
        g = self.game
        moves, bad = _parse_moves(self.moves.value)
        rejected = _apply(g.grid(pid), g.given_mask, moves) if moves else 0

        if not g.finished.is_set() and _is_solved(g.grid(pid), g.solution):
            g.winner_id = pid
            g.finished.set()

        note = "✅ Solved!" if g.winner_id == pid else "Updated."
        extras = []
        if not moves:
            extras.append("no valid moves found")
        if rejected:
            extras.append(f"{rejected} clue-cell(s) skipped")
        if bad:
            extras.append(f"ignored: {', '.join(bad[:4])}")
        if extras:
            note += " (" + "; ".join(extras) + ")"
        if not g.finished.is_set() and _is_full(g.grid(pid)):
            note += " — grid full but not correct yet."

        await interaction.response.edit_message(
            content=note,
            attachments=[_file(g.grid(pid), g.given_mask)],
            view=self.view_ref,
        )


class _PlayerView(discord.ui.View):
    """Private per-player board controls (each player's own ephemeral)."""

    def __init__(self, game: "_SudokuMP"):
        super().__init__(timeout=MP_TIMEOUT)
        self.game = game

    @discord.ui.button(label="Make moves", style=discord.ButtonStyle.primary, emoji="✏️")
    async def move_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id not in self.game.player_ids:
            await interaction.response.send_message(
                "👀 You're spectating — only players can solve.", ephemeral=True
            )
            return
        if self.game.finished.is_set():
            await interaction.response.send_message(
                "This round is over.", ephemeral=True
            )
            return
        await interaction.response.send_modal(_MoveModal(self.game, self))

    @discord.ui.button(label="Refresh board", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id not in self.game.player_ids:
            await interaction.response.send_message(
                "👀 You're spectating.", ephemeral=True
            )
            return
        g = self.game
        await interaction.response.edit_message(
            content="Your board:",
            attachments=[_file(g.grid(interaction.user.id), g.given_mask)],
            view=self,
        )


class _LauncherView(discord.ui.View):
    def __init__(self, game: "_SudokuMP"):
        super().__init__(timeout=MP_TIMEOUT)
        self.game = game

    @discord.ui.button(label="Open my board", style=discord.ButtonStyle.primary, emoji="📝")
    async def open_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        g = self.game
        if interaction.user.id not in g.player_ids:
            await interaction.response.send_message(
                "👀 You're spectating — only players in the lobby can solve.",
                ephemeral=True,
            )
            return
        if g.finished.is_set():
            await interaction.response.send_message(
                "This round is over.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            content="Your private board — use **Make moves** to play.",
            file=_file(g.grid(interaction.user.id), g.given_mask),
            view=_PlayerView(g),
            ephemeral=True,
        )


async def start_multi(thread, players, bot):
    bank = _load_bank()
    host = players[0]
    difficulty = await _pick_difficulty(thread, host)
    givens, solution, given_mask = _pick_puzzle(bank, difficulty)
    game = _SudokuMP(players, solution, givens, given_mask)

    names = ", ".join(p.display_name for p in players)
    await thread.send(
        f"**Sudoku Race — {difficulty}**\n"
        f"Same puzzle, everyone plays their **own private board**. First to "
        f"finish it correctly wins. Click **Open my board** — your board and "
        f"moves are only visible to you.\nPlayers: {names}"
    )
    await thread.send(view=_LauncherView(game))

    try:
        await asyncio.wait_for(game.finished.wait(), timeout=MP_TIMEOUT)
    except asyncio.TimeoutError:
        await thread.send(
            "⏱ Time's up — nobody finished. The solution was:\n"
            f"||`{''.join(str(d) for d in solution)}`||"
        )
        return

    winner = game.players_by_id.get(game.winner_id)
    elapsed = _fmt_time(time.monotonic() - game.started)
    if winner is not None:
        await thread.send(
            f"🏆 **{winner.display_name}** finished first in **{elapsed}** "
            f"({difficulty})! 🎉"
        )
