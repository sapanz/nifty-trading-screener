"""Thin wrapper around the Upstox v2 market-data HTTP API.

Two things this module deals with that are easy to get wrong:

1. Historical candles are addressed by Upstox's own ``instrument_key``
   (e.g. ``NSE_EQ|INE002A01018``), not by trading symbol. The instrument
   master file (a gzipped CSV, updated daily by Upstox, no auth required)
   is how we translate NSE trading symbols into instrument keys.
2. The access token is short-lived (Upstox invalidates it every day around
   3:30am IST) and is supplied per run via the ``UPSTOX_ACCESS_TOKEN``
   environment variable/GitHub secret. See tools/refresh_upstox_token.py
   for the one-tap daily refresh flow - this module never tries to mint
   or refresh one itself.

The "day" interval is fetched once per run for Daily Swing. Weekly Range
Breakout and Monthly ATH Breakout each fetch their own native interval
directly (get_weekly_history / get_monthly_history) rather than
resampling the daily fetch: monthly needs a much deeper lookback to find
a genuine all-time high (the daily-history cap is nowhere near enough),
and weekly wants candles that match what Upstox itself considers "the
week's" OHLCV rather than a pandas resample of daily bars. Both only run
on the day their strategy actually fires (once a week / once a month),
so the extra fetch isn't paid on every run.

Upstox's API has changed shape before; if historical-candle requests start
failing with 4xx errors, check developer.upstox.com and adjust the URL
building in ``get_daily_history`` accordingly.
"""
from __future__ import annotations

import gzip
import io
import logging
import time
from datetime import date, timedelta

import pandas as pd
import requests

from signals import config

logger = logging.getLogger(__name__)

CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]


class UpstoxError(RuntimeError):
    """Raised for unrecoverable Upstox API failures."""


class UpstoxClient:
    def __init__(self, access_token: str):
        if not access_token:
            raise UpstoxError("UPSTOX_ACCESS_TOKEN is empty")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
        )

    def fetch_instrument_map(self) -> dict[str, str]:
        """Return {trading_symbol: instrument_key} for NSE equities."""
        resp = requests.get(config.UPSTOX_INSTRUMENTS_URL, timeout=60)
        resp.raise_for_status()
        raw = gzip.decompress(resp.content)
        df = pd.read_csv(io.BytesIO(raw))
        df.columns = [c.strip().lower() for c in df.columns]
        logger.info("Instrument master: %d rows, columns=%s", len(df), list(df.columns))

        symbol_col = next((c for c in ("tradingsymbol", "trading_symbol") if c in df.columns), None)
        key_col = next((c for c in ("instrument_key", "instrumentkey") if c in df.columns), None)
        if symbol_col is None or key_col is None:
            raise UpstoxError(
                f"Unexpected instrument master columns: {list(df.columns)}. "
                "Upstox may have changed the file format - check developer.upstox.com."
            )

        # The instruments URL is already NSE-specific, so no exchange/segment
        # filter is needed here - just exclude non-equity instruments (F&O,
        # indices) that the same per-exchange file also lists.
        type_col = next((c for c in ("instrument_type", "instrumenttype") if c in df.columns), None)
        if type_col is not None:
            value_counts = df[type_col].astype(str).value_counts().head(10).to_dict()
            logger.info("Instrument master %s value counts: %s", type_col, value_counts)
            df = df[df[type_col].astype(str).str.upper() == config.UPSTOX_EQUITY_TYPE]
            if df.empty:
                raise UpstoxError(
                    f"Filtering {type_col} == {config.UPSTOX_EQUITY_TYPE!r} left zero rows. "
                    f"Actual {type_col} values seen: {value_counts}. "
                    "Upstox likely uses a different value for equities now."
                )
        logger.info("Instrument master: %d rows after instrument_type filter", len(df))
        logger.info("Sample %s/%s pairs: %s", symbol_col, key_col, df[[symbol_col, key_col]].head(5).to_dict("records"))

        return dict(zip(df[symbol_col].astype(str).str.strip(), df[key_col].astype(str).str.strip()))

    def get_daily_history(self, instrument_key: str, years: int) -> pd.DataFrame:
        """Fetch daily OHLCV candles for one instrument.

        Returns a DataFrame indexed by date, sorted oldest -> newest, with
        columns open/high/low/close/volume.
        """
        return self._get_history(instrument_key, "day", years)

    def get_weekly_history(self, instrument_key: str, years: int) -> pd.DataFrame:
        """Fetch weekly OHLCV candles for one instrument, aggregated by
        Upstox itself rather than resampled from daily bars.

        Used only by Weekly Range Breakout. Unlike the monthly ATH fetch,
        this isn't about needing more history (200-week SMA is ~4 years,
        comfortably inside the same depth as the daily fetch) - it's about
        each weekly candle matching what Upstox itself considers "the
        week's" OHLCV (e.g. around a holiday-shortened week), rather than
        a pandas resample of daily bars that may draw week boundaries
        slightly differently.
        """
        return self._get_history(instrument_key, "week", years)

    def get_monthly_history(self, instrument_key: str, years: int) -> pd.DataFrame:
        """Fetch monthly OHLCV candles for one instrument, aggregated by
        Upstox itself rather than resampled from daily bars.

        Used only by the Monthly ATH Breakout strategy, which needs a much
        deeper lookback than the other strategies to find a stock's genuine
        all-time high - fetching at monthly granularity keeps that cheap
        (a 25-year lookback is ~300 candles/symbol here vs ~6,300 at daily
        granularity), so it doesn't need to share signals.data.fetch_daily's
        DAILY_HISTORY_YEARS cap.
        """
        return self._get_history(instrument_key, "month", years)

    def _get_history(self, instrument_key: str, interval: str, years: int) -> pd.DataFrame:
        today = date.today()
        from_date = today - timedelta(days=365 * years)
        url = (
            f"{config.UPSTOX_BASE_URL}/historical-candle/"
            f"{instrument_key}/{interval}/{today.isoformat()}/{from_date.isoformat()}"
        )

        last_exc: Exception | None = None
        for attempt in range(1, config.UPSTOX_MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, timeout=20)
                if resp.status_code == 429:
                    wait = 2**attempt
                    logger.warning("Upstox rate-limited us, backing off %ss", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                candles = payload.get("data", {}).get("candles", [])
                break
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(1)
        else:
            raise UpstoxError(f"Failed to fetch candles for {instrument_key}: {last_exc}")

        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(candles, columns=CANDLE_COLUMNS[: len(candles[0])])
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df = df.sort_values("timestamp").set_index("timestamp")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["open", "high", "low", "close", "volume"]]

    def throttle(self) -> None:
        time.sleep(config.UPSTOX_REQUEST_DELAY_SECONDS)
