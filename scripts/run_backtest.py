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
run (order of 10-20 minutes, not seconds). Four separate Upstox fetches,
same split as a live run: daily (DAILY_HISTORY_YEARS) for Daily Swing,
native-weekly (WEEKLY_HISTORY_YEARS) for Weekly Range Breakout,
native-monthly (MONTHLY_ATH_HISTORY_YEARS) for Monthly ATH Breakout's
all-time-high check, and a ~210-symbol F&O futures fetch for Futures OI
Buildup - that last one has an inherently short backtest window (a
futures contract only carries its own ~2-3 month history; see
tools/debug_futures.py), so its results here are a smoke test, not the
same kind of multi-year validation the other three strategies get.
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
    "daily_swing": ("Daily Swing (SMA44/BB Confluence)", "📈"),
    "futures_oi": ("Futures OI Buildup", "⚡"),
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
        # Weekly Range Breakout and Monthly ATH Breakout each get their own
        # native fetch rather than a resample of `daily` above - weekly so
        # its candles match live Upstox week boundaries, monthly because
        # its all-time-high check needs much deeper history than
        # DAILY_HISTORY_YEARS (see WEEKLY_HISTORY_YEARS /
        # MONTHLY_ATH_HISTORY_YEARS in config.py).
        weekly = data.fetch_weekly_history(client, instrument_map)
        monthly = data.fetch_monthly_ath_history(client, instrument_map)
        # Futures OI Buildup gets its own F&O instrument map + fetch, not a
        # resample of `daily` - only ~210 of Nifty 500 have a futures
        # contract at all, and OI simply doesn't exist on the equity series.
        futures_map = data.build_futures_instrument_map(client, symbols)
        futures = data.fetch_futures_daily(client, futures_map)
        results = backtest.run_backtest(
            daily, months=months, weekly_data=weekly, monthly_data=monthly, futures_data=futures,
        )
    except Exception as exc:
        runtime.notify_error(FETCH_TITLE, FETCH_EMOJI, str(exc))
        raise

    send_message(token, chat_id, f"🧪 <b>Backtest results — last {months} month(s)</b>")

    all_trades = []
    for strategy, trades in results.items():
        title, emoji = STRATEGY_LABELS[strategy]
        summary = backtest.summarize(trades)
        send_message(token, chat_id, f"{emoji} <b>{title}</b>\n{summary}")
        print(f"\n=== {title} ===\n{summary}")  # also visible in the Actions run log, not just Telegram
        all_trades.extend(trades)

    backtest.write_csv(all_trades, CSV_PATH)
    print(f"Wrote {len(all_trades)} trades to {CSV_PATH}")


if __name__ == "__main__":
    main()
