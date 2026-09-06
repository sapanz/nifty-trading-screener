#!/usr/bin/env python3
"""Entry point for the monthly ATH-breakout screener (last trading day of month, 5pm IST).

The workflow's cron fires on the last few calendar days of every month;
this script no-ops (exits 0 without sending anything) on any day that
isn't actually the last trading day, so it's safe to schedule broadly.
"""
import logging
import os
from datetime import date

from signals import data, runtime, universe
from signals.calendar_utils import is_last_trading_day_of_month
from signals.formatting import format_strategy_message
from signals.strategies import monthly_breakout
from signals.upstox_client import UpstoxClient

TITLE = "Monthly ATH Breakout"
EMOJI = "🏔️"


def build_message() -> str:
    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    symbols = universe.fetch_nifty500_symbols()
    instrument_map = data.build_instrument_map(client, symbols)
    monthly = data.fetch_monthly(client, instrument_map)
    signals = monthly_breakout.scan(monthly)
    return format_strategy_message(TITLE, EMOJI, signals, date.today())


def main() -> None:
    runtime.setup_logging()
    if not is_last_trading_day_of_month() and os.environ.get("FORCE_RUN") != "true":
        logging.info("Not the last trading day of the month, skipping monthly breakout run.")
        return
    runtime.run_and_notify(TITLE, EMOJI, build_message)


if __name__ == "__main__":
    main()
