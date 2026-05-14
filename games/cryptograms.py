import asyncio
import json
import random
import re
import string
from pathlib import Path

import discord

MAX_HINTS = 3
INACTIVITY_TIMEOUT = 600  # 10 minutes before the bot gives up waiting
QUOTES_PATH = Path(__file__).resolve().parent / "data" / "cryptogram_quotes.json"

# Player commands: "X=Y" or "X =" (unmap)
_MAPPING_RE = re.compile(r"^\s*([a-zA-Z])\s*=\s*([a-zA-Z]?)\s*$")


def _load_quotes() -> list[dict]:
    with open(QUOTES_PATH, encoding="utf-8") as f:
        return json.load(f)["quotes"]


def _make_cipher() -> dict[str, str]:
    """Random monoalphabetic substitution with no fixed points."""
    letters = list(string.ascii_uppercase)
    while True:
        shuffled = letters[:]
        random.shuffle(shuffled)
        if all(a != b for a, b in zip(letters, shuffled)):
            return dict(zip(letters, shuffled))


def _encode(text: str, cipher: dict[str, str]) -> str:
    return "".join(cipher.get(c, c) for c in text.upper())


def _normalize(text: str) -> str:
    return "".join(c.lower() for c in text if c.isalpha())


def _apply(encoded: str, mapping: dict[str, str]) -> str:
    """Render the encoded text with the player's mapping applied; unmapped letters become '_'."""
    out = []
    for ch in encoded:
        if ch.isalpha():
            out.append(mapping.get(ch, "_"))
        else:
            out.append(ch)
    return "".join(out)


def _format_mappings(mapping: dict[str, str]) -> str:
    if not mapping:
        return "_(no mappings yet)_"
    return " ".join(f"`{c}={p}`" for c, p in sorted(mapping.items()))


def _format_board(encoded: str, mapping: dict[str, str]) -> str:
    working = _apply(encoded, mapping)
    return f"```\nCipher:  {encoded}\nWorking: {working}\n```"


async def start(thread, user, bot):
    quotes = _load_quotes()
    chosen = random.choice(quotes)
    plaintext = chosen["text"]
    author = chosen["author"]
    cipher = _make_cipher()
    decipher = {v: k for k, v in cipher.items()}
    encoded = _encode(plaintext, cipher)

    cipher_letters = {c for c in encoded if c.isalpha()}
    target_mapping = {c: decipher[c] for c in cipher_letters}
    target_normalized = _normalize(plaintext)

    mapping: dict[str, str] = {}
    hints_used = 0

    intro = (
        "**Cryptograms** — every letter A-Z has been swapped for another letter. "
        "Decode the quote by mapping cipher letters to plaintext letters one at a time.\n\n"
        "**How to play:**\n"
        "• `X=Y` — guess that cipher letter `X` decodes to plaintext letter `Y` (e.g., `Q=T`)\n"
        "• `X=` — unmap `X` (clear that guess)\n"
        f"• `hint` — reveal one correct mapping (you get **{MAX_HINTS}** of these)\n"
        "• `solve <your full guess>` — submit the whole decoded quote\n"
        "• `give up` — reveal the answer and end the round\n\n"
        "Wrong mappings are free — experiment all you want. Spaces and punctuation are unchanged. "
        "No cipher letter maps to itself. The puzzle wins automatically when every letter is mapped correctly.\n"
    )
    await thread.send(intro + _format_board(encoded, mapping))

    def is_action(msg):
        return (
            msg.author.id == user.id
            and msg.channel.id == thread.id
            and bool(msg.content.strip())
        )

    async def announce_state(prefix: str = ""):
        lines = []
        if prefix:
            lines.append(prefix)
        lines.append(f"Mappings: {_format_mappings(mapping)}  |  Hints left: **{MAX_HINTS - hints_used}**")
        lines.append(_format_board(encoded, mapping))
        await thread.send("\n".join(lines))

    while True:
        try:
            msg = await bot.wait_for("message", check=is_action, timeout=INACTIVITY_TIMEOUT)
        except asyncio.TimeoutError:
            await thread.send(f"No activity for {INACTIVITY_TIMEOUT // 60} minutes. The quote was:\n> {plaintext}\n— **{author}**")
            return

        content = msg.content.strip()
        cmd = content.lower()

        if cmd == "give up":
            await thread.send(f"Game over. The quote was:\n> {plaintext}\n— **{author}**")
            return

        if cmd == "hint":
            if hints_used >= MAX_HINTS:
                await thread.send(f"You've used all **{MAX_HINTS}** hints — keep solving by hand!")
                continue
            unsolved = [c for c in cipher_letters if mapping.get(c) != target_mapping[c]]
            if not unsolved:
                await thread.send("Every letter is already mapped correctly — try submitting `solve` or your final guess.")
                continue
            chosen_cipher = random.choice(unsolved)
            chosen_plain = target_mapping[chosen_cipher]
            mapping[chosen_cipher] = chosen_plain
            hints_used += 1
            await announce_state(prefix=f"Hint: **`{chosen_cipher}` = `{chosen_plain}`**.")
            if mapping == target_mapping:
                await thread.send(f"Solved (with hints)! The quote was:\n> {plaintext}\n— **{author}**")
                return
            continue

        if cmd.startswith("solve "):
            guess = content[len("solve "):].strip()
            if _normalize(guess) == target_normalized:
                await thread.send(f"Solved it! 🎉\n> {plaintext}\n— **{author}**")
                return
            await thread.send("That doesn't match the quote. Keep trying — your mappings are still in place.")
            continue

        m = _MAPPING_RE.match(content)
        if m:
            c_letter = m.group(1).upper()
            p_letter = m.group(2).upper() if m.group(2) else ""

            if c_letter not in cipher_letters:
                await thread.send(f"`{c_letter}` doesn't appear in the cipher. Pick a letter that's actually in the puzzle.")
                continue

            if p_letter == "":
                mapping.pop(c_letter, None)
                await announce_state(prefix=f"Cleared `{c_letter}`.")
            else:
                mapping[c_letter] = p_letter
                await announce_state()

            if mapping == target_mapping:
                await thread.send(f"Solved it! 🎉\n> {plaintext}\n— **{author}**")
                return
            continue

        await thread.send(
            "I didn't catch that. Valid commands:\n"
            "• `X=Y` to map a letter, `X=` to clear one\n"
            "• `hint`  •  `solve <full quote>`  •  `give up`"
        )


# -----------------------------------------------------------------------------
# Multiplayer race (solve-only)
# -----------------------------------------------------------------------------

# Cryptograms are slow; give players generous time
MP_TIMEOUT = 900  # 15 minutes


class _CryptoSolveModal(discord.ui.Modal, title="Decode the quote"):
    def __init__(self, view: "_MPCryptoView"):
        super().__init__()
        self.view_ref = view
        self.guess = discord.ui.TextInput(
            label="Your decoded quote",
            style=discord.TextStyle.paragraph,
            max_length=500,
            placeholder="Type the full decoded text",
        )
        self.add_item(self.guess)

    async def on_submit(self, interaction: discord.Interaction):
        await self.view_ref.handle_solve(interaction, self.guess.value)


class _MPCryptoView(discord.ui.View):
    def __init__(self, player_ids: set[int], target_normalized: str):
        super().__init__(timeout=MP_TIMEOUT)
        self.player_ids = player_ids
        self.target = target_normalized
        self.winner_id: int | None = None
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()

    @discord.ui.button(label="Submit Solution", style=discord.ButtonStyle.success, emoji="🔓")
    async def solve_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id not in self.player_ids:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        if self.finished.is_set():
            await interaction.response.send_message("Round's over.", ephemeral=True)
            return
        await interaction.response.send_modal(_CryptoSolveModal(self))

    async def handle_solve(self, interaction: discord.Interaction, raw: str):
        async with self._lock:
            if self.finished.is_set():
                await interaction.response.send_message("Too late.", ephemeral=True)
                return
            if _normalize(raw) == self.target:
                self.winner_id = interaction.user.id
                await interaction.response.send_message("✅ Correct!", ephemeral=True)
                for child in self.children:
                    child.disabled = True
                self.finished.set()
            else:
                await interaction.response.send_message(
                    "❌ Not quite — keep working on it.", ephemeral=True
                )


async def start_multi(thread, players, bot):
    quotes = _load_quotes()
    chosen = random.choice(quotes)
    plaintext = chosen["text"]
    author = chosen["author"]
    cipher = _make_cipher()
    encoded = _encode(plaintext, cipher)
    target = _normalize(plaintext)

    player_ids = {p.id for p in players}
    players_by_id = {p.id: p for p in players}

    names = ", ".join(p.display_name for p in players)
    view = _MPCryptoView(player_ids, target)
    await thread.send(
        f"**Cryptograms — MP Race**\n"
        f"Same cipher for all players. Solve it mentally (or on paper). "
        f"First to submit the correct decoded quote wins. Unlimited submissions, **{MP_TIMEOUT // 60} minutes** max.\n"
        f"Spaces and punctuation are unchanged. No letter maps to itself. "
        f"Submissions are private — only you see whether you got it right.\n"
        f"Players: {names}\n\n```{encoded}```",
        view=view,
    )

    try:
        await asyncio.wait_for(view.finished.wait(), timeout=MP_TIMEOUT)
    except asyncio.TimeoutError:
        await thread.send(f"⏱ Time's up! The quote was:\n> {plaintext}\n— **{author}**")
        return

    if view.winner_id is not None:
        winner = players_by_id[view.winner_id]
        await thread.send(f"🏆 **{winner.display_name}** solved it!\n> {plaintext}\n— **{author}**")
