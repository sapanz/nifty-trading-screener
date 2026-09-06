"""Fully automated Upstox login via TOTP, driving Upstox's real login page
with Playwright rather than guessing their undocumented internal login
API - the API is unpublished and could change shape with no notice, while
the page a human actually uses is under more pressure for Upstox to keep
working. The trade-off: this is still built against selectors that were
NOT verified live (no network access to Upstox from the environment this
was built in) - the first real run may need the selectors below updated
to match what Upstox's login page actually looks like. Whenever the
expected redirect never arrives - whether or not an exception was
raised, since a page can happily sit on an unexpected extra step without
erroring - a screenshot and the full page HTML are saved next to
`screenshot_path` (uploaded as CI artifacts by the workflow) specifically
to make that fix fast.

Assumes a 3-step login, confirmed against a real account: mobile number;
a verification code satisfied by a TOTP code from an authenticator app
(Upstox's account security settings must have TOTP enabled as the 2FA
method, replacing SMS OTP, for this to be accepted instead of a texted
OTP); then the account's 6-digit login PIN on a "Hi <name>, welcome
back" screen before Upstox will complete the API authorization.

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


def _save_diagnostics(page, screenshot_path: str | None) -> None:
    if not screenshot_path:
        return
    try:
        page.screenshot(path=screenshot_path, full_page=True)
        html_path = os.path.splitext(screenshot_path)[0] + ".html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        logger.error("Saved failure diagnostics: %s, %s (page URL: %s)", screenshot_path, html_path, page.url)
    except Exception as diag_exc:  # noqa: BLE001 - diagnostics must never mask the real error
        logger.error("Could not save failure diagnostics: %s", diag_exc)


def get_authorization_code(
    client_id: str,
    redirect_uri: str,
    mobile_number: str,
    totp_secret: str,
    pin: str,
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
            # Plain string-prefix check rather than Playwright's glob
            # matching - a glob pattern here was silently failing to match
            # the real redirect (trailing slash / query string edge cases),
            # letting the browser try to actually load a URL that doesn't
            # resolve to anything (chrome-error://chromewebdata).
            if route.request.url.startswith(redirect_uri):
                captured["url"] = route.request.url
                route.fulfill(status=200, body="ok")
            else:
                route.continue_()

        context.route("**/*", _capture_redirect)
        page = context.new_page()

        try:
            logger.info("Opening Upstox login dialog")
            page.goto(_build_dialog_url(client_id, redirect_uri), wait_until="networkidle")

            logger.info("Entering mobile number")
            page.fill("#mobileNum", mobile_number)
            page.click("#getOtp")

            logger.info("Entering TOTP code")
            page.wait_for_selector("#otpNum", timeout=STEP_TIMEOUT_MS)
            page.fill("#otpNum", pyotp.TOTP(totp_secret).now())
            page.click("#continueBtn")

            logger.info("Entering login PIN")
            page.wait_for_selector("#pinCode", timeout=STEP_TIMEOUT_MS)
            page.fill("#pinCode", pin)
            page.click("#pinContinueBtn")

            page.wait_for_timeout(3000)  # give the final redirect a moment to fire
        except Exception:
            logger.error("Upstox login flow raised an exception")
            raise
        finally:
            if "url" not in captured:
                _save_diagnostics(page, screenshot_path)
            browser.close()

    if "url" not in captured:
        raise RuntimeError(
            "Never observed a redirect to redirect_uri - the login flow didn't complete "
            "(wrong selector, an unexpected extra step, or Upstox changed their login page). "
            "Check the uploaded screenshot/HTML artifacts for the page state."
        )

    query = urllib.parse.urlparse(captured["url"]).query
    code = urllib.parse.parse_qs(query).get("code", [None])[0]
    if not code:
        raise RuntimeError(f"Redirect captured but no `code` param present: {captured['url']}")
    return code
