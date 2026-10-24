"""Open-interest series for the dashboard's OI-buildup chart.

For each F&O stock's current futures contract, the 5-minute candles carry open
interest (OI) at the end of each candle. The chart shows, per candle, how much
OI was added or removed and whether price was rising or falling while it
happened - the classic "buildup" reading:

    OI up   + price up   = long buildup     (fresh buying)
    OI up   + price down = short buildup    (fresh selling)
    OI down + price up   = short covering
    OI down + price down = long unwinding

Only the arrays are produced here (aligned to the dashboard's 75 five-minute
slots, see signals/dashboard_history.py); the classification and drawing are
done by the page.
"""
from __future__ import annotations

import pandas as pd

from signals.dashboard_history import SESSION_START_MINUTES, SLOTS, STEP_MINUTES


def oi_arrays(candles: pd.DataFrame, day: pd.Timestamp) -> dict[str, list] | None:
    """{"o": [OI per slot], "p": [futures close per slot]} for `day`, carried
    forward through slots with no candle and None before the first one; None
    if the frame has no usable OI for that day."""
    if candles is None or candles.empty or "open_interest" not in candles.columns:
        return None
    session = candles[candles.index.normalize() == day.normalize()]
    session = session[session["open_interest"].notna() & (session["open_interest"] > 0)]
    if session.empty:
        return None
    slots = ((session.index.hour * 60 + session.index.minute - SESSION_START_MINUTES) // STEP_MINUTES).to_numpy()
    oi: list[int | None] = [None] * SLOTS
    px: list[float | None] = [None] * SLOTS
    for slot, o, c in zip(slots, session["open_interest"], session["close"]):
        if 0 <= slot < SLOTS:
            oi[slot] = int(o)
            px[slot] = round(float(c), 2) if pd.notna(c) else None
    last_o = last_p = None
    for i in range(SLOTS):
        if oi[i] is None:
            oi[i], px[i] = last_o, last_p
        else:
            last_o, last_p = oi[i], px[i] if px[i] is not None else last_p
            px[i] = last_p
    return {"o": oi, "p": px}
