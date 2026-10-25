#!/usr/bin/env python3
"""Backtest of order-block retests on daily candles (Nifty 500; shorts only on F&O stocks).

  python scripts/run_orderblock_backtest.py

Intended to be run through .github/workflows/orderblock_backtest.yml (Upstox
TOTP login first). The full summary goes to the Actions log, the trade CSV is
uploaded as an artifact, a shortened version goes to Telegram.
"""
import csv
import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, orderblock_backtest, runtime, universe  # noqa: E402
from signals.telegram import send_message  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

TITLE = "Order block retest (daily) - backtest"
EMOJI = "🧱"
CSV_PATH = "orderblock_backtest.csv"


def write_csv(trades, path: str) -> None:
    with open(path, "w", newline="") as handle:
        if not trades:
            return
        writer = csv.DictWriter(handle, fieldnames=[f.name for f in dataclasses.fields(trades[0])])
        writer.writeheader()
        for trade in trades:
            writer.writerow(dataclasses.asdict(trade))


def main() -> None:
    runtime.setup_logging()
    token = runtime.get_env("TELEGRAM_BOT_TOKEN")
    chat_id = runtime.get_env("TELEGRAM_CHAT_ID")
    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        symbols = universe.fetch_nifty500_symbols()
        instrument_map = data.build_instrument_map(client, symbols)
        daily = data.fetch_daily(client, instrument_map)
        fo_symbols = data.fetch_fo_eligible_symbols(client)
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise

    trades, arrays = orderblock_backtest.run_orderblock_backtest(daily, short_eligible=fo_symbols)
    baseline = orderblock_backtest.random_baseline(trades, arrays)
    summary = orderblock_backtest.summarize(trades, baseline)
    print(f"\n=== {TITLE} ===\n{summary}")
    send_message(token, chat_id, f"{EMOJI} <b>{TITLE}</b>\n{summary.split('<b>By feature</b>')[0].rstrip()}")
    write_csv(trades, CSV_PATH)
    print(f"Wrote {len(trades)} trades to {CSV_PATH}")


if __name__ == "__main__":
    main()
