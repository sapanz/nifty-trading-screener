#!/usr/bin/env python3
"""Entry point for the daily swing-trading screener (Mon-Fri, 5pm IST)."""
from datetime import date

from signals import data, runtime, universe
from signals.formatting import format_strategy_message
from signals.strategies import daily_swing
from signals.upstox_client import UpstoxClient

TITLE = "Daily Swing (200 SMA trend, 44 SMA + Lower BB support)"
EMOJI = "📈"


def build_message() -> str:
    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    symbols = universe.fetch_nifty500_symbols()
    instrument_map = data.build_instrument_map(client, symbols)
    daily = data.fetch_daily(client, instrument_map)
    signals = daily_swing.scan(daily)
    return format_strategy_message(TITLE, EMOJI, signals, date.today())


def main() -> None:
    runtime.setup_logging()
    runtime.run_and_notify(TITLE, EMOJI, build_message)


if __name__ == "__main__":
    main()
