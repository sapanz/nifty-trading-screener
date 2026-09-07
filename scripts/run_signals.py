#!/usr/bin/env python3
"""Single entry point for every active strategy - runs Fridays (weekly
strategies) and on the last trading day of the month (monthly), nothing
in between now that there's no daily-timeframe strategy left in the
lineup (see signals/config.py for why "Daily Swing" was retired).

Fetches daily OHLCV via Upstox once, then:
  - runs both weekly strategies (Weekly Range Breakout, CIP Weekly) on
    Fridays (or FORCE_WEEKLY=true)
  - runs the monthly ATH breakout on the last trading day of the month
    (or FORCE_MONTHLY=true)

.github/workflows/signals.yml gates the expensive Upstox login/fetch
steps on the same Friday-or-month-end check so non-trading days don't
pay for a login and fetch that would find nothing to run; this script
re-checks independently so a direct/manual invocation is still safe.

One shared fetch keeps Upstox calls to a single pass per day even when
several strategies fire on the same run (e.g. every Friday). Requires
UPSTOX_ACCESS_TOKEN, refreshed daily - see tools/refresh_upstox_token.py.
"""
import logging
import os
import sys
from datetime import date

# Allow running as `python scripts/run_signals.py` from anywhere (CI or
# local) without needing PYTHONPATH set - Python only searches this
# script's own directory by default, not the repo root where `signals/`
# lives.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, runtime, universe  # noqa: E402
from signals.calendar_utils import is_last_trading_day_of_month
from signals.formatting import format_strategy_message
from signals.strategies import cip_weekly, monthly_breakout, weekly_breakout
from signals.upstox_client import UpstoxClient

WEEKLY_BREAKOUT_TITLE = "Weekly Range Breakout"
WEEKLY_BREAKOUT_EMOJI = "🚀"
CIP_WEEKLY_TITLE = "CIP Weekly"
CIP_WEEKLY_EMOJI = "🔄"
MONTHLY_TITLE = "Monthly ATH Breakout"
MONTHLY_EMOJI = "🏔️"
FETCH_TITLE = "Signals (data fetch)"
FETCH_EMOJI = "⚠️"


def main() -> None:
    runtime.setup_logging()
    today = date.today()

    run_weekly = today.weekday() == 4 or os.environ.get("FORCE_WEEKLY") == "true"
    run_monthly = is_last_trading_day_of_month(today) or os.environ.get("FORCE_MONTHLY") == "true"
    if not run_weekly and not run_monthly:
        logging.info("Not Friday or month-end - nothing to run today.")
        return

    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        symbols = universe.fetch_nifty500_symbols()
        instrument_map = data.build_instrument_map(client, symbols)
        daily = data.fetch_daily(client, instrument_map)
    except Exception as exc:
        runtime.notify_error(FETCH_TITLE, FETCH_EMOJI, str(exc))
        raise

    failures: list[tuple[str, Exception]] = []

    def run(title: str, emoji: str, builder) -> None:
        try:
            runtime.run_and_notify(title, emoji, builder)
        except Exception as exc:  # noqa: BLE001 - isolate strategies from each other
            failures.append((title, exc))

    if run_weekly:
        weekly = data.to_weekly(daily)
        run(
            WEEKLY_BREAKOUT_TITLE,
            WEEKLY_BREAKOUT_EMOJI,
            lambda: format_strategy_message(WEEKLY_BREAKOUT_TITLE, WEEKLY_BREAKOUT_EMOJI, weekly_breakout.scan(weekly), today),
        )
        run(
            CIP_WEEKLY_TITLE,
            CIP_WEEKLY_EMOJI,
            lambda: format_strategy_message(CIP_WEEKLY_TITLE, CIP_WEEKLY_EMOJI, cip_weekly.scan(weekly), today),
        )

    if run_monthly:
        monthly = data.to_monthly(daily)
        run(
            MONTHLY_TITLE,
            MONTHLY_EMOJI,
            lambda: format_strategy_message(MONTHLY_TITLE, MONTHLY_EMOJI, monthly_breakout.scan(monthly), today),
        )

    if failures:
        raise RuntimeError(f"{len(failures)} strategy run(s) failed: {failures}")


if __name__ == "__main__":
    main()
