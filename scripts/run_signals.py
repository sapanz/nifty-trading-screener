#!/usr/bin/env python3
"""Single daily entry point for every strategy (Mon-Fri, 5pm IST).

Fetches daily OHLCV via Upstox for Daily Swing (always runs), then:
  - also runs Futures OI Buildup every day, with its own F&O instrument
    map + current-contract candle fetch (~210 symbols, not the full 500 -
    most of Nifty 500 has no futures contract at all)
  - also runs the weekly range breakout on Fridays (or FORCE_WEEKLY=true),
    with its own native-weekly Upstox fetch
  - also runs the monthly ATH breakout on the last trading day of the
    month (or FORCE_MONTHLY=true), with its own native-monthly fetch

Weekly Range Breakout and Monthly ATH Breakout each fetch their own
interval directly rather than resampling the daily fetch - weekly so its
candles match what Upstox itself considers "the week's" OHLCV, monthly
because its all-time-high check needs much deeper history than the daily
fetch's cap (see WEEKLY_HISTORY_YEARS / MONTHLY_ATH_HISTORY_YEARS in
config.py). Each only costs an extra ~500-symbol fetch on the day it
actually runs (once a week / once a month), not every run. Futures OI
Buildup's own ~210-symbol fetch runs every day instead, since open
interest is a daily-updated signal - a real, deliberate extra daily API
cost (still well within a normal run's time budget). Requires
UPSTOX_ACCESS_TOKEN, refreshed daily - see tools/refresh_upstox_token.py.
"""
import os
import sys

# Allow running as `python scripts/run_signals.py` from anywhere (CI or
# local) without needing PYTHONPATH set - Python only searches this
# script's own directory by default, not the repo root where `signals/`
# lives.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, runtime, universe  # noqa: E402
from signals.calendar_utils import ist_today, is_last_trading_day_of_month
from signals.formatting import format_strategy_message
from signals.strategies import daily_swing, futures_oi, monthly_breakout, weekly_breakout
from signals.upstox_client import UpstoxClient

DAILY_SWING_TITLE = "Daily Swing (SMA44/BB Confluence)"
DAILY_SWING_EMOJI = "📈"
FUTURES_OI_TITLE = "Futures OI Buildup"
FUTURES_OI_EMOJI = "⚡"
WEEKLY_BREAKOUT_TITLE = "Weekly Range Breakout"
WEEKLY_BREAKOUT_EMOJI = "🚀"
MONTHLY_TITLE = "Monthly ATH Breakout"
MONTHLY_EMOJI = "🏔️"
FETCH_TITLE = "Signals (data fetch)"
FETCH_EMOJI = "⚠️"


def main() -> None:
    runtime.setup_logging()
    # IST, not the runner's local time (UTC on GitHub Actions) - see
    # calendar_utils.ist_today's docstring for why this matters here.
    today = ist_today()

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

    # Daily Swing's weekly-trend confirmation (BREAKOUT_TREND_SMA rising)
    # uses a resample of the daily fetch already in hand, not a separate
    # native weekly fetch - it runs every day, and a fresh ~500-symbol
    # Upstox weekly fetch daily (rather than just Fridays, like Weekly
    # Range Breakout below) isn't worth the extra API load for a trend
    # check that a resample answers just as well.
    run(
        DAILY_SWING_TITLE,
        DAILY_SWING_EMOJI,
        lambda: format_strategy_message(DAILY_SWING_TITLE, DAILY_SWING_EMOJI, daily_swing.scan(daily, data.to_weekly(daily)), today),
    )

    def build_futures_oi():
        # Its own F&O instrument-map + candle fetch, not derived from
        # `daily` - only ~210 of Nifty 500 have a futures contract at all,
        # and futures/OI data lives at different instrument_keys entirely
        # (NSE_FO|..., not NSE_EQ|...) from the equity fetch above.
        futures_map = data.build_futures_instrument_map(client, symbols)
        futures = data.fetch_futures_daily(client, futures_map)
        return format_strategy_message(FUTURES_OI_TITLE, FUTURES_OI_EMOJI, futures_oi.scan(daily, futures), today)

    run(FUTURES_OI_TITLE, FUTURES_OI_EMOJI, build_futures_oi)

    if today.weekday() == 4 or os.environ.get("FORCE_WEEKLY") == "true":
        def build_weekly():
            # Its own native weekly fetch, not resampled from `daily` - so
            # each candle matches what Upstox itself considers "the
            # week's" OHLCV (see WEEKLY_HISTORY_YEARS in config.py).
            weekly = data.fetch_weekly_history(client, instrument_map)
            return format_strategy_message(WEEKLY_BREAKOUT_TITLE, WEEKLY_BREAKOUT_EMOJI, weekly_breakout.scan(weekly), today)

        run(WEEKLY_BREAKOUT_TITLE, WEEKLY_BREAKOUT_EMOJI, build_weekly)

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
