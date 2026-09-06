"""Direct (unauthenticated) fetch of daily OHLCV from NSE's own historical-data API.

No broker account, API key, or daily token refresh needed - this is the
same public JSON endpoint https://www.nseindia.com/get-quotes/equity uses
to render its historical-data chart. The trade-off versus a broker API is
that this endpoint is undocumented, rate-limits aggressively, and blocks
obviously bot-like traffic - so requests are spaced out, sessions are
re-warmed on 401/403, and a failed symbol/chunk is skipped rather than
treated as fatal.

If NSE changes this endpoint's shape, only this module needs fixing - the
rest of the codebase just sees a plain OHLCV DataFrame per symbol.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd
import requests

from signals import config

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/equity",
}

# NSE's JSON field names for this endpoint, with a couple of historically
# seen aliases - defensive because this is a reverse-engineered API, not
# a documented contract.
_COLUMN_ALIASES = {
    "open": ("CH_OPENING_PRICE", "CH_OPEN_PRICE"),
    "high": ("CH_TRADE_HIGH_PRICE",),
    "low": ("CH_TRADE_LOW_PRICE",),
    "close": ("CH_CLOSING_PRICE",),
    "volume": ("CH_TOT_TRADED_QTY", "CH_TOT_TRADED_VAL"),
    "timestamp": ("CH_TIMESTAMP",),
}


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    resolved = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in columns:
                resolved[canonical] = alias
                break
    return resolved


class NseClient:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._warm = False

    def _ensure_session(self) -> None:
        if self._warm:
            return
        try:
            self._session.get("https://www.nseindia.com", timeout=20)
            self._warm = True
        except requests.RequestException as exc:
            logger.warning("NSE session warm-up failed: %s", exc)

    def _fetch_chunk(self, symbol: str, start: date, end: date) -> pd.DataFrame | None:
        params = {
            "symbol": symbol,
            "series": '["EQ"]',
            "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"),
        }

        for attempt in range(1, config.NSE_MAX_RETRIES + 1):
            try:
                resp = self._session.get(config.NSE_HISTORICAL_URL, params=params, timeout=20)
                if resp.status_code in (401, 403):
                    logger.warning("NSE rejected session for %s (attempt %d), re-warming", symbol, attempt)
                    self._warm = False
                    self._ensure_session()
                    time.sleep(1)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                records = payload.get("data", [])
                if not records:
                    return None

                raw_df = pd.DataFrame(records)
                col_map = _resolve_columns(list(raw_df.columns))
                missing = {"open", "high", "low", "close", "volume", "timestamp"} - col_map.keys()
                if missing:
                    raise RuntimeError(
                        f"NSE historical response is missing expected fields {missing}; "
                        f"got columns {list(raw_df.columns)}. NSE may have changed this API."
                    )

                df = raw_df.rename(columns={v: k for k, v in col_map.items()})[list(col_map.keys())]
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp").sort_index()
                for c in ("open", "high", "low", "close", "volume"):
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                return df[["open", "high", "low", "close", "volume"]]
            except requests.RequestException as exc:
                logger.warning("NSE fetch failed for %s [%s..%s] attempt %d: %s", symbol, start, end, attempt, exc)
                time.sleep(2**attempt)
        return None

    def get_daily_history(self, symbol: str, years: int) -> pd.DataFrame:
        """Fetch up to `years` of daily OHLCV, chunked to stay under NSE's per-request range limit."""
        self._ensure_session()
        end = date.today()
        overall_start = end - timedelta(days=365 * years)

        frames: list[pd.DataFrame] = []
        chunk_end = end
        while chunk_end > overall_start:
            chunk_start = max(overall_start, chunk_end - timedelta(days=config.NSE_CHUNK_DAYS))
            frame = self._fetch_chunk(symbol, chunk_start, chunk_end)
            if frame is not None and not frame.empty:
                frames.append(frame)
            chunk_end = chunk_start - timedelta(days=1)
            time.sleep(config.NSE_REQUEST_DELAY_SECONDS)

        if not frames:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.concat(frames).sort_index()
        return df[~df.index.duplicated(keep="last")]
