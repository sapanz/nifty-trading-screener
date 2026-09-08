#!/usr/bin/env python3
"""Single daily entry point for every strategy (Mon-Fri, 5pm IST).

Fetches daily OHLCV via Upstox once, then:
  - always runs the daily swing (SMA44/lower-BB confluence) screener
  - also runs the weekly range breakout on Fridays (or FORCE_WEEKLY=true)
  - also runs the monthly ATH breakout on the last trading day of the
    month (or FORCE_MONTHLY=true)

One shared daily fetch keeps Upstox calls to a single pass for Daily Swing
and Weekly Range Breakout even when both fire on the same run (Fridays).
Monthly ATH Breakout fetches separately, at native monthly granularity,
since its all-time-high check needs much deeper history than the daily
fetch's cap (see MONTHLY_ATH_HISTORY_YEARS in config.py) - this only
costs an extra ~500-symbol fetch once a month, not every run. Requires
UPSTOX_ACCESS_TOKEN, refreshed daily - see tools/refresh_upstox_token.py.
"""
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
from signals.strategies import daily_swing, monthly_breakout, weekly_breakout
from signals.upstox_client import UpstoxClient

DAILY_SWING_TITLE = "Daily Swing (SMA44/BB Confluence)"
DAILY_SWING_EMOJI = "📈"
WEEKLY_BREAKOUT_TITLE = "Weekly Range Breakout"
WEEKLY_BREAKOUT_EMOJI = "🚀"
MONTHLY_TITLE = "Monthly ATH Breakout"
MONTHLY_EMOJI = "🏔️"
FETCH_TITLE = "Signals (data fetch)"
FETCH_EMOJI = "⚠️"


def main() -> None:
    runtime.setup_logging()
    today = date.today()

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

    run(DAILY_SWING_TITLE, DAILY_SWING_EMOJI, lambda: format_strategy_message(DAILY_SWING_TITLE, DAILY_SWING_EMOJI, daily_swing.scan(daily), today))

    if today.weekday() == 4 or os.environ.get("FORCE_WEEKLY") == "true":
        weekly = data.to_weekly(daily)
        run(
            WEEKLY_BREAKOUT_TITLE,
            WEEKLY_BREAKOUT_EMOJI,
            lambda: format_strategy_message(WEEKLY_BREAKOUT_TITLE, WEEKLY_BREAKOUT_EMOJI, weekly_breakout.scan(weekly), today),
        )

    if is_last_trading_day_of_month(today) or os.environ.get("FORCE_MONTHLY") == "true":
        def build_monthly():
            # Its own native monthly fetch, not resampled from `daily` -
            # the daily fetch is capped at DAILY_HISTORY_YEARS and an
            # all-time-high check needs a much deeper lookback than that
            # (see MONTHLY_ATH_HISTORY_YEARS in config.py).
            monthly = data.fetch_monthly_ath_history(client, instrument_map)
            return format_strategy_message(MONTHLY_TITLE, MONTHLY_EMOJI, monthly_breakout.scan(monthly), today)

        run(MONTHLY_TITLE, MONTHLY_EMOJI, build_monthly)

    if failures:
        raise RuntimeError(f"{len(failures)} strategy run(s) failed: {failures}")


if __name__ == "__main__":
    main()
