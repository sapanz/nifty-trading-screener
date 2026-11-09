#!/usr/bin/env python3
"""Backtest of the weekly 30 SMA + daily 44 SMA pullback strategy (signals/ma44_backtest.py) on F&O stocks.

  MA44_SIDE=short MA44_YEARS=6 python scripts/run_ma44_backtest.py
  MA44_SIDE=short MA44_YEARS=2 MA44_SYMBOL_LIMIT=10 python scripts/run_ma44_backtest.py   # smoke test

Run through .github/workflows/ma44_backtest.yml (Upstox TOTP login first).
"""
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, ma44_backtest as bt, runtime  # noqa: E402
from signals.telegram import send_message  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

TITLE = "Weekly 30 SMA + daily 44 SMA pullback - backtest"
EMOJI = "📉"
CSV_PATH = "ma44_backtest_trades.csv"


def main() -> None:
    runtime.setup_logging()
    side = os.environ.get("MA44_SIDE", "short").lower()
    years = int(os.environ.get("MA44_YEARS", "6"))
    limit = int(os.environ.get("MA44_SYMBOL_LIMIT", "0"))
    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        symbols = sorted(data.fetch_fo_eligible_symbols(client))
        if limit:
            symbols = symbols[:limit]
        frames = data._fetch_history(data.build_instrument_map(client, symbols), fetch_one=lambda key: client.get_daily_history(key, years=years),
                                     throttle=client.throttle, label="daily")
        print(f"Fetched daily candles for {len(frames)} F&O stocks ({years} years requested).")
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise
    summary = bt.summarize(frames, side)
    print(f"\n=== {TITLE} ===\n{summary}")
    try:
        send_message(runtime.get_env("TELEGRAM_BOT_TOKEN"), runtime.get_env("TELEGRAM_CHAT_ID"), f"{EMOJI} <b>{TITLE}</b>\n<pre>{html.escape(summary[:3300])}</pre>")
    except Exception as exc:  # noqa: BLE001 - the log has the full summary; a Telegram hiccup must not fail the run
        print(f"telegram skipped: {exc}")
    trades = bt.run(frames, bt.Params(side=side))
    trades.to_csv(CSV_PATH, index=False)
    print(f"Wrote {len(trades)} trades to {CSV_PATH}")


if __name__ == "__main__":
    main()
