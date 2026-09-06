#!/usr/bin/env python3
"""Entry point for both weekly screeners (Fridays, 5pm IST)."""
from datetime import date

from signals import data, runtime, universe
from signals.formatting import format_strategy_message
from signals.strategies import weekly_breakout, weekly_sma_support
from signals.upstox_client import UpstoxClient

SUPPORT_TITLE = "Weekly SMA-30 Support"
SUPPORT_EMOJI = "🟢"
BREAKOUT_TITLE = "Weekly Range Breakout"
BREAKOUT_EMOJI = "🚀"
SHARED_TITLE = "Weekly Signals (data fetch)"
SHARED_EMOJI = "⚠️"


def main() -> None:
    runtime.setup_logging()

    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        symbols = universe.fetch_nifty500_symbols()
        instrument_map = data.build_instrument_map(client, symbols)
        weekly = data.fetch_weekly(client, instrument_map)
    except Exception as exc:
        runtime.notify_error(SHARED_TITLE, SHARED_EMOJI, str(exc))
        raise

    failures = []
    for title, emoji, scan_fn in (
        (SUPPORT_TITLE, SUPPORT_EMOJI, weekly_sma_support.scan),
        (BREAKOUT_TITLE, BREAKOUT_EMOJI, weekly_breakout.scan),
    ):
        try:
            runtime.run_and_notify(
                title, emoji, lambda scan_fn=scan_fn, title=title, emoji=emoji: format_strategy_message(
                    title, emoji, scan_fn(weekly), date.today()
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate the two strategies from each other
            failures.append((title, exc))

    if failures:
        raise RuntimeError(f"{len(failures)} weekly strategy run(s) failed: {failures}")


if __name__ == "__main__":
    main()
