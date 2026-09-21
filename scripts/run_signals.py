#!/usr/bin/env python3
"""Single daily entry point for every strategy (Mon-Fri, 5pm IST).

Fetches daily OHLCV via Upstox for Daily Swing (always runs), then:
  - also runs Price Action Breakout's daily leg every day, off the same
    daily fetch (no extra Upstox call)
  - also runs the weekly range breakout and Price Action Breakout's weekly
    leg on Fridays (or FORCE_WEEKLY=true), sharing one native-weekly
    Upstox fetch between them
  - also runs the monthly ATH breakout on the last trading day of the
    month (or FORCE_MONTHLY=true), off its own native-monthly Upstox
    fetch. Price Action Breakout has no monthly leg - a 5-year backtest
    showed it never fires under these thresholds, and Monthly ATH
    Breakout already covers this timeframe.

Weekly Range Breakout and Monthly ATH Breakout each fetch their own
interval directly rather than resampling the daily fetch - weekly so its
candles match what Upstox itself considers "the week's" OHLCV, monthly
because its all-time-high check needs much deeper history than the daily
fetch's cap (see WEEKLY_HISTORY_YEARS / MONTHLY_ATH_HISTORY_YEARS in
config.py). Each only costs an extra ~500-symbol fetch on the day it
actually runs (once a week / once a month), not every run.

Also fetches the F&O-eligible symbol set (one cheap instrument-master
call, no price history) once per run and threads it through to both
Price Action Breakout legs as `short_eligible` - it gates their short
(breakdown) side, since a cash-segment equity short can't be carried
overnight in India without a futures contract to actually sell (see
data.fetch_fo_eligible_symbols). A failure fetching it disables shorts
for that run rather than taking down the long-only pipeline.

Requires UPSTOX_ACCESS_TOKEN, refreshed daily - see
tools/refresh_upstox_token.py.
"""
import logging
import os
import sys

# Allow running as `python scripts/run_signals.py` from anywhere (CI or
# local) without needing PYTHONPATH set - Python only searches this
# script's own directory by default, not the repo root where `signals/`
# lives.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import config, data, runtime, universe  # noqa: E402
from signals.calendar_utils import ist_today, is_last_trading_day_of_month
from signals.formatting import format_strategy_message
from signals.strategies import daily_swing, monthly_breakout, price_action_breakout, weekly_breakout
from signals.upstox_client import UpstoxClient

logger = logging.getLogger(__name__)

DAILY_SWING_TITLE = "Daily Swing (SMA44/BB Confluence)"
DAILY_SWING_EMOJI = "📈"
WEEKLY_BREAKOUT_TITLE = "Weekly Range Breakout"
WEEKLY_BREAKOUT_EMOJI = "🚀"
MONTHLY_TITLE = "Monthly ATH Breakout"
MONTHLY_EMOJI = "🏔️"
PRICE_ACTION_DAILY_TITLE = "Price Action Breakout (Daily)"
PRICE_ACTION_WEEKLY_TITLE = "Price Action Breakout (Weekly)"
PRICE_ACTION_EMOJI = "🎯"
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

    try:
        # Gates Price Action Breakout's short leg (see
        # data.fetch_fo_eligible_symbols) - kept out of the try/except
        # above and defaulted to None (shorts off) on failure, since a
        # hiccup fetching this shouldn't take down the long-only pipeline
        # that already works reliably.
        fo_symbols = data.fetch_fo_eligible_symbols(client)
    except Exception as exc:  # noqa: BLE001 - additive; see comment above
        logger.warning("Failed to fetch F&O-eligible symbols, short leg disabled this run: %s", exc)
        fo_symbols = None

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

    # Price Action Breakout's daily leg reuses `daily` too - no extra fetch,
    # same as Daily Swing above.
    run(
        PRICE_ACTION_DAILY_TITLE,
        PRICE_ACTION_EMOJI,
        lambda: format_strategy_message(
            PRICE_ACTION_DAILY_TITLE,
            PRICE_ACTION_EMOJI,
            price_action_breakout.scan(
                daily,
                pattern_min_lookback=config.PRICE_ACTION_PATTERN_MIN_LOOKBACK_DAILY,
                pattern_max_lookback=config.PRICE_ACTION_PATTERN_MAX_LOOKBACK_DAILY,
                breakout_window=config.PRICE_ACTION_BREAKOUT_WINDOW_DAILY,
                volume_lookback=config.PRICE_ACTION_VOLUME_LOOKBACK_DAILY,
                short_eligible=fo_symbols,
            ),
            today,
        ),
    )

    if today.weekday() == 4 or os.environ.get("FORCE_WEEKLY") == "true":
        # Fetched once, outside either strategy's own error isolation, since
        # both weekly strategies below need the exact same native-weekly
        # data and there's no point fetching it twice (or letting one
        # succeed on a stale in-memory copy while the other re-fetches).
        try:
            weekly = data.fetch_weekly_history(client, instrument_map)
        except Exception as exc:  # noqa: BLE001 - isolate from the daily-only strategies above
            failures.append(("Weekly fetch", exc))
        else:
            # Its own native weekly fetch, not resampled from `daily` - so
            # each candle matches what Upstox itself considers "the
            # week's" OHLCV (see WEEKLY_HISTORY_YEARS in config.py).
            run(
                WEEKLY_BREAKOUT_TITLE,
                WEEKLY_BREAKOUT_EMOJI,
                lambda: format_strategy_message(WEEKLY_BREAKOUT_TITLE, WEEKLY_BREAKOUT_EMOJI, weekly_breakout.scan(weekly), today),
            )
            run(
                PRICE_ACTION_WEEKLY_TITLE,
                PRICE_ACTION_EMOJI,
                lambda: format_strategy_message(
                    PRICE_ACTION_WEEKLY_TITLE,
                    PRICE_ACTION_EMOJI,
                    price_action_breakout.scan(
                        weekly,
                        pattern_min_lookback=config.PRICE_ACTION_PATTERN_MIN_LOOKBACK_WEEKLY,
                        pattern_max_lookback=config.PRICE_ACTION_PATTERN_MAX_LOOKBACK_WEEKLY,
                        breakout_window=config.PRICE_ACTION_BREAKOUT_WINDOW_WEEKLY,
                        volume_lookback=config.PRICE_ACTION_VOLUME_LOOKBACK_WEEKLY,
                        short_eligible=fo_symbols,
                    ),
                    today,
                ),
            )

    if is_last_trading_day_of_month(today) or os.environ.get("FORCE_MONTHLY") == "true":
        # Fetched once, outside either strategy's own error isolation, same
        # reasoning as the shared weekly fetch above.
        try:
            # Its own native monthly fetch, not resampled from `daily` - the
            # daily fetch is capped at DAILY_HISTORY_YEARS and an all-time-
            # high check needs a much deeper lookback than that (see
            # MONTHLY_ATH_HISTORY_YEARS in config.py).
            monthly = data.fetch_monthly_ath_history(client, instrument_map)
        except Exception as exc:  # noqa: BLE001 - isolate from the daily/weekly strategies above
            failures.append(("Monthly fetch", exc))
        else:
            run(
                MONTHLY_TITLE,
                MONTHLY_EMOJI,
                lambda: format_strategy_message(MONTHLY_TITLE, MONTHLY_EMOJI, monthly_breakout.scan(monthly), today),
            )

    if failures:
        raise RuntimeError(f"{len(failures)} strategy run(s) failed: {failures}")


if __name__ == "__main__":
    main()
