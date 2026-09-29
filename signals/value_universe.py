"""Fetches the "value stock" universe from screener.in's custom-screen
query feature (VALUE_SCREEN_QUERY in config.py), for Weekly Value Stocks
Breakout's fundamental gate - see signals/strategies/value_breakout.py.

screener.in has no official/documented API - this scrapes its public
custom-screen results page. A first real run (via GitHub Actions, which
has actual network access unlike this dev sandbox) confirmed the
original endpoint guessed here, `/screens/new/`, doesn't exist (404) -
`/screen/new/` is the human-facing "create a new screen" page, which
loads its live query preview asynchronously from a separate endpoint.
This now points at that endpoint, `/screen/raw/`, which returns just the
results table's HTML rather than a full page - but this specific path is
still **unverified against the live site**, since this dev sandbox's
network egress is blocked to screener.in. Likely failure points once it
is tested for real:
  - screener.in may cap how many rows an unauthenticated request sees
    (a logged-in/premium session could see the full list; this doesn't
    log in at all)
  - if the results table is rendered client-side via JavaScript rather
    than server-rendered HTML, a plain `requests.get` won't see any rows
    at all - it would need a headless-browser fetch instead (see
    signals/upstox_login.py for the Playwright pattern already used
    elsewhere in this project for exactly that kind of site)
  - the page's HTML structure/class names changing over time

A failure here (network error, zero results, structure it can't parse)
raises rather than silently returning an empty set - an empty/wrong
universe would make Weekly Value Stocks Breakout look validly-empty
rather than broken. Callers are expected to catch it and disable the
strategy for that run instead, the same pattern
data.fetch_fo_eligible_symbols already uses for Price Action Breakout's
short leg.
"""
from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

from signals import config

logger = logging.getLogger(__name__)

SCREENER_URL = "https://www.screener.in/screen/raw/"
# A real browser User-Agent - screener.in (like many sites) may reject or
# serve a different/reduced response to the default python-requests UA.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
_COMPANY_LINK_RE = re.compile(r"^/company/([A-Z0-9&\-]+)/")


def fetch_value_stock_symbols(query: str = config.VALUE_SCREEN_QUERY, timeout: int = 30) -> set[str]:
    """Runs `query` as a screener.in custom screen (its own query syntax,
    e.g. "Profit growth > 25 AND Debt to equity < 0.5 AND Market
    Capitalization > 5000") and returns the NSE trading symbols of every
    matching company.

    Symbols are pulled from each result row's /company/<SYMBOL>/ link
    rather than its displayed name - screener.in's display names don't
    always match NSE trading symbols exactly (punctuation, suffixes),
    while the URL slug reliably does."""
    response = requests.get(SCREENER_URL, params={"query": query}, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    symbols: set[str] = set()
    for link in soup.find_all("a", href=True):
        match = _COMPANY_LINK_RE.match(link["href"])
        if match:
            symbols.add(match.group(1))

    if not symbols:
        raise RuntimeError(
            "screener.in query returned 0 matching symbols - either the query genuinely "
            "matched nothing, or the page structure/login requirement changed and this "
            "scraper needs updating (see signals/value_universe.py's module docstring)."
        )
    logger.info("Value stock screen matched %d symbols", len(symbols))
    return symbols
