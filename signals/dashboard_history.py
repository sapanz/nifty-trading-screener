"""Per-day 5-minute history for the dashboard's Replay mode.

For each recent trading day one compact JSON file holds, for every stock, the
5-minute close and volume of each slot of the session. The page then rebuilds
the sector and highest-volume views "as of" any 5-minute mark by itself:
price = the close of the chosen slot, volume = the sum of slot volumes up to
it, change % against the previous session's close - the same definitions the
live snapshot uses (signals/dashboard.py), so a replayed moment looks like the
live page would have looked then.

Day file shape (keys kept short - 500 stocks x 75 slots adds up):
  {"date": "2026-10-23", "start": "09:15", "step": 5, "slots": 75,
   "stocks": {"SYM": {"s": sector, "fo": 0|1, "pc": previous close,
                      "av": 20-day average volume or null,
                      "ph": previous session's high, "pl": its low,
                      "bh": first slot whose high went above ph (null if never),
                      "bl": first slot whose low went below pl (null if never),
                      "c": [close per slot, null before the first trade],
                      "v": [volume per slot],
                      "o": [futures open interest per slot, F&O stocks only, when known],
                      "p": [futures close per slot, alongside "o"],
                      "ob": [order blocks that formed that day, F&O stocks only:
                             {"d": "bull"|"bear", "i": block candle slot, "f": slot it formed (structure broke),
                              "lo","hi": zone, "t": first retest slot or null, "x": slot it was stopped through or null,
                              "imp": thrust in ATRs} - see signals/intraday_blocks.py],
                      "ox","hx","lx": [open / high / low per slot, null where no candle - only with "ob", for the chart]}}}
Slot i covers 09:15 + 5i minutes .. +5; a closed price is carried forward
through slots with no trades.
"""
from __future__ import annotations

import pandas as pd

SESSION_START_MINUTES = 9 * 60 + 15
STEP_MINUTES = 5
SLOTS = 75                 # 09:15 .. 15:30
MIN_BARS = 20              # a stock with fewer traded slots that day is left out (illiquid / data gap)
AVG_VOLUME_DAYS = 20


def build_day(
    day: pd.Timestamp,
    intraday: dict[str, pd.DataFrame],
    daily: dict[str, pd.DataFrame],
    industries: dict[str, str],
    fo_symbols: set[str] | None,
    futures: dict[str, pd.DataFrame] | None = None,
) -> dict | None:
    """The day file for `day`, or None if too few stocks traded to be a real
    session. `futures` ({symbol: 5-minute candles of its futures contract with
    open_interest}) adds each F&O stock's OI arrays ("o", "p") when available."""
    from signals.intraday_blocks import day_blocks
    from signals.oi import oi_arrays

    day = day.normalize()
    stocks: dict[str, dict] = {}
    for symbol, df in intraday.items():
        session = df[df.index.normalize() == day]
        if len(session) < MIN_BARS or symbol not in daily:
            continue
        history = daily[symbol]
        before = history[history.index.normalize() < day]
        if before.empty or before["close"].iloc[-1] <= 0:
            continue
        slots = ((session.index.hour * 60 + session.index.minute - SESSION_START_MINUTES) // STEP_MINUTES).to_numpy()
        closes: list[float | None] = [None] * SLOTS
        volumes = [0] * SLOTS
        for slot, close, volume in zip(slots, session["close"], session["volume"]):
            if 0 <= slot < SLOTS and pd.notna(close):
                closes[slot] = round(float(close), 2)
                volumes[slot] += int(volume) if pd.notna(volume) else 0
        last = None
        for i, value in enumerate(closes):          # carry the last traded price through quiet slots
            if value is None:
                closes[i] = last
            else:
                last = value
        prev_high, prev_low = float(before["high"].iloc[-1]), float(before["low"].iloc[-1])
        broke_hi = broke_lo = None
        for slot, high, low in zip(slots, session["high"], session["low"]):
            if not 0 <= slot < SLOTS:
                continue
            if broke_hi is None and pd.notna(high) and high > prev_high:
                broke_hi = int(slot)
            if broke_lo is None and pd.notna(low) and low < prev_low:
                broke_lo = int(slot)
        recent = before["volume"].tail(AVG_VOLUME_DAYS)
        stocks[symbol] = {
            "s": industries.get(symbol) or "Other",
            "fo": int(bool(fo_symbols and symbol in fo_symbols)),
            "pc": round(float(before["close"].iloc[-1]), 2),
            "ph": round(prev_high, 2), "pl": round(prev_low, 2), "bh": broke_hi, "bl": broke_lo,
            "av": round(float(recent.mean()), 1) if len(recent) >= 5 and recent.mean() > 0 else None,
            "c": closes,
            "v": volumes,
        }
        if stocks[symbol]["fo"]:
            blocks = day_blocks(df, day)
            if blocks:
                stocks[symbol]["ob"] = blocks
                ox: list = [None] * SLOTS; hx: list = [None] * SLOTS; lx: list = [None] * SLOTS
                for slot, op, hi, lo in zip(slots, session["open"], session["high"], session["low"]):
                    if 0 <= slot < SLOTS and pd.notna(op) and pd.notna(hi) and pd.notna(lo):
                        ox[slot], hx[slot], lx[slot] = round(float(op), 2), round(float(hi), 2), round(float(lo), 2)
                stocks[symbol]["ox"], stocks[symbol]["hx"], stocks[symbol]["lx"] = ox, hx, lx
        arrays = oi_arrays(futures.get(symbol), day) if futures else None
        if arrays:
            stocks[symbol].update(arrays)
    if len(stocks) < 50:
        return None
    return {"date": day.date().isoformat(), "start": "09:15", "step": STEP_MINUTES, "slots": SLOTS, "stocks": stocks}


def trading_days(intraday: dict[str, pd.DataFrame], limit: int) -> list[pd.Timestamp]:
    """The most recent `limit` dates on which most stocks have candles."""
    counts: dict[pd.Timestamp, int] = {}
    for df in intraday.values():
        for d in df.index.normalize().unique():
            counts[d] = counts.get(d, 0) + 1
    cutoff = max(counts.values(), default=0) * 0.5
    return sorted(d for d, n in counts.items() if n >= cutoff)[-limit:]
