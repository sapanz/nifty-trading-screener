"""Fetch daily OHLCV for the Nifty 500 universe from NSE, once per run.

NSE's public historical API only speaks daily bars (no native weekly/
monthly interval like a broker API would offer), so weekly and monthly
series used by the other strategies are derived here by resampling the
same daily fetch - one NSE scrape serves every strategy that runs that day.
"""
from __future__ import annotations

import logging

import pandas as pd

from signals import config
from signals.nse_client import NseClient

logger = logging.getLogger(__name__)

_RESAMPLE_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def fetch_daily(client: NseClient, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for every symbol. Failures are logged and skipped."""
    result: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    for symbol in symbols:
        try:
            df = client.get_daily_history(symbol, years=config.DAILY_HISTORY_YEARS)
            if not df.empty:
                result[symbol] = df
            else:
                failures.append(symbol)
        except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't kill the run
            failures.append(symbol)
            logger.warning("Failed to fetch daily history for %s: %s", symbol, exc)

    if failures:
        logger.warning("Skipped %d/%d symbols due to fetch errors or empty data: %s", len(failures), len(symbols), failures[:20])
    return result


def _resample(daily_df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if daily_df.empty:
        return daily_df
    return daily_df.resample(rule).agg(_RESAMPLE_AGG).dropna(subset=["close"])


def to_weekly(daily_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {symbol: _resample(df, "W-FRI") for symbol, df in daily_data.items()}


def to_monthly(daily_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {symbol: _resample(df, "ME") for symbol, df in daily_data.items()}
