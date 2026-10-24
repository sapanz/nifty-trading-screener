"""Fetch the current Nifty 500 constituent list from NSE.

NSE blocks bare requests without browser-like headers, so we open a
session against the homepage first to pick up cookies before hitting the
CSV endpoint. Two mirror URLs are tried since NSE has moved this file
between subdomains before.
"""
from __future__ import annotations

import csv
import io
import logging

import requests

from signals import config

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_csv_text(session: requests.Session, url: str) -> str:
    resp = session.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def _fetch_nifty500_rows() -> list[dict]:
    """The rows of NSE's Nifty 500 constituent CSV (columns: Company Name,
    Industry, Symbol, Series, ISIN Code), from the first mirror that returns a
    plausible list. Raises RuntimeError if none of the known sources could be
    reached, so callers can surface a loud failure instead of silently
    screening a stale or empty universe."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=_HEADERS, timeout=20)
    except requests.RequestException as exc:
        logger.warning("Could not warm up NSE session cookies: %s", exc)

    last_error: Exception | None = None
    for url in config.NSE_INDEX_LIST_URLS:
        try:
            text = _fetch_csv_text(session, url)
            rows = [row for row in csv.DictReader(io.StringIO(text)) if row.get("Symbol")]
            if len(rows) >= 400:  # sanity check, list should have ~500 rows
                return rows
            last_error = RuntimeError(
                f"{url} returned only {len(rows)} symbols, expected ~500"
            )
        except Exception as exc:  # noqa: BLE001 - we want to try the next mirror
            last_error = exc
            logger.warning("Failed to fetch Nifty 500 list from %s: %s", url, exc)

    raise RuntimeError(
        "Could not fetch the Nifty 500 constituent list from any known NSE source"
    ) from last_error


def fetch_nifty500_symbols() -> list[str]:
    """Return NSE trading symbols (without exchange suffix) for Nifty 500."""
    return sorted({row["Symbol"].strip() for row in _fetch_nifty500_rows()})


def fetch_sector_map() -> dict[str, str]:
    """{NSE trading symbol: NSE "Industry"} for the Nifty 500 (about 20 broad
    industries, e.g. "Financial Services", "Information Technology"). Coarse -
    "Financial Services" lumps banks, NBFCs and insurers - but free and from the
    same file the universe already comes from."""
    return {row["Symbol"].strip(): (row.get("Industry") or "").strip()
            for row in _fetch_nifty500_rows() if (row.get("Industry") or "").strip()}


def to_yf_ticker(nse_symbol: str) -> str:
    return f"{nse_symbol}{config.YF_TICKER_SUFFIX}"
