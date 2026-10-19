"""Symbols with a corporate event (results, board meeting, dividend, split,
fund raising...) near a given date, from NSE's own event calendar - used
to keep event-driven volatility out of the Intraday Watchlist.

NSE's JSON API sits behind the same bot protection as the rest of
nseindia.com: a request without the cookies set by first loading the
homepage gets a 401/403, so this warms up a session first (the same trick
universe.py uses for the constituent list). Unverified against the live
API from the environment this was built in (its network egress to NSE is
blocked), so a failure here raises loudly rather than returning an empty
set - an empty set would silently mean "no events", the most dangerous
wrong answer. Callers are expected to catch it and flag the watchlist as
unfiltered instead.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from signals import config

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar",
}


def fetch_event_symbols(
    around: date,
    lookback_days: int = config.INTRADAY_EVENT_LOOKBACK_DAYS,
    lookahead_days: int = config.INTRADAY_EVENT_LOOKAHEAD_DAYS,
    timeout: int = 20,
) -> set[str]:
    """NSE symbols with any calendar event from `lookback_days` before
    `around` to `lookahead_days` after it, inclusive."""
    session = requests.Session()
    session.headers.update(_HEADERS)
    try:
        session.get("https://www.nseindia.com", timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("Could not warm up NSE session cookies: %s", exc)

    params = {
        "index": "equities",
        "from_date": (around - timedelta(days=lookback_days)).strftime("%d-%m-%Y"),
        "to_date": (around + timedelta(days=lookahead_days)).strftime("%d-%m-%Y"),
    }
    resp = session.get(config.NSE_EVENT_CALENDAR_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    try:
        events = resp.json()
    except ValueError as exc:
        raise RuntimeError(
            f"NSE event calendar returned non-JSON (status {resp.status_code}, "
            f"first 300 chars: {resp.text[:300]!r})"
        ) from exc
    if not isinstance(events, list):
        raise RuntimeError(f"NSE event calendar returned unexpected shape: {str(events)[:300]!r}")

    symbols = {str(e["symbol"]).strip() for e in events if isinstance(e, dict) and e.get("symbol")}
    if events and not symbols:
        raise RuntimeError(f"NSE event calendar rows have no 'symbol' field: {events[0]!r}")
    if not events:
        # Possible on a genuinely quiet week, but rare enough (and
        # indistinguishable from a changed API) to be worth a visible log line.
        logger.warning("NSE event calendar returned no events for %s", params)
    logger.info("NSE event calendar: %d events across %d symbols (%s to %s)",
                len(events), len(symbols), params["from_date"], params["to_date"])
    return symbols
