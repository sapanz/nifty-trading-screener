#!/usr/bin/env python3
"""Backtest R-factor on the Replay day files (no Upstox needed).

  python scripts/backtest_rfactor.py [DIR]

DIR holds the day files (YYYY-MM-DD.json); without it they're downloaded from
the dashboard-history branch of the repo.
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import rfactor, secure  # noqa: E402

BASE = "https://raw.githubusercontent.com/sapanz/nifty-trading-screener/dashboard-history/"
TIMES = ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00"]


def _load(directory: str | None) -> list[dict]:
    if directory:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json") and n != "index.json")
        return [secure.loads(open(os.path.join(directory, n)).read()) for n in names]
    dates = secure.loads(urllib.request.urlopen(BASE + "index.json").read())["dates"]
    return [secure.loads(urllib.request.urlopen(f"{BASE}days/{d}.json").read()) for d in dates]


def main() -> None:
    days = _load(sys.argv[1] if len(sys.argv) > 1 else None)
    results = rfactor.run_backtest(days, TIMES, top_n=5)
    print(rfactor.summarize(results, len(days), TIMES, 5, 0.10))


if __name__ == "__main__":
    main()
