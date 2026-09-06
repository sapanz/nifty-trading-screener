"""Fetch daily OHLCV for the Nifty 500 universe via Upstox, once per run.

Upstox only gets called for the "day" interval; weekly and monthly series
used by the other strategies are derived here by resampling that same
daily fetch - one round of Upstox calls serves every strategy that runs
that day.

If the data source is rejecting requests wholesale (as NSE direct
scraping turned out to do from GitHub Actions), grinding through all ~500
symbols before giving up wastes hours. `fetch_daily` checks the failure
rate after a small sample and aborts early if it looks systemic.
"""
from __future__ import annotations

import logging

import pandas as pd

from signals import config
from signals.upstox_client import UpstoxClient

logger = logging.getLogger(__name__)

_RESAMPLE_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def build_instrument_map(client: UpstoxClient, symbols: list[str]) -> dict[str, str]:
    """Map NSE trading symbols to Upstox instrument keys, dropping unknowns.

    Raises if only a small fraction match - a handful of unlisted/renamed
    symbols is normal, but most of the universe failing to map means the
    instrument master's shape changed underneath us (e.g. a filtered
    column's values no longer look like we assumed), not that Upstox
    genuinely doesn't list most of Nifty 500.
    """
    full_map = client.fetch_instrument_map()
    mapping = {sym: full_map[sym] for sym in symbols if sym in full_map}
    missing = sorted(set(symbols) - mapping.keys())
    if missing:
        logger.warning("No Upstox instrument_key found for %d symbols: %s", len(missing), missing[:20])

    match_ratio = len(mapping) / len(symbols) if symbols else 0
    if match_ratio < config.MIN_INSTRUMENT_MATCH_RATIO:
        raise RuntimeError(
            f"Only {len(mapping)}/{len(symbols)} symbols matched an Upstox instrument_key "
            f"({match_ratio:.0%}) - the instrument master likely changed shape; "
            "check signals/upstox_client.py's column/filter assumptions."
        )
    return mapping


def fetch_daily(client: UpstoxClient, instrument_map: dict[str, str]) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for every symbol. Failures are logged and skipped.

    Aborts early with a clear error if a large fraction of an initial
    sample fails - a sign the data source is blocking us wholesale rather
    than a handful of unlucky symbols.
    """
    result: dict[str, pd.DataFrame] = {}
    failures: list[str] = []

    for i, (symbol, instrument_key) in enumerate(instrument_map.items(), start=1):
        try:
            df = client.get_daily_history(instrument_key, years=config.DAILY_HISTORY_YEARS)
            if not df.empty:
                result[symbol] = df
            else:
                failures.append(symbol)
        except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't kill the run
            failures.append(symbol)
            logger.warning("Failed to fetch daily history for %s: %s", symbol, exc)
        client.throttle()

        if i == config.CIRCUIT_BREAKER_SAMPLE_SIZE:
            failure_ratio = len(failures) / i
            if failure_ratio >= config.CIRCUIT_BREAKER_FAILURE_RATIO:
                raise RuntimeError(
                    f"{len(failures)}/{i} symbols failed in the first sample - the data "
                    "source looks like it's blocking requests wholesale, aborting instead "
                    "of grinding through the rest of the universe."
                )

    if failures:
        logger.warning("Skipped %d/%d symbols due to fetch errors or empty data: %s", len(failures), len(instrument_map), failures[:20])
    return result


def _resample(daily_df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if daily_df.empty:
        return daily_df
    return daily_df.resample(rule).agg(_RESAMPLE_AGG).dropna(subset=["close"])


def to_weekly(daily_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {symbol: _resample(df, "W-FRI") for symbol, df in daily_data.items()}


def to_monthly(daily_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {symbol: _resample(df, "ME") for symbol, df in daily_data.items()}
