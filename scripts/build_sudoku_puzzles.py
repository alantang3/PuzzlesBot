"""Generate games/data/sudoku_puzzles.json.

The game uses a curated bank (no generation at play time). This script builds
that bank offline: it makes a full solved grid, then removes cells one at a
time, keeping a removal only if the puzzle still has a UNIQUE solution. So
every shipped puzzle is guaranteed solvable with exactly one answer.

Difficulty is approximated by the number of givens (fewer = harder) — a
standard, good-enough proxy for a casual bot.

Run:  python scripts/build_sudoku_puzzles.py
"""

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "games" / "data" / "sudoku_puzzles.json"

PER_DIFFICULTY = 15
GIVENS = {"easy": 42, "medium": 34, "hard": 28}


def _solve_count(grid: list[int], limit: int = 2) -> int:
    """Count solutions up to `limit` (early-exit). 0 cell = empty."""
    try:
        i = grid.index(0)
    except ValueError:
        return 1
    r, c = divmod(i, 9)
    box = (r // 3) * 3 + (c // 3)
    used = set()
    for k in range(9):
        used.add(grid[r * 9 + k])
        used.add(grid[k * 9 + c])
    br, bc = (r // 3) * 3, (c // 3) * 3
    for dr in range(3):
        for dc in range(3):
            used.add(grid[(br + dr) * 9 + (bc + dc)])

    total = 0
    for v in range(1, 10):
        if v in used:
            continue
        grid[i] = v
        total += _solve_count(grid, limit)
        grid[i] = 0
        if total >= limit:
            return total
    return total


def _full_grid() -> list[int]:
    grid = [0] * 81

    def fill(pos: int) -> bool:
        if pos == 81:
            return True
        r, c = divmod(pos, 9)
        if grid[pos] != 0:
            return fill(pos + 1)
        vals = list(range(1, 10))
        random.shuffle(vals)
        for v in vals:
            ok = True
            for k in range(9):
                if grid[r * 9 + k] == v or grid[k * 9 + c] == v:
                    ok = False
                    break
            if ok:
                br, bc = (r // 3) * 3, (c // 3) * 3
                for dr in range(3):
                    for dc in range(3):
                        if grid[(br + dr) * 9 + (bc + dc)] == v:
                            ok = False
            if ok:
                grid[pos] = v
                if fill(pos + 1):
                    return True
                grid[pos] = 0
        return False

    fill(0)
    return grid


def _make_puzzle(target_givens: int) -> tuple[str, str]:
    solution = _full_grid()
    puzzle = solution[:]
    order = list(range(81))
    random.shuffle(order)
    givens = 81
    for idx in order:
        if givens <= target_givens:
            break
        if puzzle[idx] == 0:
            continue
        saved = puzzle[idx]
        puzzle[idx] = 0
        if _solve_count(puzzle[:], 2) != 1:
            puzzle[idx] = saved  # removal broke uniqueness — keep it
        else:
            givens -= 1
    return (
        "".join(str(d) for d in puzzle),
        "".join(str(d) for d in solution),
    )


if __name__ == "__main__":
    bank: dict[str, list[dict]] = {}
    for diff, target in GIVENS.items():
        bank[diff] = []
        for _ in range(PER_DIFFICULTY):
            pz, sol = _make_puzzle(target)
            bank[diff].append({"puzzle": pz, "solution": sol})
        print(f"{diff}: {len(bank[diff])} puzzles "
              f"(~{81 - bank[diff][0]['puzzle'].count('0')} givens example)")
    OUT.write_text(
        json.dumps(bank, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {sum(len(v) for v in bank.values())} puzzles to {OUT}")
