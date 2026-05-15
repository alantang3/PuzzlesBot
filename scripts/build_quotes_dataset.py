"""
Grow games/data/cryptogram_quotes.json with quotes from free public APIs.

Usage:
    python scripts/build_quotes_dataset.py                # add as many as it can
    python scripts/build_quotes_dataset.py 500            # stop after ~500 new quotes

No API key needed. Sources (tried in order, best-effort):
  1. quotable.io   — paginated, clean author + length metadata
  2. type.fit      — one bulk JSON of ~1600 quotes (fallback)

Only quotes that work as cryptograms are kept:
  - plain ASCII after normalizing smart quotes / dashes (the substitution
    cipher only handles A-Z; accented/non-Latin text is skipped)
  - length between MIN_LEN and MAX_LEN (solvable but not a wall of text)
  - enough letters to allow pattern/frequency analysis
  - de-duplicated against everything already in the file

The script is idempotent: run it again any time to top up. It never
removes or rewrites existing quotes, only appends new unique ones.
"""

import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "games" / "data" / "cryptogram_quotes.json"

MIN_LEN = 24
MAX_LEN = 180
MIN_LETTERS = 15
USER_AGENT = "PuzzlesBot-quote-builder/1.0"

_SMART = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "―": "-",
    "…": "...", " ": " ", "′": "'", "″": '"',
}


def _normalize_text(s: str) -> str:
    for bad, good in _SMART.items():
        s = s.replace(bad, good)
    return " ".join(s.split()).strip()


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _is_suitable(text: str) -> bool:
    if not (MIN_LEN <= len(text) <= MAX_LEN):
        return False
    if any(ord(c) > 126 for c in text):  # non-ASCII survived normalization
        return False
    if sum(c.isalpha() for c in text) < MIN_LETTERS:
        return False
    return True


def _http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        # Some hosts have flaky certs; retry once without verification.
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))


def _from_quotable(limit_new: int, have: set[str], verbose: bool):
    out = []
    page = 1
    while len(out) < limit_new:
        url = (
            f"https://api.quotable.io/quotes?limit=150&page={page}"
            f"&maxLength={MAX_LEN}&minLength={MIN_LEN}"
        )
        try:
            data = _http_json(url)
        except Exception as e:
            if verbose:
                print(f"  quotable page {page} failed ({e}); stopping that source")
            break
        results = data.get("results") or []
        if not results:
            break
        for item in results:
            text = _normalize_text(item.get("content", ""))
            author = (item.get("author") or "Unknown").strip()
            if not _is_suitable(text):
                continue
            k = _key(text)
            if k in have:
                continue
            have.add(k)
            out.append({"text": text, "author": author})
            if len(out) >= limit_new:
                break
        if page >= int(data.get("totalPages", page)):
            break
        page += 1
    if verbose:
        print(f"  quotable: +{len(out)}")
    return out


def _from_typefit(limit_new: int, have: set[str], verbose: bool):
    out = []
    try:
        data = _http_json("https://type.fit/api/quotes")
    except Exception as e:
        if verbose:
            print(f"  type.fit failed ({e})")
        return out
    for item in data:
        text = _normalize_text(item.get("text", ""))
        author = (item.get("author") or "Unknown")
        author = author.split(",")[0].strip() or "Unknown"
        if not _is_suitable(text):
            continue
        k = _key(text)
        if k in have:
            continue
        have.add(k)
        out.append({"text": text, "author": author})
        if len(out) >= limit_new:
            break
    if verbose:
        print(f"  type.fit: +{len(out)}")
    return out


def build_quotes_dataset(target_new: int = 100_000, verbose: bool = True) -> int:
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    quotes = data["quotes"]
    have = {_key(q["text"]) for q in quotes}
    before = len(quotes)

    added = []
    added += _from_quotable(target_new - len(added), have, verbose)
    if len(added) < target_new:
        added += _from_typefit(target_new - len(added), have, verbose)

    if not added:
        if verbose:
            print("No new quotes found (sources unreachable or all duplicates).")
        return 0

    quotes.extend(added)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)
    if verbose:
        print(f"Added {len(added)} quotes. {before} -> {len(quotes)}.")
    return len(added)


if __name__ == "__main__":
    cap = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 100_000
    build_quotes_dataset(target_new=cap)
