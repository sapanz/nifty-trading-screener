"""Fetch OHLCV for the Nifty 500 universe via Upstox.

Upstox gets called for the "day" interval once per run, for Daily Swing.
Weekly Range Breakout and Monthly ATH Breakout each fetch their own
native interval directly instead of resampling that daily fetch:
`fetch_weekly_history` (native "week", so each candle matches what
Upstox itself considers "the week's" OHLCV) and
`fetch_monthly_ath_history` (native "month", fetched much deeper than
the daily cap so "all-time high" means what it says - see
MONTHLY_ATH_HISTORY_YEARS in config.py). `to_weekly`/`to_monthly` below
still resample from daily as a fallback for callers that don't have a
live client (e.g. a quick local backtest without extra Upstox fetches).

If the data source is rejecting requests wholesale (as NSE direct
scraping turned out to do from GitHub Actions), grinding through all ~500
symbols before giving up wastes hours. All three fetch functions below
check the failure rate after a small sample and abort early if it looks
systemic.
"""
from __future__ import annotations

import logging
from typing import Callable

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
        sample_wanted = symbols[:10]
        sample_master = list(full_map.keys())[:10]
        raise RuntimeError(
            f"Only {len(mapping)}/{len(symbols)} symbols matched an Upstox instrument_key "
            f"({match_ratio:.0%}) - the instrument master likely changed shape. "
            f"Symbols we looked for: {sample_wanted}. "
            f"Symbols actually in the instrument master ({len(full_map)} total): {sample_master}. "
            "Compare these two lists to see the mismatch; check signals/upstox_client.py's "
            "column/filter assumptions."
        )
    return mapping


def build_futures_instrument_map(client: UpstoxClient, symbols: list[str]) -> dict[str, dict]:
    """Map NSE trading symbols to their current (nearest-unexpired-month)
    stock-futures contract: {symbol: {"instrument_key", "expiry", "lot_size"}}.

    Only ~150-220 of Nifty 500 actually have futures listed at all - a low
    match ratio against the full 500-symbol list is normal here, unlike
    build_instrument_map's equity mapping (which should match nearly
    everything). What would signal a broken parse instead is a near-zero
    absolute count - the F&O universe size is stable enough that under
    config.MIN_FO_MATCH_COUNT genuinely matched symbols means something
    upstream changed shape (see fetch_fo_instrument_master's own defensive
    parsing for the more common single-column/format failure modes).
    """
    fo = client.fetch_fo_instrument_master()
    today = pd.Timestamp(ist_today())
    fo = fo[fo["expiry"] >= today]
    # Nearest unexpired expiry per underlying = the current (near-month)
    # contract - the one with real liquidity and OI right now. Falling
    # back to sort+groupby.first() rather than idxmin() since a symbol
    # with zero remaining unexpired rows (shouldn't happen day-to-day, but
    # possible right at a data refresh boundary) should just be absent
    # from the result, not raise.
    fo = fo.sort_values("expiry").groupby("name", as_index=False).first()

    mapping = {
        row["name"]: {"instrument_key": row["instrument_key"], "expiry": row["expiry"], "lot_size": row["lot_size"]}
        for _, row in fo.iterrows()
        if row["name"] in symbols
    }
    matched = len(mapping)
    if matched < config.MIN_FO_MATCH_COUNT:
        raise RuntimeError(
            f"Only {matched} symbols matched a current F&O futures contract - expected at least "
            f"{config.MIN_FO_MATCH_COUNT}. Either the F&O universe genuinely shrank a lot, or "
            f"fetch_fo_instrument_master's name-as-underlying-symbol assumption is wrong. "
            f"Sample underlyings in the F&O master: {sorted(fo['name'].unique())[:20]}. "
            f"Sample requested symbols: {symbols[:20]}."
        )
    logger.info("F&O futures universe: %d/%d requested symbols have a current contract", matched, len(symbols))
    return mapping


def _fetch_history(
    instrument_map: dict[str, str], fetch_one: Callable[[str], pd.DataFrame], throttle: Callable[[], None], label: str
) -> dict[str, pd.DataFrame]:
    """Shared fetch loop: pull one series per symbol, skip individual
    failures, and abort early if a large fraction of an initial sample
    fails - a sign the data source is blocking us wholesale rather than a
    handful of unlucky symbols.
    """
    result: dict[str, pd.DataFrame] = {}
    failures: list[str] = []

    for i, (symbol, instrument_key) in enumerate(instrument_map.items(), start=1):
        try:
            df = fetch_one(instrument_key)
            if not df.empty:
                result[symbol] = df
            else:
                failures.append(symbol)
        except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't kill the run
            failures.append(symbol)
            logger.warning("Failed to fetch %s history for %s: %s", label, symbol, exc)
        throttle()

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


def fetch_daily(client: UpstoxClient, instrument_map: dict[str, str]) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for every symbol. Failures are logged and skipped."""
    return _fetch_history(
        instrument_map,
        fetch_one=lambda key: client.get_daily_history(key, years=config.DAILY_HISTORY_YEARS),
        throttle=client.throttle,
        label="daily",
    )


def fetch_weekly_history(client: UpstoxClient, instrument_map: dict[str, str]) -> dict[str, pd.DataFrame]:
    """Fetch native weekly OHLCV for every symbol, for Weekly Range Breakout -
    see WEEKLY_HISTORY_YEARS in config.py for why this is a separate fetch
    from fetch_daily rather than a resample of it.
    """
    return _fetch_history(
        instrument_map,
        fetch_one=lambda key: client.get_weekly_history(key, years=config.WEEKLY_HISTORY_YEARS),
        throttle=client.throttle,
        label="weekly",
    )


def fetch_monthly_ath_history(client: UpstoxClient, instrument_map: dict[str, str]) -> dict[str, pd.DataFrame]:
    """Fetch native monthly OHLCV for every symbol, for Monthly ATH Breakout's
    all-time-high check specifically - see MONTHLY_ATH_HISTORY_YEARS in
    config.py for why this is a separate, deeper fetch from fetch_daily.
    """
    return _fetch_history(
        instrument_map,
        fetch_one=lambda key: client.get_monthly_history(key, years=config.MONTHLY_ATH_HISTORY_YEARS),
        throttle=client.throttle,
        label="monthly ATH",
    )


def _resample(daily_df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if daily_df.empty:
        return daily_df
    return daily_df.resample(rule).agg(_RESAMPLE_AGG).dropna(subset=["close"])


def to_weekly(daily_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {symbol: _resample(df, "W-FRI") for symbol, df in daily_data.items()}


def to_monthly(daily_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {symbol: _resample(df, "ME") for symbol, df in daily_data.items()}
