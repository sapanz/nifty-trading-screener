"""Fully automated Upstox login via TOTP, driving Upstox's real login page
with Playwright rather than guessing their undocumented internal login
API - the API is unpublished and could change shape with no notice, while
the page a human actually uses is under more pressure for Upstox to keep
working. The trade-off: this is still built against selectors that were
NOT verified live (no network access to Upstox from the environment this
was built in) - the first real run may need the selectors below updated
to match what Upstox's login page actually looks like. On failure, a
screenshot is saved to `screenshot_path` (uploaded as a CI artifact by
the workflow) specifically to make that fix fast.

If Upstox changes their login flow, this is the file to update - nothing
else in the codebase needs to know how the login happened, only that it
produces an OAuth authorization `code`.
"""
from __future__ import annotations

import logging
import os
import urllib.parse

import pyotp
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Headless in CI; set HEADLESS=false locally to watch the login flow in a
# real browser window while debugging a selector change.
_HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"

AUTH_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"
STEP_TIMEOUT_MS = 15_000


def _build_dialog_url(client_id: str, redirect_uri: str) -> str:
    return (
        f"{AUTH_DIALOG_URL}?response_type=code&client_id={urllib.parse.quote(client_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
    )


def get_authorization_code(
    client_id: str,
    redirect_uri: str,
    mobile_number: str,
    password: str,
    totp_secret: str,
    screenshot_path: str | None = None,
) -> str:
    """Drive the Upstox login UI headlessly and return the OAuth `code`.

    Uses Playwright's request routing to intercept the final redirect to
    `redirect_uri` - it never needs to actually resolve or be reachable.
    """
    captured: dict[str, str] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=_HEADLESS)
        context = browser.new_context()

        def _capture_redirect(route):
            captured["url"] = route.request.url
            route.fulfill(status=200, body="ok")

        context.route(f"{redirect_uri}*", _capture_redirect)
        page = context.new_page()

        try:
            logger.info("Opening Upstox login dialog")
            page.goto(_build_dialog_url(client_id, redirect_uri), wait_until="networkidle")

            logger.info("Entering mobile number")
            page.fill("#mobileNum", mobile_number)
            page.click("#getOtp")

            logger.info("Entering password/PIN")
            page.wait_for_selector("#pinCode", timeout=STEP_TIMEOUT_MS)
            page.fill("#pinCode", password)
            page.click("#pinContinueBtn")

            logger.info("Entering TOTP code")
            page.wait_for_selector("#totpNum", timeout=STEP_TIMEOUT_MS)
            page.fill("#totpNum", pyotp.TOTP(totp_secret).now())
            page.click("#continueBtn")

            page.wait_for_timeout(3000)  # give the final redirect a moment to fire
        except Exception:
            logger.error("Upstox login flow failed at URL: %s", page.url)
            if screenshot_path:
                page.screenshot(path=screenshot_path, full_page=True)
            raise
        finally:
            browser.close()

    if "url" not in captured:
        if screenshot_path:
            logger.error("No redirect captured - see %s for the page state at the last step.", screenshot_path)
        raise RuntimeError(
            "Never observed a redirect to redirect_uri - the login flow didn't complete "
            "(wrong selector, unexpected extra step, or Upstox changed their login page)."
        )

    query = urllib.parse.urlparse(captured["url"]).query
    code = urllib.parse.parse_qs(query).get("code", [None])[0]
    if not code:
        raise RuntimeError(f"Redirect captured but no `code` param present: {captured['url']}")
    return code
