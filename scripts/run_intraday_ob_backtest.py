#!/usr/bin/env python3
"""Backtest of 5-minute order-block retests on F&O stocks (the dashboard's intraday list).

  INTRADAY_OB_MONTHS=12 python scripts/run_intraday_ob_backtest.py
  INTRADAY_OB_MONTHS=1 INTRADAY_OB_SYMBOL_LIMIT=5 python scripts/run_intraday_ob_backtest.py   # smoke test

Run through .github/workflows/intraday_ob_backtest.yml (Upstox TOTP login first).
"""
import dataclasses
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, intraday_ob_backtest as ib, runtime  # noqa: E402
from signals.telegram import send_message  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

TITLE = "Order block retest (5-min, intraday) - backtest"
EMOJI = "🧱"
CSV_PATH = "intraday_ob_backtest.csv"


def main() -> None:
    runtime.setup_logging()
    months = int(os.environ.get("INTRADAY_OB_MONTHS", "12"))
    limit = int(os.environ.get("INTRADAY_OB_SYMBOL_LIMIT", "0"))
    token = runtime.get_env("TELEGRAM_BOT_TOKEN")
    chat_id = runtime.get_env("TELEGRAM_CHAT_ID")
    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        symbols = sorted(data.fetch_fo_eligible_symbols(client))
        if limit:
            symbols = symbols[:limit]
        intraday = data.fetch_intraday(client, data.build_instrument_map(client, symbols), months)
        print(f"Fetched 5-minute candles for {len(intraday)} symbols.")
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise
    trades, arrays = ib.run_intraday_backtest(intraday)
    summary = ib.summarize(trades, ib.random_baseline(trades, arrays))
    print(f"\n=== {TITLE} ===\n{summary}")
    send_message(token, chat_id, f"{EMOJI} <b>{TITLE}</b>\n{summary.split('<b>By entry time</b>')[0].rstrip()}")
    with open(CSV_PATH, "w", newline="") as handle:
        if trades:
            writer = csv.DictWriter(handle, fieldnames=[f.name for f in dataclasses.fields(trades[0])])
            writer.writeheader()
            for trade in trades:
                writer.writerow(dataclasses.asdict(trade))
    print(f"Wrote {len(trades)} trades to {CSV_PATH}")


if __name__ == "__main__":
    main()
