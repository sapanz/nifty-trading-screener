"""Fetches the "value stock" universe from screener.in's custom-screen
query feature (VALUE_SCREEN_QUERY in config.py), for Weekly Value Stocks
Breakout's fundamental gate - see signals/strategies/value_breakout.py.

screener.in has no official/documented API - this scrapes its public
custom-screen results page. Two real runs (via GitHub Actions, which has
actual network access unlike this dev sandbox) have already corrected two
wrong guesses:
  1. The endpoint isn't `/screens/new/` (404) - `/screen/new/` is the
     human-facing "create a new screen" page, which loads its live query
     preview asynchronously from `/screen/raw/`.
  2. `/screen/raw/` redirects anonymous requests to `/register/` - running
     an ad-hoc custom-screen query requires a logged-in screener.in
     account (a free one, not necessarily premium). Anonymous access
     genuinely cannot do this, no matter the URL/headers.

This now logs in first (SCREENER_EMAIL/SCREENER_PASSWORD, a plain
Django-style CSRF-protected form login - see `_login`) and reuses that
session's cookies for the query request. **Still unverified against the
live site**: this dev sandbox's network egress is blocked to screener.in,
so the login flow itself (field names, CSRF cookie name, success/failure
detection) is built from the standard Django auth pattern, not confirmed
against screener.in's actual login form. Likely failure points once it is
tested for real:
  - field names other than "username"/"password", or a CSRF token that
    isn't in a `csrftoken` cookie
  - bot protection (a captcha, rate limiting) on the login endpoint that a
    plain `requests.Session` can't satisfy - would need a headless-browser
    login instead (see signals/upstox_login.py's Playwright pattern)
  - screener.in may still cap how many rows even a logged-in-but-free
    account sees
  - the results table's HTML structure/class names changing over time

A failure here (login failure, network error, zero results, structure it
can't parse) raises rather than silently returning an empty set - an
empty/wrong universe would make Weekly Value Stocks Breakout look
validly-empty rather than broken. Callers are expected to catch it and
disable the strategy for that run instead, the same pattern
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

LOGIN_URL = "https://www.screener.in/login/"
SCREENER_URL = "https://www.screener.in/screen/raw/"
# A real browser User-Agent - screener.in (like many sites) may reject or
# serve a different/reduced response to the default python-requests UA.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
_COMPANY_LINK_RE = re.compile(r"^/company/([A-Z0-9&\-]+)/")


def _login(email: str, password: str, timeout: int) -> requests.Session:
    """Logs in to screener.in and returns the authenticated session.

    Standard Django login pattern: GET the login page to receive a
    `csrftoken` cookie, then POST credentials plus that same token back
    to the same URL. Untested against the live form - see this module's
    docstring for what's most likely to need fixing first."""
    session = requests.Session()
    session.headers.update(_HEADERS)

    login_page = session.get(LOGIN_URL, timeout=timeout)
    login_page.raise_for_status()
    csrf_token = session.cookies.get("csrftoken")
    if not csrf_token:
        raise RuntimeError(
            "screener.in login page didn't set a csrftoken cookie - its login "
            "form has likely changed (see signals/value_universe.py's module docstring)."
        )

    response = session.post(
        LOGIN_URL,
        data={"username": email, "password": password, "csrfmiddlewaretoken": csrf_token},
        headers={"Referer": LOGIN_URL},
        timeout=timeout,
    )
    response.raise_for_status()
    if response.url.startswith(LOGIN_URL):
        # A failed Django login re-renders the same login URL with form
        # errors instead of redirecting away from it - a reliable enough
        # signal without needing to parse the error message itself.
        raise RuntimeError(
            "screener.in login failed - check SCREENER_EMAIL/SCREENER_PASSWORD, "
            "or the login flow has changed (see signals/value_universe.py's module docstring)."
        )
    return session


def fetch_value_stock_symbols(email: str, password: str, query: str = config.VALUE_SCREEN_QUERY, timeout: int = 30) -> set[str]:
    """Logs in to screener.in and runs `query` as a custom screen (its own
    query syntax, e.g. "Profit growth > 25 AND Debt to equity < 0.5 AND
    Market Capitalization > 5000"), returning the NSE trading symbols of
    every matching company.

    Symbols are pulled from each result row's /company/<SYMBOL>/ link
    rather than its displayed name - screener.in's display names don't
    always match NSE trading symbols exactly (punctuation, suffixes),
    while the URL slug reliably does."""
    session = _login(email, password, timeout)
    response = session.get(SCREENER_URL, params={"query": query}, timeout=timeout)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    symbols: set[str] = set()
    for link in soup.find_all("a", href=True):
        match = _COMPANY_LINK_RE.match(link["href"])
        if match:
            symbols.add(match.group(1))

    if not symbols:
        # Logged (not just raised) so a real CI run's logs show exactly what
        # screener.in sent back - the last two failures here were wrong
        # guesses (wrong endpoint, then a login wall), so diagnose from
        # evidence instead of guessing blind again.
        snippet = response.text[:1000].replace("\n", " ")
        logger.error(
            "screener.in returned 0 symbols. status=%d final_url=%s content-length=%s snippet=%r",
            response.status_code, response.url, len(response.text), snippet,
        )
        raise RuntimeError(
            "screener.in query returned 0 matching symbols - either the query genuinely "
            "matched nothing, or the page structure changed and this scraper needs "
            "updating (see signals/value_universe.py's module docstring)."
        )
    logger.info("Value stock screen matched %d symbols", len(symbols))
    return symbols
