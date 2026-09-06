"""Minimal Telegram Bot API sender with chunking for long screener output."""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3800  # margin under Telegram's 4096-char cap


def _chunk(text: str, max_len: int) -> list[str]:
    lines = text.split("\n")
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            current = line
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
