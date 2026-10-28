#!/usr/bin/env python3
"""Backtest the swing R-factor from the saved store (no Upstox needed).

  python scripts/backtest_swing.py [swing_oi.json]

Without a path it downloads swing_oi.json from the dashboard-swing branch.
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import secure, swing_backtest  # noqa: E402

URL = "https://raw.githubusercontent.com/sapanz/nifty-trading-screener/dashboard-swing/swing_oi.json"


def main() -> None:
    store = secure.loads(open(sys.argv[1]).read()) if len(sys.argv) > 1 else secure.loads(urllib.request.urlopen(URL).read())
    results = swing_backtest.run_backtest(store)
    print(swing_backtest.summarize(results))


if __name__ == "__main__":
    main()
