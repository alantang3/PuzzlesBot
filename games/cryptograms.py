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
        "• `reset` — clear all your mappings and start the board over\n"
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

        if cmd == "reset":
            mapping.clear()
            await announce_state(prefix="🔄 Board reset — all mappings cleared. (Used hints aren't refunded.)")
            continue

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
            "• `hint`  •  `solve <full quote>`  •  `reset`  •  `give up`"
        )


# -----------------------------------------------------------------------------
# Multiplayer race — private per-player letter mapping (like single player)
# -----------------------------------------------------------------------------

MP_TIMEOUT = 1200  # 20 minutes — cryptograms are slow

# One token like "L=T" or "L=" (clear). Tokens separated by commas/whitespace/newlines.
_MP_TOKEN_RE = re.compile(r"^([a-zA-Z])=([a-zA-Z]?)$")


class _MPCryptoGame:
    def __init__(self, players, encoded: str, target_mapping: dict[str, str],
                 target_normalized: str, plaintext: str, author: str):
        self.player_ids = {p.id for p in players}
        self.players_by_id = {p.id: p for p in players}
        self.encoded = encoded
        self.cipher_letters = {c for c in encoded if c.isalpha()}
        self.target_mapping = target_mapping       # cipher letter -> correct plain letter
        self.target_normalized = target_normalized
        self.plaintext = plaintext
        self.author = author
        self.mappings: dict[int, dict[str, str]] = {}  # player id -> their cipher->plain map
        self.winner_id: int | None = None
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()

    def _player_map(self, uid: int) -> dict[str, str]:
        return self.mappings.setdefault(uid, {})

    def _is_solved(self, uid: int) -> bool:
        m = self.mappings.get(uid, {})
        return all(m.get(c) == self.target_mapping[c] for c in self.cipher_letters)

    def _apply_player(self, uid: int) -> str:
        return _apply(self.encoded, self.mappings.get(uid, {}))

    def _workspace_text(self, uid: int, note: str = "") -> str:
        m = self.mappings.get(uid, {})
        lines = []
        if note:
            lines.append(note)
        lines.append(f"Mappings: {_format_mappings(m)}")
        lines.append(f"```\nCipher:  {self.encoded}\nWorking: {self._apply_player(uid)}\n```")
        lines.append("Add more with the button below: `X=Y` to map, `X=` to clear "
                     "(comma or newline separated). Or type the full quote to solve.")
        return "\n".join(lines)


def _parse_tokens(raw: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ([(cipher, plain_or_empty)], [bad_tokens])."""
    pairs, bad = [], []
    for tok in re.split(r"[,\s]+", raw.strip()):
        if not tok:
            continue
        m = _MP_TOKEN_RE.match(tok)
        if not m:
            bad.append(tok)
            continue
        pairs.append((m.group(1).upper(), m.group(2).upper()))
    return pairs, bad


class _MappingModal(discord.ui.Modal, title="Crack the cipher"):
    def __init__(self, game: _MPCryptoGame):
        super().__init__()
        self.game = game
        self.maps = discord.ui.TextInput(
            label="Letter mappings (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
            placeholder="e.g.  L=T, A=E, R=S   ( X= clears one  •  type  reset  to clear all )",
        )
        self.full = discord.ui.TextInput(
            label="OR full decoded quote (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
            placeholder="Type the entire decoded sentence to solve outright",
        )
        self.add_item(self.maps)
        self.add_item(self.full)

    async def on_submit(self, interaction: discord.Interaction):
        await self.game.process(interaction, self.maps.value, self.full.value)


class _WorkspaceView(discord.ui.View):
    """A single 'open my workspace / continue solving' button. Reused for the public
    launcher and each player's private ephemeral follow-ups."""

    def __init__(self, game: _MPCryptoGame):
        super().__init__(timeout=MP_TIMEOUT)
        self.game = game

    @discord.ui.button(label="Work on puzzle", style=discord.ButtonStyle.primary, emoji="🔓")
    async def work(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id not in self.game.player_ids:
            await interaction.response.send_message(
                "👀 You're spectating — only players can solve.", ephemeral=True
            )
            return
        if self.game.finished.is_set():
            await interaction.response.send_message("This round is over.", ephemeral=True)
            return
        await interaction.response.send_modal(_MappingModal(self.game))


def _attach_process(game: _MPCryptoGame):
    async def process(interaction: discord.Interaction, maps_raw: str, full_raw: str):
        async with game._lock:
            if game.finished.is_set():
                await interaction.response.send_message("Too late — someone solved it.", ephemeral=True)
                return

            # Full-solution path takes priority
            if full_raw and _normalize(full_raw) == game.target_normalized:
                game.winner_id = interaction.user.id
                game.finished.set()
                await interaction.response.send_message(
                    f"✅ Correct! You cracked it.", ephemeral=True
                )
                return

            note = ""
            if maps_raw.strip().lower() == "reset":
                game._player_map(interaction.user.id).clear()
                await interaction.response.send_message(
                    game._workspace_text(interaction.user.id, "🔄 Board reset — all your mappings cleared."),
                    view=_WorkspaceView(game),
                    ephemeral=True,
                )
                return
            if maps_raw.strip():
                pairs, bad = _parse_tokens(maps_raw)
                pm = game._player_map(interaction.user.id)
                for c_letter, p_letter in pairs:
                    if c_letter not in game.cipher_letters:
                        continue  # ignore letters not in the cipher
                    if p_letter == "":
                        pm.pop(c_letter, None)
                    else:
                        pm[c_letter] = p_letter
                if bad:
                    note = f"⚠️ Ignored unparseable: `{', '.join(bad[:5])}`"
            elif full_raw:
                note = "❌ That full guess wasn't right — keep working."

            if game._is_solved(interaction.user.id):
                game.winner_id = interaction.user.id
                game.finished.set()
                await interaction.response.send_message(
                    "✅ Your mapping fully decodes the quote — you win!", ephemeral=True
                )
                return

            await interaction.response.send_message(
                game._workspace_text(interaction.user.id, note),
                view=_WorkspaceView(game),
                ephemeral=True,
            )

    game.process = process  # type: ignore[attr-defined]


async def start_multi(thread, players, bot):
    quotes = _load_quotes()
    chosen = random.choice(quotes)
    plaintext = chosen["text"]
    author = chosen["author"]
    cipher = _make_cipher()
    decipher = {v: k for k, v in cipher.items()}
    encoded = _encode(plaintext, cipher)
    cipher_letters = {c for c in encoded if c.isalpha()}
    target_mapping = {c: decipher[c] for c in cipher_letters}

    game = _MPCryptoGame(
        players, encoded, target_mapping, _normalize(plaintext), plaintext, author
    )
    _attach_process(game)

    names = ", ".join(p.display_name for p in players)
    await thread.send(
        f"**Cryptograms — MP Race**\n"
        f"Same cipher for everyone. Click **Work on puzzle** to open your **private** "
        f"workspace and map letters one at a time (just like single player) — nobody else "
        f"sees your progress. First to fully crack it wins. **{MP_TIMEOUT // 60} min** limit.\n"
        f"Spaces and punctuation are unchanged. No letter maps to itself.\n"
        f"Players: {names}\n\n```{encoded}```",
        view=_WorkspaceView(game),
    )

    try:
        await asyncio.wait_for(game.finished.wait(), timeout=MP_TIMEOUT)
    except asyncio.TimeoutError:
        await thread.send(f"⏱ Time's up! The quote was:\n> {plaintext}\n— **{author}**")
        return

    if game.winner_id is not None:
        winner = game.players_by_id[game.winner_id]
        await thread.send(f"🏆 **{winner.display_name}** solved it!\n> {plaintext}\n— **{author}**")
