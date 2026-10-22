#!/usr/bin/env python3
"""Backtest of Trendline Break Short on 5-minute candles (F&O stocks).

  TRENDLINE_BACKTEST_MONTHS=12 python scripts/run_trendline_backtest.py
  # smoke test the Upstox 5-minute fetch on a handful of symbols first:
  TRENDLINE_BACKTEST_MONTHS=1 TRENDLINE_SYMBOL_LIMIT=5 python scripts/run_trendline_backtest.py

Intended to be run through .github/workflows/trendline_backtest.yml, which
does the Upstox TOTP login. The full summary goes to the Actions log (the
trade-by-trade CSV is also uploaded as an artifact); a shortened version goes
to Telegram.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, runtime, trendline_backtest  # noqa: E402
from signals.telegram import send_message  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402
from signals import config  # noqa: E402

TITLE = "Trendline Break Short (5-min) - backtest"
EMOJI = "📉"
CSV_PATH = "trendline_backtest.csv"


def _describe(intraday: dict) -> str:
    """Shape of what Upstox returned - the first live run is also the first
    check of the 5-minute fetch, so make wrong assumptions visible in the log."""
    lines = [f"Fetched 5-minute candles for {len(intraday)} symbols."]
    for symbol in sorted(intraday)[:3]:
        df = intraday[symbol]
        per_day = df.groupby(df.index.normalize()).size()
        lines.append(
            f"  {symbol}: {len(df)} bars, {df.index[0]} -> {df.index[-1]}, "
            f"{per_day.median():.0f} bars/day median (full session = 75), "
            f"first bar of last day {df.index[df.index.normalize() == df.index[-1].normalize()][0].time()}"
        )
    return "\n".join(lines)


def main() -> None:
    runtime.setup_logging()
    months = int(os.environ.get("TRENDLINE_BACKTEST_MONTHS", config.TRENDLINE_HISTORY_MONTHS))
    limit = int(os.environ.get("TRENDLINE_SYMBOL_LIMIT", "0"))
    token = runtime.get_env("TELEGRAM_BOT_TOKEN")
    chat_id = runtime.get_env("TELEGRAM_CHAT_ID")

    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        symbols = sorted(data.fetch_fo_eligible_symbols(client))
        if limit:
            symbols = symbols[:limit]
        instrument_map = data.build_instrument_map(client, symbols)
        intraday = data.fetch_intraday(client, instrument_map, months)
        print(_describe(intraday))
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise

    trades = trendline_backtest.run_trendline_backtest(intraday, months)
    summary = trendline_backtest.summarize_trendline(trades, months)
    print(f"\n=== {TITLE} ===\n{summary}")
    # Telegram gets the headline and the width test, not the per-feature tables.
    send_message(token, chat_id, f"{EMOJI} <b>{TITLE}</b>\n{summary.split('<b>By feature</b>')[0].rstrip()}")
    trendline_backtest.write_trendline_csv(trades, CSV_PATH)
    print(f"Wrote {len(trades)} trades to {CSV_PATH}")


if __name__ == "__main__":
    main()
