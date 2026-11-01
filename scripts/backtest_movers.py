"""Backtest of the Swing tab's movers watchlist.

  python scripts/backtest_movers.py [swing_candles.json swing.json]

Without paths it downloads both files from the dashboard-swing branch (encrypted files need DASHBOARD_PASSWORD set).
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import movers_backtest, secure  # noqa: E402

BASE = "https://raw.githubusercontent.com/sapanz/nifty-trading-screener/dashboard-swing/"


def _load(path_or_name: str, local: bool):
    if local:
        return secure.loads(open(path_or_name).read())
    return secure.loads(urllib.request.urlopen(BASE + path_or_name, timeout=60).read())


def main() -> None:
    local = len(sys.argv) > 2
    candles = _load(sys.argv[1] if local else "swing_candles.json", local)
    snapshot = _load(sys.argv[2] if local else "swing.json", local)
    print(movers_backtest.summarize(candles, {r["symbol"]: r.get("turnover_cr") or 0 for r in snapshot["stocks"]}))


if __name__ == "__main__":
    main()
