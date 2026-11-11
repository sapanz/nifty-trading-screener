#!/usr/bin/env python3
"""Backtest of momentum stocks nearest to a daily zone, with and without a volume-thrust confirmation (signals/momentum_zone_backtest.py).

  MZ_YEARS=6 python scripts/run_momentum_zone_backtest.py
  MZ_YEARS=2 MZ_SYMBOL_LIMIT=60 python scripts/run_momentum_zone_backtest.py     # smoke test

Run through .github/workflows/momentum_zone_backtest.yml (Upstox TOTP login first).
"""
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, momentum_zone_backtest as bt, runtime  # noqa: E402
from signals.telegram import send_message  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

TITLE = "Momentum stocks near a zone (+ volume thrust) - backtest"
EMOJI = "🎯"


def main() -> None:
    runtime.setup_logging()
    years = int(os.environ.get("MZ_YEARS", "6"))
    limit = int(os.environ.get("MZ_SYMBOL_LIMIT", "0"))
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
    P, events = bt.run(frames)
    print(f"{len(events)} setups (stock-sessions with a live zone on the market's side), {int(events['filled'].sum()) if len(events) else 0} filled")
    summary = bt.summarize(events, P)
    print(f"\n=== {TITLE} ===\n{summary}")
    with open("momentum_zone_summary.txt", "w") as fh:
        fh.write(summary)
    if len(events):
        events.drop(columns=["j"]).to_csv("momentum_zone_events.csv.gz", index=False)
    try:
        send_message(runtime.get_env("TELEGRAM_BOT_TOKEN"), runtime.get_env("TELEGRAM_CHAT_ID"), f"{EMOJI} <b>{TITLE}</b>\n<pre>{html.escape(summary[:3300])}</pre>")
    except Exception as exc:  # noqa: BLE001 - the log and the artifact have the full summary; a Telegram hiccup must not fail the run
        print(f"telegram skipped: {exc}")


if __name__ == "__main__":
    main()
