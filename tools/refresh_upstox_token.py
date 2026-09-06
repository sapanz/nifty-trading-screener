#!/usr/bin/env python3
"""Run this once a day (before 5pm IST) to refresh the Upstox access token
the GitHub Actions screener uses.

No PIN, password, or TOTP secret is stored anywhere - this does the same
OAuth login Upstox's own web app uses, in your browser, and captures the
result locally. What it automates is only the boring parts: opening the
right URL and pushing the resulting token to your GitHub secret for you.

One-time setup:
  1. pip install -r tools/requirements.txt
  2. Register a redirect URI on your Upstox app (developer.upstox.com)
     matching UPSTOX_REDIRECT_URI below, e.g. http://localhost:5000/callback
  3. Copy tools/.env.example to tools/.env and fill in:
       UPSTOX_CLIENT_ID, UPSTOX_CLIENT_SECRET, UPSTOX_REDIRECT_URI
       GITHUB_TOKEN   - a fine-grained PAT scoped to ONLY this repo with
                        "Secrets" permission set to "Read and write"
       GITHUB_OWNER, GITHUB_REPO

Daily usage:
  python tools/refresh_upstox_token.py
  -> opens your browser to the Upstox login page
  -> log in as usual (with whatever 2FA you normally use)
  -> the script captures the redirect, exchanges it for an access token,
     and pushes it straight to the UPSTOX_ACCESS_TOKEN GitHub secret
"""
from __future__ import annotations

import http.server
import os
import sys
import threading
import urllib.parse
import webbrowser

import requests
from dotenv import load_dotenv
from nacl import encoding, public

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

AUTH_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
CALLBACK_TIMEOUT_SECONDS = 300


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing {name} - set it in tools/.env (see tools/.env.example)")
    return value


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    auth_code: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - required name by http.server
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _CallbackHandler.auth_code = params.get("code", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if _CallbackHandler.auth_code:
            self.wfile.write(b"<h2>Upstox login captured. You can close this tab.</h2>")
        else:
            error = params.get("error_description", params.get("error", ["unknown error"]))[0]
            self.wfile.write(f"<h2>Login failed: {error}. Close this tab and try again.</h2>".encode())

    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass


def _get_auth_code(redirect_uri: str, client_id: str) -> str:
    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 80
    server = http.server.HTTPServer((parsed.hostname or "localhost", port), _CallbackHandler)

    thread = threading.Thread(target=server.handle_request)
    thread.start()

    dialog_url = (
        f"{AUTH_DIALOG_URL}?response_type=code&client_id={urllib.parse.quote(client_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
    )
    print(f"Opening browser for Upstox login:\n  {dialog_url}\n")
    webbrowser.open(dialog_url)

    thread.join(timeout=CALLBACK_TIMEOUT_SECONDS)
    server.server_close()

    if not _CallbackHandler.auth_code:
        sys.exit(f"Timed out after {CALLBACK_TIMEOUT_SECONDS}s waiting for the Upstox login redirect.")
    return _CallbackHandler.auth_code


def _exchange_code_for_token(code: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "access_token" not in payload:
        sys.exit(f"Upstox token exchange didn't return an access_token: {payload}")
    return payload["access_token"]


def _push_github_secret(owner: str, repo: str, github_token: str, secret_name: str, secret_value: str) -> None:
    api_base = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    key_resp = requests.get(f"{api_base}/actions/secrets/public-key", headers=headers, timeout=20)
    key_resp.raise_for_status()
    key_payload = key_resp.json()

    public_key = public.PublicKey(key_payload["key"].encode("utf-8"), encoding.Base64Encoder())
    encrypted = public.SealedBox(public_key).encrypt(secret_value.encode("utf-8"))
    encrypted_b64 = encoding.Base64Encoder().encode(encrypted).decode("utf-8")

    put_resp = requests.put(
        f"{api_base}/actions/secrets/{secret_name}",
        headers=headers,
        json={"encrypted_value": encrypted_b64, "key_id": key_payload["key_id"]},
        timeout=20,
    )
    put_resp.raise_for_status()


def main() -> None:
    client_id = _require_env("UPSTOX_CLIENT_ID")
    client_secret = _require_env("UPSTOX_CLIENT_SECRET")
    redirect_uri = _require_env("UPSTOX_REDIRECT_URI")
    github_token = _require_env("GITHUB_TOKEN")
    github_owner = _require_env("GITHUB_OWNER")
    github_repo = _require_env("GITHUB_REPO")

    code = _get_auth_code(redirect_uri, client_id)
    token = _exchange_code_for_token(code, client_id, client_secret, redirect_uri)
    _push_github_secret(github_owner, github_repo, github_token, "UPSTOX_ACCESS_TOKEN", token)

    print("UPSTOX_ACCESS_TOKEN updated on GitHub. Today's runs are good to go.")


if __name__ == "__main__":
    main()
