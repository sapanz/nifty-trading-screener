"""Fetch OHLCV data for the Nifty 500 universe via Upstox."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from signals import config
from signals.upstox_client import UpstoxClient

logger = logging.getLogger(__name__)


def build_instrument_map(client: UpstoxClient, symbols: list[str]) -> dict[str, str]:
    """Map NSE trading symbols to Upstox instrument keys, dropping unknowns."""
    full_map = client.fetch_instrument_map()
    mapping = {sym: full_map[sym] for sym in symbols if sym in full_map}
    missing = sorted(set(symbols) - mapping.keys())
    if missing:
        logger.warning("No Upstox instrument_key found for %d symbols: %s", len(missing), missing[:20])
    return mapping


def fetch_all(
    client: UpstoxClient,
    instrument_map: dict[str, str],
    interval: str,
    years: int | None = None,
    start: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch historical candles for every symbol at the given interval.

    Provide either `years` (relative to today) or an explicit `start` date
    string (YYYY-MM-DD). Symbols whose fetch fails are logged and skipped
    rather than aborting the whole run.
    """
    today = date.today()
    from_date = date.fromisoformat(start) if start else today - timedelta(days=365 * years)

    result: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    for symbol, instrument_key in instrument_map.items():
        try:
            df = client.get_historical_candles(instrument_key, interval, from_date, today)
            if not df.empty:
                result[symbol] = df
        except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't kill the run
            failures.append(symbol)
            logger.warning("Failed to fetch %s candles for %s: %s", interval, symbol, exc)
        client.throttle()

    if failures:
        logger.warning("Skipped %d/%d symbols for interval=%s due to fetch errors", len(failures), len(instrument_map), interval)
    return result


def fetch_daily(client: UpstoxClient, instrument_map: dict[str, str]) -> dict[str, pd.DataFrame]:
    return fetch_all(client, instrument_map, "day", years=config.DAILY_HISTORY_YEARS)


def fetch_weekly(client: UpstoxClient, instrument_map: dict[str, str]) -> dict[str, pd.DataFrame]:
    return fetch_all(client, instrument_map, "week", years=config.WEEKLY_HISTORY_YEARS)


def fetch_monthly(client: UpstoxClient, instrument_map: dict[str, str]) -> dict[str, pd.DataFrame]:
    return fetch_all(client, instrument_map, "month", start=config.MONTHLY_HISTORY_START)
