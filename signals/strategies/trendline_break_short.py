"""Trendline Break Short: sell a stock whose 5-minute candle just closed below
a rising support trendline.

Per explicit direction: find when a trendline breaks, and where several
stocks break at once, rank them by the *width* of the trendline (the wider
line wins) - a line that has held across more bars is a more meaningful
level to lose. The line geometry lives in signals/trendlines.py; this module
turns a break into a Signal (entry = the break candle's close, stop above the
highest high since the line's last touch, target a fixed multiple of the risk)
and does the cross-stock ranking.

Only meant for same-session use: the line is drawn through the current
session's swing lows, and `scan` looks only at the latest session in each
frame. Callers pass *completed* candles only.
"""
from __future__ import annotations

from datetime import time

import pandas as pd

from signals import config
from signals.models import Signal
from signals.trendlines import Break, find_break

NAME = "trendline_break_short"


def _hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def in_entry_window(bar_start: pd.Timestamp) -> bool:
    return _hhmm(config.TRENDLINE_FIRST_ENTRY) <= bar_start.time() <= _hhmm(config.TRENDLINE_LAST_ENTRY)


def session_arrays(df: pd.DataFrame):
    """The latest session's bars as numpy arrays (open, high, low, close, volume)."""
    day = df[df.index.normalize() == df.index[-1].normalize()]
    return day, day["open"].to_numpy(), day["high"].to_numpy(), day["low"].to_numpy(), \
        day["close"].to_numpy(), day["volume"].to_numpy()


def to_signal(symbol: str, brk: Break, bar_start: pd.Timestamp, volume_ratio: float | None = None) -> Signal:
    extra = {
        "width_bars": brk.width_bars, "touches": brk.touches, "rise_pct": round(brk.rise_pct, 3),
        "break_depth_pct": round(brk.break_depth_pct, 3), "risk_pct": round(brk.risk_pct, 3),
        "bar_time": bar_start.strftime("%H:%M"),
    }
    if volume_ratio is not None:
        extra["vol_ratio"] = round(volume_ratio, 2)
    return Signal(
        symbol=symbol, entry=brk.entry, stop_loss=brk.stop, targets=[brk.target], direction="short",
        # wider line first; more touches breaks ties
        sort_key=brk.width_bars + brk.touches / 100,
        note=(f"Broke {brk.width_bars * config.TRENDLINE_INTERVAL_MINUTES}-min rising trendline "
              f"({brk.touches} touches)"),
        extra=extra, candle_date=bar_start.date(),
    )


def scan(intraday_data: dict[str, pd.DataFrame]) -> list[Signal]:
    """Signals where the *latest* candle of the latest session breaks a
    trendline, widest line first."""
    signals: list[Signal] = []
    for symbol, df in intraday_data.items():
        if df.empty:
            continue
        day, o, h, l, c, v = session_arrays(df)
        t = len(day) - 1
        if not in_entry_window(day.index[t]):
            continue
        brk = find_break(o, h, l, c, t)
        if brk is None:
            continue
        window = v[max(brk.anchor_idx, 0):t]
        ratio = float(v[t] / window.mean()) if len(window) and window.mean() > 0 else None
        signals.append(to_signal(symbol, brk, day.index[t], ratio))
    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
