#!/usr/bin/env python3
"""One-off historical backtest of all active strategies over a lookback window.

For each historical date, reconstructs what each strategy would have
signalled using only data available up to that date (the live scan()
functions, unmodified - no separate backtest logic to drift out of sync),
then walks the real subsequent daily price action forward to see whether
each trade would have hit a target or its stop-loss first.

Usage:
  BACKTEST_MONTHS=3 python scripts/run_backtest.py

Requires the same env vars as scripts/run_signals.py (UPSTOX_ACCESS_TOKEN,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Intended to be triggered manually via
.github/workflows/backtest.yml, which handles the Upstox TOTP login and
uploads the full trade-by-trade CSV as a workflow artifact.

This fetches the same ~500-symbol universe as a live run and then re-scans
it once per historical date, so it takes noticeably longer than a normal
run (order of 10-20 minutes, not seconds).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import backtest, data, runtime, universe  # noqa: E402
from signals.telegram import send_message  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

CSV_PATH = "backtest_trades.csv"
FETCH_TITLE = "Backtest (data fetch)"
FETCH_EMOJI = "🧪"

STRATEGY_LABELS = {
    "daily_swing": ("Daily Swing (Darvas Box)", "📈"),
    "cip_weekly": ("CIP Weekly", "🔄"),
    "weekly_breakout": ("Weekly Range Breakout", "🚀"),
    "monthly_breakout": ("Monthly ATH Breakout", "🏔️"),
}


def main() -> None:
    runtime.setup_logging()
    months = int(os.environ.get("BACKTEST_MONTHS", "3"))

    token = runtime.get_env("TELEGRAM_BOT_TOKEN")
    chat_id = runtime.get_env("TELEGRAM_CHAT_ID")

    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        symbols = universe.fetch_nifty500_symbols()
        instrument_map = data.build_instrument_map(client, symbols)
        daily = data.fetch_daily(client, instrument_map)
        results = backtest.run_backtest(daily, months=months)
    except Exception as exc:
        runtime.notify_error(FETCH_TITLE, FETCH_EMOJI, str(exc))
        raise

    send_message(token, chat_id, f"🧪 <b>Backtest results — last {months} month(s)</b>")

    all_trades = []
    for strategy, trades in results.items():
        title, emoji = STRATEGY_LABELS[strategy]
        send_message(token, chat_id, f"{emoji} <b>{title}</b>\n{backtest.summarize(trades)}")
        all_trades.extend(trades)

    backtest.write_csv(all_trades, CSV_PATH)
    print(f"Wrote {len(all_trades)} trades to {CSV_PATH}")


if __name__ == "__main__":
    main()
