"""Upstox OAuth authorization-code <-> access-token exchange.

Shared by both the manual one-tap refresh tool (tools/refresh_upstox_token.py)
and the automated TOTP login used in CI (signals/upstox_login.py) - the
exchange step itself is Upstox's one officially documented, stable part
of this flow, whatever gets us the `code` in the first place.
"""
from __future__ import annotations

import requests

TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def exchange_code_for_token(code: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
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
        raise RuntimeError(f"Upstox token exchange didn't return an access_token: {payload}")
    return payload["access_token"]
