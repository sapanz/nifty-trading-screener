#!/usr/bin/env python3
"""Single daily entry point for every strategy (Mon-Fri, 5pm IST).

Fetches NSE daily OHLCV once, then:
  - always runs the daily swing screener
  - also runs both weekly screeners on Fridays (or FORCE_WEEKLY=true)
  - also runs the monthly ATH breakout on the last trading day of the
    month (or FORCE_MONTHLY=true)

One shared fetch keeps NSE load to a single scrape per day even when
several strategies fire on the same run (e.g. every Friday).
"""
import os
from datetime import date

from signals import data, runtime, universe
from signals.calendar_utils import is_last_trading_day_of_month
from signals.formatting import format_strategy_message
from signals.nse_client import NseClient
from signals.strategies import daily_swing, monthly_breakout, weekly_breakout, weekly_sma_support

DAILY_TITLE = "Daily Swing (200 SMA trend, 44 SMA + Lower BB support)"
DAILY_EMOJI = "📈"
WEEKLY_SUPPORT_TITLE = "Weekly SMA-30 Support"
WEEKLY_SUPPORT_EMOJI = "🟢"
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
        client = NseClient()
        symbols = universe.fetch_nifty500_symbols()
        daily = data.fetch_daily(client, symbols)
    except Exception as exc:
        runtime.notify_error(FETCH_TITLE, FETCH_EMOJI, str(exc))
        raise

    failures: list[tuple[str, Exception]] = []

    def run(title: str, emoji: str, builder) -> None:
        try:
            runtime.run_and_notify(title, emoji, builder)
        except Exception as exc:  # noqa: BLE001 - isolate strategies from each other
            failures.append((title, exc))

    run(DAILY_TITLE, DAILY_EMOJI, lambda: format_strategy_message(DAILY_TITLE, DAILY_EMOJI, daily_swing.scan(daily), today))

    if today.weekday() == 4 or os.environ.get("FORCE_WEEKLY") == "true":
        weekly = data.to_weekly(daily)
        run(
            WEEKLY_SUPPORT_TITLE,
            WEEKLY_SUPPORT_EMOJI,
            lambda: format_strategy_message(WEEKLY_SUPPORT_TITLE, WEEKLY_SUPPORT_EMOJI, weekly_sma_support.scan(weekly), today),
        )
        run(
            WEEKLY_BREAKOUT_TITLE,
            WEEKLY_BREAKOUT_EMOJI,
            lambda: format_strategy_message(WEEKLY_BREAKOUT_TITLE, WEEKLY_BREAKOUT_EMOJI, weekly_breakout.scan(weekly), today),
        )

    if is_last_trading_day_of_month(today) or os.environ.get("FORCE_MONTHLY") == "true":
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
