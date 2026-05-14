import asyncio
import io
import random
import re

import aiohttp
import discord

MAX_GUESSES = 3
GUESS_TIMEOUT = 45
ROUNDS = 5
MAX_POKEMON_ID = 1025  # current Pokédex national-ID ceiling; safe to fetch
INTERMISSION = 3
POKEAPI = "https://pokeapi.co/api/v2/pokemon/{id}"


def _normalize(name: str) -> str:
    """Lowercase, strip non-alphanumeric. 'Mr. Mime' → 'mrmime'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


async def _fetch_pokemon(session: aiohttp.ClientSession, pokemon_id: int) -> dict:
    async with session.get(POKEAPI.format(id=pokemon_id), timeout=15) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _fetch_sprite(session: aiohttp.ClientSession, url: str) -> bytes:
    async with session.get(url, timeout=15) as resp:
        resp.raise_for_status()
        return await resp.read()


def _make_silhouette(image_bytes: bytes) -> io.BytesIO:
    """Convert non-transparent pixels of a PNG sprite to solid black."""
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("Pillow not installed — pip install pillow")
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = pixels[x, y]
            if a > 0:
                pixels[x, y] = (0, 0, 0, a)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def _pick_pokemon(session: aiohttp.ClientSession) -> dict | None:
    """Pick a random Pokémon that actually has a usable front sprite."""
    for _ in range(5):  # retry a few times if a Pokémon has no sprite
        pid = random.randint(1, MAX_POKEMON_ID)
        try:
            data = await _fetch_pokemon(session, pid)
        except Exception:
            continue
        sprite = data.get("sprites", {}).get("front_default")
        if sprite:
            return data
    return None


async def start(thread, user, bot):
    try:
        from PIL import Image  # noqa: F401 — early import to fail fast
    except ImportError:
        await thread.send(
            "Pillow isn't installed on the bot host — run `pip install pillow` and restart."
        )
        return

    score = 0
    await thread.send(
        f"**Who's That Pokémon?** — {ROUNDS} rounds. You'll see a silhouette; "
        f"type the Pokémon's name (you get **{MAX_GUESSES}** guesses per round). "
        "Capitalization and punctuation don't matter."
    )

    async with aiohttp.ClientSession() as session:
        for round_num in range(1, ROUNDS + 1):
            pokemon = await _pick_pokemon(session)
            if pokemon is None:
                await thread.send("Couldn't reach PokéAPI right now. Round cancelled.")
                break

            name = pokemon["name"]
            sprite_url = pokemon["sprites"]["front_default"]
            normalized_target = _normalize(name)

            try:
                sprite_bytes = await _fetch_sprite(session, sprite_url)
                silhouette = _make_silhouette(sprite_bytes)
            except Exception as e:
                await thread.send(f"Image error: `{e}`. Round cancelled.")
                break

            embed = discord.Embed(title=f"Round {round_num}/{ROUNDS}", description="Who's that Pokémon?")
            embed.set_image(url="attachment://silhouette.png")
            file = discord.File(silhouette, filename="silhouette.png")
            await thread.send(embed=embed, file=file)

            def is_guess(msg):
                return (
                    msg.author.id == user.id
                    and msg.channel.id == thread.id
                    and bool(msg.content.strip())
                )

            solved = False
            for attempt in range(1, MAX_GUESSES + 1):
                try:
                    msg = await bot.wait_for("message", check=is_guess, timeout=GUESS_TIMEOUT)
                except asyncio.TimeoutError:
                    await thread.send(f"⏱ Out of time! It was **{name.title()}**.")
                    break

                if _normalize(msg.content) == normalized_target:
                    score += 1
                    solved = True
                    reveal = discord.Embed(title=f"It's {name.title()}! 🎉")
                    reveal.set_image(url=sprite_url)
                    await thread.send(
                        f"✅ Got it in {attempt} {'try' if attempt == 1 else 'tries'}! Score: **{score}/{round_num}**.",
                        embed=reveal,
                    )
                    break

                remaining = MAX_GUESSES - attempt
                if remaining == 0:
                    reveal = discord.Embed(title=f"It was {name.title()}.")
                    reveal.set_image(url=sprite_url)
                    await thread.send(
                        f"❌ Out of guesses. Score: **{score}/{round_num}**.",
                        embed=reveal,
                    )
                else:
                    await thread.send(f"Nope. **{remaining}** {'try' if remaining == 1 else 'tries'} left.")

            if round_num < ROUNDS:
                await asyncio.sleep(INTERMISSION)

    if score is not None:
        pct = round(100 * score / ROUNDS)
        await thread.send(f"**Final score: {score}/{ROUNDS} ({pct}%)**")


# -----------------------------------------------------------------------------
# Multiplayer race mode
# -----------------------------------------------------------------------------

MP_ROUND_TIMEOUT = 30


class _PokemonGuessModal(discord.ui.Modal, title="Who's that Pokémon?"):
    def __init__(self, view: "_MPPokemonView"):
        super().__init__()
        self.view_ref = view
        self.guess = discord.ui.TextInput(label="Pokémon name", max_length=32, placeholder="e.g. Pikachu")
        self.add_item(self.guess)

    async def on_submit(self, interaction: discord.Interaction):
        await self.view_ref.handle_guess(interaction, self.guess.value)


class _MPPokemonView(discord.ui.View):
    def __init__(self, player_ids: set[int], target: str):
        super().__init__(timeout=MP_ROUND_TIMEOUT)
        self.player_ids = player_ids
        self.target = target  # normalized
        self.attempts: dict[int, int] = {}
        self.winner_id: int | None = None
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary, emoji="✏️")
    async def guess_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if interaction.user.id not in self.player_ids:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        if self.attempts.get(interaction.user.id, 0) >= MAX_GUESSES:
            await interaction.response.send_message("You're out of guesses for this round.", ephemeral=True)
            return
        if self.finished.is_set():
            await interaction.response.send_message("Round's over.", ephemeral=True)
            return
        await interaction.response.send_modal(_PokemonGuessModal(self))

    async def handle_guess(self, interaction: discord.Interaction, raw: str):
        async with self._lock:
            if self.finished.is_set():
                await interaction.response.send_message("Too late.", ephemeral=True)
                return
            if _normalize(raw) == self.target:
                self.winner_id = interaction.user.id
                await interaction.response.send_message("✅ You got it first!", ephemeral=True)
                for child in self.children:
                    child.disabled = True
                self.finished.set()
                return
            self.attempts[interaction.user.id] = self.attempts.get(interaction.user.id, 0) + 1
            left = MAX_GUESSES - self.attempts[interaction.user.id]
            if left <= 0:
                await interaction.response.send_message("❌ Out of guesses.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"❌ Wrong — **{left}** {'guess' if left == 1 else 'guesses'} left.", ephemeral=True
                )


async def start_multi(thread, players, bot):
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        await thread.send("Pillow isn't installed — `pip install pillow`.")
        return

    player_ids = {p.id for p in players}
    players_by_id = {p.id: p for p in players}
    scores = {pid: 0 for pid in player_ids}

    names = ", ".join(p.display_name for p in players)
    await thread.send(
        f"**Who's That Pokémon? — MP Race**\n"
        f"{ROUNDS} rounds. Click **Guess** and type the name. First correct wins the round. "
        f"**{MAX_GUESSES}** guesses each, **{MP_ROUND_TIMEOUT}s** per round.\nPlayers: {names}"
    )

    async with aiohttp.ClientSession() as session:
        for round_num in range(1, ROUNDS + 1):
            pokemon = await _pick_pokemon(session)
            if pokemon is None:
                await thread.send("Couldn't reach PokéAPI. Stopping.")
                break

            name = pokemon["name"]
            sprite_url = pokemon["sprites"]["front_default"]
            normalized_target = _normalize(name)

            try:
                sprite_bytes = await _fetch_sprite(session, sprite_url)
                silhouette = _make_silhouette(sprite_bytes)
            except Exception as e:
                await thread.send(f"Image error: `{e}`. Stopping.")
                break

            scoreboard = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
            embed = discord.Embed(title=f"Round {round_num}/{ROUNDS}", description="Who's that Pokémon?")
            embed.set_image(url="attachment://silhouette.png")
            file = discord.File(silhouette, filename="silhouette.png")
            view = _MPPokemonView(player_ids, normalized_target)
            await thread.send(content=scoreboard, embed=embed, file=file, view=view)
            try:
                await asyncio.wait_for(view.finished.wait(), timeout=MP_ROUND_TIMEOUT)
            except asyncio.TimeoutError:
                pass

            reveal = discord.Embed(title=f"It was {name.title()}.")
            reveal.set_image(url=sprite_url)
            if view.winner_id is not None:
                scores[view.winner_id] += 1
                winner = players_by_id[view.winner_id]
                await thread.send(f"✅ **{winner.display_name}** got it!", embed=reveal)
            else:
                await thread.send(f"⏱ No one got it.", embed=reveal)

            if round_num < ROUNDS:
                await asyncio.sleep(INTERMISSION)

    max_score = max(scores.values())
    winners = [players_by_id[pid] for pid, s in scores.items() if s == max_score]
    final = " | ".join(f"{players_by_id[pid].display_name}: **{scores[pid]}**" for pid in player_ids)
    if len(winners) == 1:
        await thread.send(f"🏆 **{winners[0].display_name}** wins!\n{final}")
    else:
        names = ", ".join(w.display_name for w in winners)
        await thread.send(f"🤝 Tie between **{names}**!\n{final}")
