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
import time

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


RETRIES, RETRY_PAUSE = 3, 3.0          # NSE's archive answers 403 to cloud runners now and then; a pause and another try often gets through


def _fetch_csv_text(session: requests.Session, url: str) -> str:
    last: Exception | None = None
    for attempt in range(RETRIES):
        if attempt:
            time.sleep(RETRY_PAUSE * attempt)
        try:
            resp = session.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last = exc
    raise last  # type: ignore[misc]


def fetch_nifty500_symbols() -> list[str]:
    """Return NSE trading symbols (without exchange suffix) for Nifty 500."""
    return sorted(fetch_nifty500_constituents())


def fetch_nifty500_names() -> dict[str, str]:
    """{NSE symbol: company name} from the same constituent list ("Company Name" column) - used to search the news. {} if no source answers."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=_HEADERS, timeout=20)
    except requests.RequestException:
        pass
    for url in config.NSE_INDEX_LIST_URLS:
        try:
            rows = csv.DictReader(io.StringIO(_fetch_csv_text(session, url)))
            names = {r["Symbol"].strip(): (r.get("Company Name") or "").strip() for r in rows if r.get("Symbol")}
            if len(names) >= 400:
                return names
        except Exception as exc:  # noqa: BLE001
            logger.warning("Company names from %s failed: %s", url, exc)
    return {}


def fetch_extra_constituents() -> tuple[dict[str, str], dict[str, str]]:
    """({NSE symbol: industry}, {NSE symbol: company name}) from the wider NSE lists (config.NSE_EXTRA_LIST_URLS: Nifty Total Market, Microcap 250), merged. Optional context for the
    stocks above the market-cap line that the Nifty 500 does not hold: every source fails soft and a missing one just leaves those stocks without a sector or name."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=_HEADERS, timeout=20)
    except requests.RequestException:
        pass
    industry, names = {}, {}
    for url in config.NSE_EXTRA_LIST_URLS:
        try:
            for row in csv.DictReader(io.StringIO(_fetch_csv_text(session, url))):
                sym = (row.get("Symbol") or "").strip()
                if sym:
                    industry.setdefault(sym, (row.get("Industry") or "").strip())
                    names.setdefault(sym, (row.get("Company Name") or "").strip())
        except Exception as exc:  # noqa: BLE001 - optional source
            logger.warning("Extra constituent list %s failed: %s", url, exc)
    return {k: v for k, v in industry.items() if v}, {k: v for k, v in names.items() if v}


def fetch_nifty500_constituents() -> dict[str, str]:
    """Return {NSE symbol: industry} for Nifty 500, the industry taken from
    the constituent list's own "Industry" column (e.g. "Capital Goods",
    "Financial Services") - used as each stock's sector.

    Raises RuntimeError if none of the known sources could be reached, so
    callers can surface a loud failure (e.g. a Telegram alert) instead of
    silently screening a stale or empty universe.
    """
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=_HEADERS, timeout=20)
    except requests.RequestException as exc:
        logger.warning("Could not warm up NSE session cookies: %s", exc)

    last_error: Exception | None = None
    for url in config.NSE_INDEX_LIST_URLS:
        try:
            text = _fetch_csv_text(session, url)
            reader = csv.DictReader(io.StringIO(text))
            constituents = {
                row["Symbol"].strip(): (row.get("Industry") or "").strip()
                for row in reader
                if row.get("Symbol")
            }
            if len(constituents) >= 400:  # sanity check, list should have ~500 rows
                return constituents
            last_error = RuntimeError(
                f"{url} returned only {len(constituents)} symbols, expected ~500"
            )
        except Exception as exc:  # noqa: BLE001 - we want to try the next mirror
            last_error = exc
            logger.warning("Failed to fetch Nifty 500 list from %s: %s", url, exc)

    raise RuntimeError(
        "Could not fetch the Nifty 500 constituent list from any known NSE source"
    ) from last_error


def to_yf_ticker(nse_symbol: str) -> str:
    return f"{nse_symbol}{config.YF_TICKER_SUFFIX}"
