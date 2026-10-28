"""Order blocks on today's 5-minute candles - the dashboard's intraday setup list.

The setup the user describes: a stock is running up, pulls back with a small red
candle, then runs again - the red candle (the last down candle before the
structure-breaking thrust) is a bullish order block, and its low is a tight stop.
Detection is signals/orderblocks.py on 5-minute bars; this module adds what is
useful live: where each zone stands *now*.

  fresh    the thrust happened and price has not come back yet - the zone to wait for
  testing  price reached the zone within the last INTRADAY_OB_RECENT_BARS bars and has
           not traded through the far edge (plus stop buffer) - the retest is happening
Zones already retested earlier, stopped out, or older than INTRADAY_OB_EXPIRY_BARS are dropped.
Yesterday's session is passed in only as warm-up (ATR, earlier swing levels); only blocks
that formed today are reported. The still-forming last candle is ignored.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import config
from signals.orderblocks import OrderBlock, _pivots, find_order_blocks, first_touch

BAR_MINUTES = 5


def block_state(block: OrderBlock, high: np.ndarray, low: np.ndarray, open_: np.ndarray, close: np.ndarray) -> str | None:
    n = len(low)
    expiry, recent = config.INTRADAY_OB_EXPIRY_BARS, config.INTRADAY_OB_RECENT_BARS
    if n - 1 - block.formed_idx > expiry:
        return None
    t = first_touch(block, open_, high, low, expiry)
    if t is None:
        return "fresh"
    buf = config.OB_STOP_BUFFER_PCT / 100
    # a candle must CLOSE beyond the far edge to fail the zone; a wick through it that closes back is a rejection - it held
    if block.direction == "bull" and (close[t:] < block.low * (1 - buf)).any():
        return None
    if block.direction == "bear" and (close[t:] > block.high * (1 + buf)).any():
        return None
    return "testing" if n - 1 - t <= recent else None


def block_score(impulse_atr: float, vol_ratio: float | None, swept: bool, with_day: bool) -> int:
    """Strength 0-100 of a zone from what is known when it forms: how big the thrust was (40, full marks at 6 ATR), how much volume drove it
    (25, full marks at 5x normal; neutral if unknown), whether it first took out an earlier swing extreme - a liquidity grab (20), and whether it
    goes with the stock's direction on the day (15). The weights are judgement; the backtest reports how well the
    score ranks outcomes (signals/intraday_ob_backtest.py)."""
    volume = 0.5 if vol_ratio is None else min(vol_ratio / 5, 1.0)
    return int(round(40 * min(impulse_atr / 6, 1.0) + 25 * volume + 20 * bool(swept) + 15 * bool(with_day)))


def _with_day(direction: str, close_at_formation: float, prev_close: float | None) -> bool:
    if prev_close is None:
        return False
    return close_at_formation > prev_close if direction == "bull" else close_at_formation < prev_close


def _record(block: OrderBlock, state: str, times, with_day: bool, previous_session: bool = False) -> dict:
    """"pv" = 1 when the block candle is from the previous session (the thrust came at the open): its time label is
    then yesterday's, and the chart draws the zone from the left edge."""
    return {"d": block.direction, "st": state, "lo": round(block.low, 2), "hi": round(block.high, 2),
            "t": times[block.idx].strftime("%H:%M"), "imp": round(block.impulse_atr, 1), "sw": int(block.swept),
            "vr": block.vol_ratio, "wd": int(with_day), "sc": block_score(block.impulse_atr, block.vol_ratio, block.swept, with_day),
            "pv": int(previous_session)}


def scan_symbol(candles: pd.DataFrame, day: pd.Timestamp, now: pd.Timestamp | None = None) -> list[dict]:
    """Best bullish and best bearish zone (testing beats fresh, then the most recent) as of the
    last completed bar; candles are IST wall-clock bar starts, today's bars preceded by warm-up."""
    if candles is None or len(candles) < 30:
        return []
    if now is not None:
        candles = candles[candles.index + pd.Timedelta(minutes=BAR_MINUTES) <= now]
    if len(candles) < 30:
        return []
    o, h, l, c = (candles[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    times = candles.index
    first_today = int(np.argmax(times.normalize() == day.normalize())) if (times.normalize() == day.normalize()).any() else None
    if first_today is None:
        return []
    prev_close = float(c[first_today - 1]) if first_today > 0 else None
    volume = candles["volume"].to_numpy(dtype=float) if "volume" in candles else None
    best: dict[str, tuple] = {}
    for block in find_order_blocks(o, h, l, c, impulse_atr=config.INTRADAY_OB_IMPULSE_ATR, volume=volume):
        if block.formed_idx < first_today:
            continue
        state = block_state(block, h, l, o, c)
        if state is None:
            continue
        rec = _record(block, state, times, _with_day(block.direction, c[block.formed_idx], prev_close), block.idx < first_today)
        rank = (state == "testing", block.formed_idx)
        if block.direction not in best or rank > best[block.direction][0]:
            best[block.direction] = (rank, rec)
    return [rec for _, rec in best.values()]


# ---- Replay: the same zones for every 5-minute mark of a finished session -----------------------------------

SESSION_START_MINUTES = 9 * 60 + 15
WARMUP_BARS = 160                 # previous session (and a bit more) ahead of the day, for ATR and earlier swings


def _slot(ts: pd.Timestamp) -> int:
    return (ts.hour * 60 + ts.minute - SESSION_START_MINUTES) // BAR_MINUTES


def day_blocks(candles: pd.DataFrame, day: pd.Timestamp) -> list[dict]:
    """Every order block that formed during `day`, with the slots at which it formed ("f"), was first
    retested ("t") and was closed through ("x") - enough for the page to say what each zone looked like
    at any 5-minute mark (states_at / the page's replayBlocksAt). Slot s = the candle starting 09:15 + 5s."""
    if candles is None or candles.empty:
        return []
    frame = candles[candles.index.normalize() <= day.normalize()].tail(WARMUP_BARS + 75)
    todays = frame.index.normalize() == day.normalize()
    if len(frame) < 30 or not todays.any():
        return []
    o, h, l, c = (frame[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    times = frame.index
    first_today = int(np.argmax(todays))
    buf = config.OB_STOP_BUFFER_PCT / 100
    prev_close = float(c[first_today - 1]) if first_today > 0 else None
    volume = frame["volume"].to_numpy(dtype=float) if "volume" in frame else None
    out = []
    for block in find_order_blocks(o, h, l, c, impulse_atr=config.INTRADAY_OB_IMPULSE_ATR, volume=volume):
        if block.formed_idx < first_today:
            continue
        t = first_touch(block, o, h, l, config.INTRADAY_OB_EXPIRY_BARS)
        x = None
        if t is not None:
            through = np.flatnonzero(c[t:] < block.low * (1 - buf)) if block.direction == "bull" else np.flatnonzero(c[t:] > block.high * (1 + buf))
            x = _slot(times[t + int(through[0])]) if len(through) else None
        out.append({"d": block.direction, "i": _slot(times[block.idx]), "f": _slot(times[block.formed_idx]), "lo": round(block.low, 2),
                    "hi": round(block.high, 2), "t": None if t is None else _slot(times[t]), "x": x, "imp": round(block.impulse_atr, 1),
                    "pv": int(block.idx < first_today), "sw": int(block.swept), "vr": block.vol_ratio, "wd": int(_with_day(block.direction, c[block.formed_idx], prev_close)),
                    "sc": block_score(block.impulse_atr, block.vol_ratio, block.swept, _with_day(block.direction, c[block.formed_idx], prev_close))})
    return out


def states_at(blocks: list[dict], slot: int) -> list[dict]:
    """Best bullish / bearish zone as of the close of `slot` - the same answer scan_symbol gives on candles up to that
    slot (test_intraday_blocks checks they agree); the page has a JS twin."""
    best: dict[str, tuple] = {}
    for b in blocks:
        if b["f"] > slot or slot - b["f"] > config.INTRADAY_OB_EXPIRY_BARS:
            continue
        touched = b["t"] is not None and b["t"] <= slot
        if not touched:
            state = "fresh"
        elif b["x"] is not None and b["x"] <= slot:
            continue
        elif slot - b["t"] <= config.INTRADAY_OB_RECENT_BARS:
            state = "testing"
        else:
            continue
        rank = (state == "testing", b["f"])
        if b["d"] not in best or rank > best[b["d"]][0]:
            best[b["d"]] = (rank, {"d": b["d"], "st": state, "lo": b["lo"], "hi": b["hi"],
                                   "t": f"{(SESSION_START_MINUTES + b['i'] * BAR_MINUTES) // 60:02d}:{(SESSION_START_MINUTES + b['i'] * BAR_MINUTES) % 60:02d}",
                                   "imp": b["imp"], "sw": b["sw"], "vr": b["vr"], "wd": b["wd"], "sc": b["sc"], "pv": b.get("pv", 0)})
    return [rec for _, rec in best.values()]


# ---- Chart data for the page: today's candles and the levels around price ----------------------------------------

POOL_TOLERANCE_PCT = 0.15         # swing extremes this close together count as one pool of resting stops ("equal highs/lows")


def key_levels(high: np.ndarray, low: np.ndarray, price: float, k: int | None = None, tol_pct: float | None = None) -> dict:
    """Nearest swing support below and resistance above `price`, and the nearest liquidity pools - two or more swing
    lows (below) / highs (above) within POOL_TOLERANCE_PCT of each other, where stops tend to rest. Swings are
    pivots confirmed by the bars after them, over the candles given (yesterday's tail + today)."""
    k = k or config.OB_SWING_K
    lows = [float(low[i]) for i in _pivots(low, k, False)]
    highs = [float(high[i]) for i in _pivots(high, k, True)]
    tol = (tol_pct if tol_pct is not None else POOL_TOLERANCE_PCT) / 100

    def pool(levels: list[float], below: bool) -> float | None:
        cands = []
        for a in levels:
            if (a < price) != below:
                continue
            if sum(1 for b in levels if abs(b - a) / a <= tol) >= 2:
                cands.append(a)
        if not cands:
            return None
        return round(max(cands) if below else min(cands), 2)

    below = [x for x in lows if x < price]
    above = [x for x in highs if x > price]
    return {"sup": round(max(below), 2) if below else None, "res": round(min(above), 2) if above else None,
            "liq_lo": pool(lows, True), "liq_hi": pool(highs, False)}


def chart_payload(candles: pd.DataFrame, day: pd.Timestamp, now: pd.Timestamp | None = None) -> dict | None:
    """{"c": [[o,h,l,c] per slot of today, None for a missing slot], "lv": key levels} from completed candles."""
    if candles is None or candles.empty:
        return None
    if now is not None:
        candles = candles[candles.index + pd.Timedelta(minutes=BAR_MINUTES) <= now]
    recent = candles.tail(WARMUP_BARS + 75)
    today = recent[recent.index.normalize() == day.normalize()]
    if today.empty:
        return None
    slots = ((today.index.hour * 60 + today.index.minute - SESSION_START_MINUTES) // BAR_MINUTES).to_numpy()
    out: list = [None] * (int(slots.max()) + 1)
    for slot, row in zip(slots, today[["open", "high", "low", "close"]].to_numpy(dtype=float)):
        if slot >= 0:
            out[int(slot)] = [round(float(x), 2) for x in row]
    levels = key_levels(recent["high"].to_numpy(dtype=float), recent["low"].to_numpy(dtype=float), float(today["close"].iloc[-1]))
    return {"c": out, "lv": levels}
