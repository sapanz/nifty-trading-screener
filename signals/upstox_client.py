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

Only the "day" interval is fetched here; weekly and monthly series used
by the other strategies are derived by resampling that daily data
(signals/data.py) rather than making separate calls per timeframe.

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

        symbol_col = next((c for c in ("tradingsymbol", "trading_symbol") if c in df.columns), None)
        key_col = next((c for c in ("instrument_key", "instrumentkey") if c in df.columns), None)
        if symbol_col is None or key_col is None:
            raise UpstoxError(
                f"Unexpected instrument master columns: {list(df.columns)}. "
                "Upstox may have changed the file format - check developer.upstox.com."
            )

        segment_col = next((c for c in ("segment", "exchange") if c in df.columns), None)
        type_col = next((c for c in ("instrument_type", "instrumenttype") if c in df.columns), None)
        if segment_col is not None:
            df = df[df[segment_col].astype(str).str.upper() == config.UPSTOX_EQUITY_SEGMENT]
        if type_col is not None:
            df = df[df[type_col].astype(str).str.upper() == "EQ"]

        return dict(zip(df[symbol_col].astype(str).str.strip(), df[key_col].astype(str).str.strip()))

    def get_daily_history(self, instrument_key: str, years: int) -> pd.DataFrame:
        """Fetch daily OHLCV candles for one instrument.

        Returns a DataFrame indexed by date, sorted oldest -> newest, with
        columns open/high/low/close/volume.
        """
        today = date.today()
        from_date = today - timedelta(days=365 * years)
        url = (
            f"{config.UPSTOX_BASE_URL}/historical-candle/"
            f"{instrument_key}/day/{today.isoformat()}/{from_date.isoformat()}"
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
