"""Turn a list of Signal objects into a Telegram-ready HTML message."""
from __future__ import annotations

import html
from datetime import date

from signals.models import Signal


def _fmt_targets(targets: list[float]) -> str:
    return " | ".join(f"T{i+1}: {t:.2f}" for i, t in enumerate(targets))


def format_strategy_message(title: str, emoji: str, signals: list[Signal], run_date: date) -> str:
    date_str = run_date.strftime("%d %b %Y")
    header = f"{emoji} <b>{html.escape(title)}</b> — {date_str}"

    if not signals:
        return f"{header}\nNo stocks matched today."

    lines = [header, f"{len(signals)} stock(s) matched\n"]
    for i, sig in enumerate(signals, start=1):
        symbol = html.escape(sig.symbol)
        risk_pct = (sig.risk_per_share / sig.entry * 100) if sig.entry else 0
        lines.append(f"{i}. <b>{symbol}</b>")
        lines.append(f"   Entry: {sig.entry:.2f} | SL: {sig.stop_loss:.2f} ({risk_pct:.1f}% risk)")
        if sig.targets:  # some strategies (e.g. Daily Swing) use a trailing stop instead of a fixed target
            lines.append(f"   {_fmt_targets(sig.targets)}")
        if sig.note:
            lines.append(f"   <i>{html.escape(sig.note)}</i>")
    return "\n".join(lines)


def format_run_error(title: str, emoji: str, error: str) -> str:
    return f"{emoji} <b>{html.escape(title)}</b>\n⚠️ Run failed: {html.escape(error)}"
