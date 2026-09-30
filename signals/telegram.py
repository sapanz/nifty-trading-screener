"""Minimal Telegram Bot API sender with chunking for long screener output."""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3800  # margin under Telegram's 4096-char cap


def _chunk(text: str, max_len: int) -> list[str]:
    """Split on newlines first, then hard-split any single line that's
    still longer than max_len on its own (e.g. a strategy summary line
    listing many open trades as one long comma-separated string) - a
    single such line used to pass through as its own oversized chunk,
    which Telegram then rejected outright with "message is too long"
    rather than sending it truncated, silently dropping that chunk (and,
    since send_message raises on any non-2xx response, killing the whole
    run). No chunk this function returns can exceed max_len, regardless
    of what's fed in.
    """
    lines = text.split("\n")
    chunks: list[str] = []
    current = ""
    for line in lines:
        pieces = [line[i : i + max_len] for i in range(0, len(line), max_len)] or [""]
        for piece in pieces:
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) > max_len:
                if current:
                    chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML") -> None:
    url = TELEGRAM_API.format(token=token)
    for chunk in _chunk(text, MAX_LEN):
        resp = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not resp.ok:
            logger.error("Telegram send failed: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()
