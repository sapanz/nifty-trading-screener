#!/usr/bin/env python3
"""Backtest the swing R-factor from the saved store (no Upstox needed).

  python scripts/backtest_swing.py [swing_oi.json]

Without a path it downloads swing_oi.json from the dashboard-swing branch.
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import swing_backtest  # noqa: E402

URL = "https://raw.githubusercontent.com/sapanz/nifty-trading-screener/dashboard-swing/swing_oi.json"


def main() -> None:
    store = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(urllib.request.urlopen(URL))
    results = swing_backtest.run_backtest(store)
    print(swing_backtest.summarize(results))


if __name__ == "__main__":
    main()
