#!/usr/bin/env python3
"""CI step: log into Upstox automatically via TOTP and hand a fresh access
token to the rest of the job.

The access token itself is never stored as a GitHub secret - only the
long-lived login credentials are (mobile number, password, TOTP secret,
client id/secret). This script writes the derived token to $GITHUB_ENV
(masked in logs) so the next step in the same job can read it as a plain
environment variable; it exists only for the lifetime of that job run.

Required environment variables:
  UPSTOX_CLIENT_ID, UPSTOX_CLIENT_SECRET, UPSTOX_REDIRECT_URI
  UPSTOX_MOBILE_NUMBER, UPSTOX_PASSWORD, UPSTOX_TOTP_SECRET

This automates Upstox's login *page*, not a documented API - if Upstox
changes it, this breaks. See signals/upstox_login.py for the selectors
to fix, and check the screenshot artifact this step's workflow step
uploads on failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import runtime  # noqa: E402
from signals.upstox_login import get_authorization_code  # noqa: E402
from signals.upstox_oauth import exchange_code_for_token  # noqa: E402

SCREENSHOT_PATH = "upstox_login_failure.png"
TITLE = "Upstox TOTP login"
EMOJI = "🔐"


def main() -> None:
    runtime.setup_logging()
    client_id = runtime.get_env("UPSTOX_CLIENT_ID")
    client_secret = runtime.get_env("UPSTOX_CLIENT_SECRET")
    redirect_uri = runtime.get_env("UPSTOX_REDIRECT_URI")
    mobile_number = runtime.get_env("UPSTOX_MOBILE_NUMBER")
    password = runtime.get_env("UPSTOX_PASSWORD")
    totp_secret = runtime.get_env("UPSTOX_TOTP_SECRET")

    try:
        code = get_authorization_code(
            client_id,
            redirect_uri,
            mobile_number,
            password,
            totp_secret,
            screenshot_path=SCREENSHOT_PATH,
        )
        token = exchange_code_for_token(code, client_id, client_secret, redirect_uri)
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise

    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        print(f"::add-mask::{token}")
        with open(github_env, "a") as f:
            f.write(f"UPSTOX_ACCESS_TOKEN={token}\n")
        print("Logged in to Upstox; UPSTOX_ACCESS_TOKEN set for the rest of this job.")
    else:
        # Local debugging fallback - not used by the workflow.
        print(token)


if __name__ == "__main__":
    main()
