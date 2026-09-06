"""Shared plumbing for the runner scripts: env vars, logging, Telegram reporting."""
from __future__ import annotations

import logging
import os
from typing import Callable

from signals.formatting import format_run_error
from signals.telegram import send_message


def get_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def notify_error(title: str, emoji: str, error: str) -> None:
    token = get_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_env("TELEGRAM_CHAT_ID")
    send_message(token, chat_id, format_run_error(title, emoji, error))


def run_and_notify(title: str, emoji: str, builder: Callable[[], str]) -> None:
    """Build a strategy's message and send it.

    On failure, sends the error to Telegram too (so a broken run is
    noticed without having to check GitHub Actions logs) and re-raises so
    the workflow itself is marked failed.
    """
    token = get_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_env("TELEGRAM_CHAT_ID")
    try:
        text = builder()
    except Exception as exc:  # noqa: BLE001 - we want to report then propagate
        send_message(token, chat_id, format_run_error(title, emoji, str(exc)))
        raise
    send_message(token, chat_id, text)
