"""Optional password protection for the published dashboard data.

The dashboard is static GitHub Pages reading JSON from public branches, so a login form alone would only hide
the page, not the data. When the DASHBOARD_PASSWORD secret is set every published JSON file is encrypted
(AES-256-GCM, key = PBKDF2-HMAC-SHA256 of the password) and the page decrypts it in the browser after login.
Unset, files are written as plain JSON exactly as before.

Envelope: {"enc":1,"alg":"AES-256-GCM","kdf":"PBKDF2-SHA256","iter":N,"salt":b64,"iv":b64,"ct":b64,"z":1}
where ct = AES-GCM(zlib(json)). The salt is fixed and public (it only stops precomputed tables); the IV is random
per file.
"""
from __future__ import annotations

import base64
import json
import os
import zlib
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 600_000
SALT = b"nifty-screener-v1"
ENV_VAR = "DASHBOARD_PASSWORD"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@lru_cache(maxsize=4)
def _key(password: str, salt: bytes, iterations: int) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations).derive(password.encode("utf-8"))


def password() -> str | None:
    return os.environ.get(ENV_VAR) or None


def encrypt(obj, pw: str) -> dict:
    iv = os.urandom(12)
    plain = zlib.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"), 9)
    ct = AESGCM(_key(pw, SALT, ITERATIONS)).encrypt(iv, plain, None)
    return {"enc": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": ITERATIONS,
            "salt": _b64(SALT), "iv": _b64(iv), "ct": _b64(ct), "z": 1}


def decrypt(envelope: dict, pw: str):
    key = _key(pw, base64.b64decode(envelope["salt"]), int(envelope["iter"]))
    plain = AESGCM(key).decrypt(base64.b64decode(envelope["iv"]), base64.b64decode(envelope["ct"]), None)
    return json.loads((zlib.decompress(plain) if envelope.get("z") else plain).decode("utf-8"))


def is_encrypted(obj) -> bool:
    return isinstance(obj, dict) and obj.get("enc") == 1 and "ct" in obj


def dumps(obj) -> str:
    """Compact JSON, encrypted when a dashboard password is configured."""
    pw = password()
    return json.dumps(encrypt(obj, pw) if pw else obj, separators=(",", ":"))


def loads(text: str | bytes):
    """Parse a published file; decrypts it when it is an envelope (needs the same password)."""
    obj = json.loads(text)
    if not is_encrypted(obj):
        return obj
    pw = password()
    if not pw:
        raise RuntimeError(f"File is encrypted but {ENV_VAR} is not set")
    return decrypt(obj, pw)


def write_json(path: Path, obj) -> None:
    Path(path).write_text(dumps(obj))
