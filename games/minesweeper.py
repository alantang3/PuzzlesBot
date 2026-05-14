import asyncio
import random
import re

import discord

ROWS = 9
COLS = 9
NUM_MINES = 10
INACTIVITY_TIMEOUT = 600  # 10 minutes
ROW_LABELS = "ABCDEFGHI"

_COMMAND_RE = re.compile(r"^\s*(?:(f|flag)\s+)?([a-i])\s*(\d)\s*$", re.IGNORECASE)


def _make_board() -> list[list[dict]]:
    cells = [
        [{"is_mine": False, "adj": 0, "revealed": False, "flagged": False} for _ in range(COLS)]
        for _ in range(ROWS)
    ]
    positions = [(r, c) for r in range(ROWS) for c in range(COLS)]
    for r, c in random.sample(positions, NUM_MINES):
        cells[r][c]["is_mine"] = True
    _recompute_adjacency(cells)
    return cells


def _recompute_adjacency(board: list[list[dict]]) -> None:
    for r in range(ROWS):
        for c in range(COLS):
            if board[r][c]["is_mine"]:
                board[r][c]["adj"] = 0
                continue
            count = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < ROWS and 0 <= cc < COLS and board[rr][cc]["is_mine"]:
                        count += 1
            board[r][c]["adj"] = count


def _move_mine_away(board: list[list[dict]], r: int, c: int) -> None:
    """First-click safety: if the clicked cell holds a mine, move it elsewhere."""
    empties = [
        (rr, cc) for rr in range(ROWS) for cc in range(COLS)
        if not board[rr][cc]["is_mine"] and (rr, cc) != (r, c)
    ]
    if not empties:
        return
    nr, nc = random.choice(empties)
    board[r][c]["is_mine"] = False
    board[nr][nc]["is_mine"] = True
    _recompute_adjacency(board)


def _reveal(board: list[list[dict]], r: int, c: int) -> bool:
    """Reveal (r,c) and flood-fill from zero-neighbor cells. Returns True if a mine was hit."""
    cell = board[r][c]
    if cell["flagged"] or cell["revealed"]:
        return False
    cell["revealed"] = True
    if cell["is_mine"]:
        return True
    if cell["adj"] == 0:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = r + dr, c + dc
                if 0 <= rr < ROWS and 0 <= cc < COLS:
                    if not board[rr][cc]["revealed"] and not board[rr][cc]["flagged"]:
                        _reveal(board, rr, cc)
    return False


def _is_won(board: list[list[dict]]) -> bool:
    for r in range(ROWS):
        for c in range(COLS):
            if not board[r][c]["is_mine"] and not board[r][c]["revealed"]:
                return False
    return True


def _render(board: list[list[dict]], *, reveal_mines: bool = False) -> str:
    lines = ["   " + " ".join(str(c + 1) for c in range(COLS))]
    for r in range(ROWS):
        row = [ROW_LABELS[r] + " "]
        for c in range(COLS):
            cell = board[r][c]
            if reveal_mines and cell["is_mine"]:
                row.append("*")
            elif cell["flagged"]:
                row.append("F")
            elif not cell["revealed"]:
                row.append("?")
            elif cell["adj"] == 0:
                row.append("·")
            else:
                row.append(str(cell["adj"]))
        lines.append(" ".join(row))
    return "```\n" + "\n".join(lines) + "\n```"


def _count_flags(board: list[list[dict]]) -> int:
    return sum(1 for r in range(ROWS) for c in range(COLS) if board[r][c]["flagged"])


async def start(thread, user, bot):
    board = _make_board()
    first_reveal = True

    await thread.send(
        f"**Minesweeper** — {ROWS}×{COLS} grid with **{NUM_MINES}** mines.\n"
        "Commands:\n"
        "• `B4` — reveal that cell (rows A–I, columns 1–9)\n"
        "• `f B4` — flag/unflag\n"
        "• `quit` — give up\n"
        "Legend: `?` hidden  •  `F` flag  •  `·` empty  •  `1`–`8` adjacent mines  •  `*` mine"
    )
    board_msg = await thread.send(_render(board) + f"Mines: **{NUM_MINES}**  •  Flags: **0**")

    async def update_board(text_suffix: str = "") -> None:
        try:
            await board_msg.edit(content=_render(board) + (text_suffix or _status()))
        except discord.HTTPException:
            pass

    def _status() -> str:
        return f"Mines: **{NUM_MINES}**  •  Flags: **{_count_flags(board)}**"

    def is_action(msg):
        return (
            msg.author.id == user.id
            and msg.channel.id == thread.id
            and bool(msg.content.strip())
        )

    while True:
        try:
            msg = await bot.wait_for("message", check=is_action, timeout=INACTIVITY_TIMEOUT)
        except asyncio.TimeoutError:
            await update_board("\nNo activity — game ended.")
            await thread.send(f"Out of time! The mines were:\n{_render(board, reveal_mines=True)}")
            return

        content = msg.content.strip()
        if content.lower() in ("quit", "q", "give up", "giveup"):
            await thread.send(f"Game over. The mines were:\n{_render(board, reveal_mines=True)}")
            return

        m = _COMMAND_RE.match(content)
        if not m:
            await thread.send(
                "Didn't catch that. Type `B4` to reveal, `f B4` to flag/unflag, or `quit`."
            )
            continue

        flag_cmd, row_letter, col_str = m.groups()
        r = ROW_LABELS.index(row_letter.upper())
        c = int(col_str) - 1
        if not (0 <= c < COLS):
            await thread.send(f"Column must be 1–{COLS}.")
            continue
        cell = board[r][c]

        if flag_cmd:
            if cell["revealed"]:
                await thread.send("Can't flag a revealed cell.")
                continue
            cell["flagged"] = not cell["flagged"]
            await update_board()
            continue

        # Reveal
        if cell["flagged"]:
            await thread.send(f"`{row_letter.upper()}{col_str}` is flagged — unflag first (`f {row_letter.upper()}{col_str}`).")
            continue
        if cell["revealed"]:
            await thread.send("Already revealed.")
            continue

        if first_reveal and cell["is_mine"]:
            _move_mine_away(board, r, c)
        first_reveal = False

        hit_mine = _reveal(board, r, c)
        if hit_mine:
            await update_board("\n💥 BOOM")
            await thread.send(f"You hit a mine! Final board:\n{_render(board, reveal_mines=True)}")
            return

        if _is_won(board):
            await update_board("\n🎉 Cleared!")
            await thread.send(f"You cleared the board! Final state:\n{_render(board)}")
            return

        await update_board()
