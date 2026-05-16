import io
import math
import random

import discord
from PIL import Image, ImageDraw, ImageFont

ROWS = 9
COLS = 9
NUM_MINES = 10
INACTIVITY_TIMEOUT = 600  # 10 minutes
ROW_LABELS = "ABCDEFGHI"

CELL = 46          # px per cell
GUTTER = 30        # px label margin (top + left)

NUM_COLORS = {
    1: (25, 118, 210),
    2: (56, 142, 60),
    3: (211, 47, 47),
    4: (123, 31, 162),
    5: (183, 28, 28),
    6: (0, 131, 143),
    7: (33, 33, 33),
    8: (97, 97, 97),
}

_FONT_CACHE: dict[int, ImageFont.ImageFont] = {}
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
]


def _font(size: int) -> ImageFont.ImageFont:
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    font = None
    for path in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=size)  # Pillow >= 10.1
        except TypeError:
            font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


# --- board logic (unchanged) ---------------------------------------------

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


def _count_flags(board: list[list[dict]]) -> int:
    return sum(1 for r in range(ROWS) for c in range(COLS) if board[r][c]["flagged"])


# --- image rendering ------------------------------------------------------

def _centered(draw: ImageDraw.ImageDraw, cx: float, cy: float, text: str,
               font: ImageFont.ImageFont, color: tuple) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=color)


def _draw_flag(draw: ImageDraw.ImageDraw, x0: int, y0: int) -> None:
    px = x0 + CELL // 2
    top = y0 + 10
    bot = y0 + CELL - 9
    draw.line([(px, top), (px, bot)], fill=(33, 33, 33), width=3)
    draw.polygon([(px, top), (px, top + 15), (px - 17, top + 7)], fill=(211, 47, 47))
    draw.line([(px - 12, bot), (px + 12, bot)], fill=(33, 33, 33), width=4)


def _draw_mine(draw: ImageDraw.ImageDraw, x0: int, y0: int) -> None:
    cx, cy = x0 + CELL // 2, y0 + CELL // 2
    rad = CELL // 5
    for ang in range(0, 360, 45):
        dx = math.cos(math.radians(ang)) * (rad + 6)
        dy = math.sin(math.radians(ang)) * (rad + 6)
        draw.line([(cx, cy), (cx + dx, cy + dy)], fill=(20, 20, 20), width=3)
    draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(20, 20, 20))
    draw.ellipse([cx - rad + 4, cy - rad + 4, cx - rad + 9, cy - rad + 9], fill=(225, 225, 225))


def _render_image(board: list[list[dict]], *, reveal_mines: bool = False) -> io.BytesIO:
    w = GUTTER + COLS * CELL
    h = GUTTER + ROWS * CELL
    img = Image.new("RGB", (w, h), (198, 198, 198))
    draw = ImageDraw.Draw(img)
    label_font = _font(20)
    num_font = _font(26)

    for c in range(COLS):
        _centered(draw, GUTTER + c * CELL + CELL / 2, GUTTER / 2,
                  str(c + 1), label_font, (40, 40, 40))
    for r in range(ROWS):
        _centered(draw, GUTTER / 2, GUTTER + r * CELL + CELL / 2,
                  ROW_LABELS[r], label_font, (40, 40, 40))

    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            x0 = GUTTER + c * CELL
            y0 = GUTTER + r * CELL
            x1, y1 = x0 + CELL - 1, y0 + CELL - 1
            show_mine = reveal_mines and cell["is_mine"]

            if show_mine:
                bg = (229, 115, 115) if cell["revealed"] else (200, 200, 200)
                draw.rectangle([x0, y0, x1, y1], fill=bg, outline=(120, 120, 120))
                _draw_mine(draw, x0, y0)
            elif cell["revealed"]:
                draw.rectangle([x0, y0, x1, y1], fill=(228, 228, 228), outline=(165, 165, 165))
                if cell["adj"] > 0:
                    _centered(draw, x0 + CELL / 2, y0 + CELL / 2,
                              str(cell["adj"]), num_font, NUM_COLORS[cell["adj"]])
            else:
                draw.rectangle([x0, y0, x1, y1], fill=(189, 189, 189))
                draw.line([(x0, y0), (x1, y0)], fill=(245, 245, 245), width=2)
                draw.line([(x0, y0), (x0, y1)], fill=(245, 245, 245), width=2)
                draw.line([(x0, y1), (x1, y1)], fill=(122, 122, 122), width=2)
                draw.line([(x1, y0), (x1, y1)], fill=(122, 122, 122), width=2)
                if cell["flagged"]:
                    _draw_flag(draw, x0, y0)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# --- interactive view -----------------------------------------------------

class _RowSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Row…",
            min_values=1,
            max_values=1,
            row=0,
            options=[discord.SelectOption(label=ROW_LABELS[i]) for i in range(ROWS)],
        )

    async def callback(self, interaction: discord.Interaction):
        view: "MinesweeperView" = self.view
        view.sel_row = ROW_LABELS.index(self.values[0])
        self.placeholder = f"Row: {self.values[0]}"
        for opt in self.options:
            opt.default = opt.label == self.values[0]
        await interaction.response.edit_message(view=view)


class _ColSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Column…",
            min_values=1,
            max_values=1,
            row=1,
            options=[discord.SelectOption(label=str(i + 1)) for i in range(COLS)],
        )

    async def callback(self, interaction: discord.Interaction):
        view: "MinesweeperView" = self.view
        view.sel_col = int(self.values[0]) - 1
        self.placeholder = f"Column: {self.values[0]}"
        for opt in self.options:
            opt.default = opt.label == self.values[0]
        await interaction.response.edit_message(view=view)


class _ActionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Reveal", style=discord.ButtonStyle.success,
                         emoji="✅", row=2)

    async def callback(self, interaction: discord.Interaction):
        await self.view.do_action(interaction)


class _FlagButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Flag mode: OFF", style=discord.ButtonStyle.secondary,
                         emoji="🚩", row=2)

    async def callback(self, interaction: discord.Interaction):
        await self.view.toggle_flag(interaction, self)


class _QuitButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Quit", style=discord.ButtonStyle.danger,
                         emoji="❌", row=2)

    async def callback(self, interaction: discord.Interaction):
        await self.view.quit_game(interaction)


class MinesweeperView(discord.ui.View):
    def __init__(self, thread, user, bot):
        super().__init__(timeout=INACTIVITY_TIMEOUT)
        self.thread = thread
        self.user = user
        self.bot = bot
        self.board = _make_board()
        self.first_reveal = True
        self.sel_row: int | None = None
        self.sel_col: int | None = None
        self.flag_mode = False
        self.finished = False
        self.message: discord.Message | None = None

        self.row_select = _RowSelect()
        self.col_select = _ColSelect()
        self.flag_button = _FlagButton()
        self.add_item(self.row_select)
        self.add_item(self.col_select)
        self.add_item(_ActionButton())
        self.add_item(self.flag_button)
        self.add_item(_QuitButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "This isn't your game.", ephemeral=True
            )
            return False
        return True

    def _embed(self) -> discord.Embed:
        mode = "🚩 Flag" if self.flag_mode else "✅ Reveal"
        embed = discord.Embed(title="💣 Minesweeper", color=0x5865F2)
        embed.description = (
            f"**{ROWS}×{COLS}** grid · **{NUM_MINES}** mines\n"
            f"Mines: **{NUM_MINES}** · Flags: **{_count_flags(self.board)}** · "
            f"Mode: **{mode}**\n"
            f"Pick a **row** and **column**, then press the action button."
        )
        embed.set_image(url="attachment://board.png")
        return embed

    def _disable(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _refresh(self, interaction: discord.Interaction, *,
                       reveal_mines: bool = False) -> None:
        file = discord.File(
            _render_image(self.board, reveal_mines=reveal_mines), filename="board.png"
        )
        await interaction.response.edit_message(
            embed=self._embed(), attachments=[file], view=self
        )

    async def toggle_flag(self, interaction: discord.Interaction,
                          button: _FlagButton) -> None:
        self.flag_mode = not self.flag_mode
        button.label = f"Flag mode: {'ON' if self.flag_mode else 'OFF'}"
        button.style = (
            discord.ButtonStyle.primary if self.flag_mode
            else discord.ButtonStyle.secondary
        )
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def quit_game(self, interaction: discord.Interaction) -> None:
        self.finished = True
        self._disable()
        await self._refresh(interaction, reveal_mines=True)
        await self.thread.send("Game over — you quit. The mines are shown above.")
        self.stop()

    async def do_action(self, interaction: discord.Interaction) -> None:
        if self.sel_row is None or self.sel_col is None:
            await interaction.response.send_message(
                "Pick a row **and** a column first.", ephemeral=True
            )
            return

        r, c = self.sel_row, self.sel_col
        cell = self.board[r][c]

        if self.flag_mode:
            if cell["revealed"]:
                await interaction.response.send_message(
                    "Can't flag a revealed cell.", ephemeral=True
                )
                return
            cell["flagged"] = not cell["flagged"]
            await self._refresh(interaction)
            return

        if cell["flagged"]:
            await interaction.response.send_message(
                "That cell is flagged — switch to Flag mode to unflag it first.",
                ephemeral=True,
            )
            return
        if cell["revealed"]:
            await interaction.response.send_message(
                "Already revealed.", ephemeral=True
            )
            return

        if self.first_reveal and cell["is_mine"]:
            _move_mine_away(self.board, r, c)
        self.first_reveal = False

        if _reveal(self.board, r, c):
            self.finished = True
            self._disable()
            await self._refresh(interaction, reveal_mines=True)
            await self.thread.send("💥 **BOOM!** You hit a mine. Game over.")
            self.stop()
            return

        if _is_won(self.board):
            self.finished = True
            self._disable()
            await self._refresh(interaction, reveal_mines=True)
            await self.thread.send("🎉 **You cleared the board!** Well played.")
            self.stop()
            return

        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        if self.finished or self.message is None:
            return
        self._disable()
        try:
            file = discord.File(
                _render_image(self.board, reveal_mines=True), filename="board.png"
            )
            await self.message.edit(embed=self._embed(), attachments=[file], view=self)
            await self.thread.send(
                "⏰ Out of time — game ended. The mines are shown above."
            )
        except discord.HTTPException:
            pass


async def start(thread, user, bot):
    view = MinesweeperView(thread, user, bot)
    file = discord.File(_render_image(view.board), filename="board.png")
    view.message = await thread.send(embed=view._embed(), file=file, view=view)
    await view.wait()
