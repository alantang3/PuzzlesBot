"""
Refresh games/data/trends.json with live Google Trends scores.

Two ways to use this:
    1. From the command line:  python scripts/build_trends_dataset.py
    2. From the bot:            from scripts.build_trends_dataset import refresh_trends_dataset

The bot calls refresh_trends_dataset() on a 12-hour loop; you can also run
this manually any time you want fresh numbers right now.

Requires:  pip install pytrends
Google Trends rate-limits aggressively — bump BATCH_DELAY if you hit 429s.
"""

import json
import time
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "games" / "data" / "trends.json"
BASELINE = "youtube"      # anchor term in every batch
BASELINE_SCORE = 96       # what we'll rescale the baseline to in the output
BATCH_SIZE = 5            # pytrends allows up to 5 terms per request
BATCH_DELAY = 3.0         # seconds between requests (raise if rate-limited)
TIMEFRAME = "today 12-m"


def _load():
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def refresh_trends_dataset(verbose: bool = True) -> int:
    """
    Re-query Google Trends for every term in the dataset and overwrite the
    scores. Returns the number of terms successfully updated.

    Raises ImportError if pytrends isn't installed.
    """
    from pytrends.request import TrendReq  # imported lazily so bot startup doesn't require it

    data = _load()
    terms = list(data["terms"].keys())
    others = [t for t in terms if t != BASELINE]

    pytrends = TrendReq(hl="en-US", tz=0)
    fresh = {}

    for i in range(0, len(others), BATCH_SIZE - 1):
        batch = [BASELINE] + others[i : i + BATCH_SIZE - 1]
        if verbose:
            print(f"  Querying {batch} ...")
        try:
            pytrends.build_payload(batch, timeframe=TIMEFRAME)
            df = pytrends.interest_over_time()
        except Exception as e:
            if verbose:
                print(f"  ! batch failed ({e}); skipping")
            time.sleep(BATCH_DELAY * 2)
            continue

        if df.empty:
            continue

        means = df.drop(columns=["isPartial"], errors="ignore").mean()
        baseline_mean = means.get(BASELINE, 0)
        if baseline_mean <= 0:
            continue

        scale = BASELINE_SCORE / baseline_mean
        for term, mean in means.items():
            fresh[term] = max(0, min(100, round(float(mean) * scale)))
        time.sleep(BATCH_DELAY)

    if not fresh:
        raise RuntimeError("Google Trends returned no data — likely rate-limited.")

    fresh[BASELINE] = BASELINE_SCORE
    data["terms"] = {t: fresh.get(t, data["terms"][t]) for t in terms}
    _save(data)
    if verbose:
        print(f"Wrote {len(fresh)} fresh scores to {DATA_PATH}")
    return len(fresh)


if __name__ == "__main__":
    refresh_trends_dataset()
