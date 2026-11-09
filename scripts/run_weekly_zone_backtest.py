#!/usr/bin/env python3
"""Backtest of weekly demand / supply zones traded when the daily close comes back out of the zone (signals/weekly_zone_backtest.py), on F&O stocks.

  WZ_SIDE=both WZ_YEARS=6 python scripts/run_weekly_zone_backtest.py
  WZ_SIDE=both WZ_YEARS=6 WZ_SYMBOL_LIMIT=6 python scripts/run_weekly_zone_backtest.py   # smoke test

Run through .github/workflows/weekly_zone_backtest.yml (Upstox TOTP login first).
"""
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, runtime, weekly_zone_backtest as bt  # noqa: E402
from signals.telegram import send_message  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

TITLE = "Weekly zones, bought when price leaves the zone - backtest"
EMOJI = "🧭"
CSV_PREFIX = "weekly_zone_trades"


def main() -> None:
    runtime.setup_logging()
    side = os.environ.get("WZ_SIDE", "both").lower()
    years = int(os.environ.get("WZ_YEARS", "6"))
    limit = int(os.environ.get("WZ_SYMBOL_LIMIT", "0"))
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
        send_message(runtime.get_env("TELEGRAM_BOT_TOKEN"), runtime.get_env("TELEGRAM_CHAT_ID"), f"{EMOJI} <b>{TITLE}</b>\n<pre>{html.escape(summary)}</pre>"[:4000])
    except Exception as exc:  # noqa: BLE001 - the log has the full summary; a Telegram hiccup must not fail the run
        print(f"telegram skipped: {exc}")
    for s in (["long", "short"] if side == "both" else [side]):
        trades = bt.run(frames, bt.Params(side=s))
        trades.to_csv(f"{CSV_PREFIX}_{s}.csv", index=False)
        print(f"Wrote {len(trades)} {s} trades to {CSV_PREFIX}_{s}.csv")


if __name__ == "__main__":
    main()
