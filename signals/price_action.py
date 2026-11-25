"""Price-action setups: weekly structure, traded on the daily chart (long side, plus the bearish setups below).

Twelve kinds, all found on COMPLETED weekly candles and triggered by a DAILY close outside the structure:
  multiyear a close above the highest weekly high of the last 2-5 years, set at least 52 weeks ago (a high more than a year old).
  mretest   a multi-year high (set at least a year before) is broken by a daily close, price moves away from it and comes back into the OLD-HIGH ZONE (the old high -2% .. +2%) for
            the FIRST time and holds there (a close inside or above the zone, in the upper half of the candle); the trigger is a close above that candle's high, the stop just under that candle's low (`mr_stop_zone` puts it under the whole
            zone). One trade per breakout.
  zone      weekly support (a weekly demand zone - signals/weekly_zone_backtest.find_zones): price closed inside the zone and then a daily
            close comes back out above it.
  range     8-40 weeks inside a box no wider than RANGE_MAX_WIDTH with at least two touches of each side; a daily close above the top.
  triangle  falling (or flat) highs and rising lows that converge - symmetric / ascending; a daily close above the upper line.
  cup       cup and handle: two rims at a similar level around a rounded 12-45% dip, then a shallow handle; a daily close above the rim.
  retest    SHORT, F&O only: a base (8-40 weeks, at most 30% wide, floor tested twice) breaks down, price comes back up to retest the broken floor from below, and a
            candle with a long upper wick fails there; the short triggers on a daily close below that candle's low, the stop is its high.
  zone (short)  SHORT, F&O only: a weekly supply zone (resistance) - price rallied into it and a daily close comes back out below it; stop above the zone. Carries
            "cf": a daily falling-resistance trendline runs through the zone (+/- 1%), the Swing tab's confluence.
  flag (short)  SHORT, F&O only: bear flag - a fall of 20%+ in 8 weeks or fewer, then a 2-6 week bounce that keeps more than half of it; trigger = a daily close below the fall's low.
  trendline     SHORT, F&O only: a rising daily trendline through at least 3 swing lows (signals/trendlines.find_break, daily settings) is broken by a red daily close.
  double / triple / hs / flag  the reversal and continuation shapes, restored at the user's request: W pattern / M pattern (double bottom / top), triple bottom / top,
            inverse head and shoulders / head and shoulders, bull flag / bear flag. Long = a daily close above the neckline (or the flag's pole top); the bearish twin
            (SHORT, F&O only) is the SAME detector on inverted prices, a daily close below the neckline.
Removed after the 6-year backtest (see the README, "Removed tools"): reversal candles at a zone and the bearish halt-zone continuation. Every kind listed
here lost money or showed no edge except the multi-year breakout (and, barely, the bull flag, triangle and weekly support zone) - the record is shown beside each setup.

Plan for every setup: entry at the next open after the trigger close (the list shows the trigger close as the reference), stop just below the
zone / back inside the pattern (the lower of the breakout level and the trigger day's low), target = the next weekly resistance above the
entry (a confirmed weekly swing high, or the high of the last few weeks), or a measured move (the pattern's height) when nothing is overhead.
Nothing looks ahead: a pattern uses weeks that closed before the trigger week, a resistance is a swing high already confirmed.

`scan_stock` returns every trigger in the history (the backtest and the probability model use them) and `pending` the structures that are
armed but not triggered yet. See signals/price_action_model.py for the outcomes and the probability.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from signals import swing_trendlines
from signals.trendlines import daily_params, find_break, swing_lows
from signals.weekly_zone_backtest import Params as ZoneParams
from signals.weekly_zone_backtest import find_zones, weekly_bars

KINDS = ("zone", "range", "triangle", "cup", "multiyear", "retest", "flag", "trendline", "double", "hs", "triple", "mretest")
NAMES = {("zone", "long"): "Weekly support zone", ("range", "long"): "Weekly range breakout", ("triangle", "long"): "Triangle breakout", ("cup", "long"): "Cup and handle",
         ("multiyear", "long"): "Multi-year breakout", ("mretest", "long"): "Multi-year high retest", ("retest", "short"): "Breakdown retest (short)", ("retest", "long"): "Breakout retest",
         ("zone", "short"): "Weekly resistance zone (short)", ("flag", "short"): "Bear flag (short)", ("trendline", "short"): "Trendline breakdown (short)",
         ("flag", "long"): "Bull flag", ("double", "long"): "W pattern (double bottom)", ("double", "short"): "M pattern (double top)",
         ("hs", "long"): "Inverse head and shoulders", ("hs", "short"): "Head and shoulders", ("triple", "long"): "Triple bottom", ("triple", "short"): "Triple top"}
KIND_NAMES = {k: next(n for (kk, _), n in NAMES.items() if kk == k) for k in KINDS}    # the name of each kind (its long side when it has one)


@dataclass(frozen=True)
class Params:
    stop_buffer_pct: float = 0.3
    min_risk_pct: float = 1.0
    max_risk_pct: float = 12.0
    min_rr: float = 1.5
    pivot_k: int = 3                    # a weekly resistance = a high above the 3 weeks either side
    res_lookback_weeks: int = 156
    recent_weeks: int = 3               # the last few weeks' high also counts as resistance (a peak too recent to be confirmed)
    range_min_weeks: int = 8
    range_max_weeks: int = 40
    range_max_width: float = 0.25
    range_min_width: float = 0.05
    touch_tol: float = 0.03
    cooldown_days: int = 20             # one trigger per stock and kind in this many sessions
    zone_impulse_atr: float = 2.0
    zone_expiry_weeks: int = 104
    near_pct: float = 3.0               # "pending": the close is within this % below the breakout level
    floor_max_width: float = 0.30       # a base for the breakdown-retest short: at most this wide (highest high / lowest low - 1) ...
    floor_min_tests: int = 2            # ... with its floor tested at least this many times, spaced at least 2 weeks apart
    retest_break_window: int = 40       # a range's breakdown must come within this many sessions of the range being seen
    retest_window: int = 15             # the retest of the broken floor must come within this many sessions of the breakdown
    retest_near_pct: float = 1.0        # ... the retest candle's high gets within this % of the floor
    retest_fail_close_pct: float = 1.0  # ... the failure candle closes no higher than this % above the band's top
    retest_reclaim_pct: float = 3.0     # ... and no close may get back above the band's top by more than this % (the breakdown has then failed)
    retest_wick_frac: float = 0.5       # the failure candle's upper wick is at least this share of its range ...
    retest_min_range_atr: float = 0.5   # ... and the candle is at least this many ATRs tall
    retest_trigger_days: int = 3        # the short triggers on a close below the failure candle's low within this many sessions
    mr_lookback: int = 1300             # the multi-year high of the retest setup: the highest high of the last 1300 sessions (about 5 years) ...
    mr_min_age: int = 250               # ... set at least this many sessions (a year) before the breakout
    mr_away_pct: float = 5.0            # after the breakout price must trade this % above the old high before a return counts as the retest
    mr_band_pct: float = 2.0            # the old-high ZONE: from the old high up to this % above it (price returning into it is the retest) ...
    mr_zone_pct: float = 2.0            # ... and down to this % under it (a support candle may close inside the zone, down to its bottom)
    mr_stop_zone: bool = False          # the stop under the support candle's low (False) or under the whole zone (True: in the 9 historical cases it cut reward:risk under 1.5 for all of them)
    mr_fail_pct: float = 3.0            # a close more than this % under the old high means the breakout failed (no setup)
    mr_window: int = 150                # the first return must come within this many sessions of the breakout
    multiyear_min_weeks: int = 52       # the old high must be at least a year old (it was 26 weeks until Nov 2026: a 9-month-old high is not "multi-year")



# ---------------------------------------------------------------- weekly patterns (as of week index w, inclusive)
def _pivots(x: np.ndarray, k: int, lo: int, hi: int, high: bool) -> list[int]:
    """Indices i in [lo, hi] where x[i] is the extreme of x[i-k .. i+k] and i+k <= hi (confirmed inside the window)."""
    out = []
    for i in range(max(lo, k), hi - k + 1):
        window = x[i - k:i + k + 1]
        if (x[i] >= window.max()) if high else (x[i] <= window.min()):
            if not out or i - out[-1] > k:
                out.append(i)
    return out


def range_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """The longest box ending at week w: width within limits, two spaced touches of each side, not broken on a weekly close."""
    for n in range(min(p.range_max_weeks, w + 1), p.range_min_weeks - 1, -1):
        a = w - n + 1
        h, l, c = H[a:w + 1], L[a:w + 1], C[a:w + 1]
        top, bot = h.max(), l.min()
        width = top / bot - 1
        if not (p.range_min_width <= width <= p.range_max_width) or c[-1] > h[:-1].max():
            continue                                              # too wide / narrow, or the last week already closed above the earlier weeks' top
        hi_t = np.flatnonzero(h >= top * (1 - p.touch_tol))
        lo_t = np.flatnonzero(l <= bot * (1 + p.touch_tol))
        if len(hi_t) < 2 or len(lo_t) < 2 or hi_t[-1] - hi_t[0] < 3 or lo_t[-1] - lo_t[0] < 3:
            continue
        if c[-1] < bot + 0.5 * (top - bot):                       # sitting in the lower half: not a breakout candidate
            return None
        return {"level": float(top), "base": float(bot), "height": float(top - bot), "start": a, "end": w, "weeks": n,
                "why": f"{n}-week range {bot:,.1f}-{top:,.1f} ({width * 100:.0f}% wide), {len(hi_t)} tests of the top"}
    return None


def _line(xs: list[int], ys: np.ndarray) -> tuple[float, float]:
    """Least-squares line through the points -> (slope per week, intercept)."""
    m, b = np.polyfit(np.asarray(xs, float), np.asarray(ys, float), 1)
    return float(m), float(b)


def triangle_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """Converging lines through the last weekly swing highs (falling or flat) and swing lows (rising), price still inside at week w."""
    lo = max(0, w - p.range_max_weeks + 1)
    ph, pl = _pivots(H, 2, lo, w, True), _pivots(L, 2, lo, w, False)
    for take in (3, 2):
        if len(ph) < take or len(pl) < take:
            continue
        hs, ls = ph[-take:], pl[-take:]
        start = min(hs[0], ls[0])
        if w - start + 1 < p.range_min_weeks:
            continue
        mu, bu = _line(hs, H[hs])
        ml, bl = _line(ls, L[ls])
        px = C[w]
        up_s, lo_s = mu / px * 100, ml / px * 100                 # slopes in % of price per week
        if lo_s < 0.10 or up_s > 0.10:                            # lows must rise; highs must fall or stay flat
            continue
        gap0, gap1 = (mu * start + bu) - (ml * start + bl), (mu * (w + 1) + bu) - (ml * (w + 1) + bl)
        if not (gap0 > 0 and 0 < gap1 <= 0.7 * gap0):
            continue
        xs = np.arange(start, w + 1)
        upper, lower = mu * xs + bu, ml * xs + bl
        if (C[start:w + 1] > upper * 1.02).any() or (C[start:w + 1] < lower * 0.98).any():
            continue
        level = mu * (w + 1) + bu
        name = "Ascending" if up_s > -0.10 else "Symmetric"
        return {"level": float(level), "base": float(ml * (w + 1) + bl), "height": float(gap0), "start": start, "end": w, "weeks": w - start + 1, "shape": name,
                "upper": (start, float(mu * start + bu), w + 1, float(level)), "lower": (start, float(ml * start + bl), w + 1, float(ml * (w + 1) + bl)),
                "why": f"{name.lower()} triangle over {w - start + 1} weeks: {take} {'lower' if name == 'Symmetric' else 'equal'} highs and {take} higher lows converging"}
    return None


def cup_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """Cup and handle whose handle ends at week w."""
    best = None
    for c in range(w - 1, max(w - 9, 7), -1):                     # right rim; the handle is weeks c+1 .. w (1-8 weeks)
        rim = H[c]
        if H[c:w + 1].max() > rim or C[w] > rim:
            continue
        a0 = max(0, c - 65)
        if c - 7 <= a0:
            continue
        a = a0 + int(np.argmax(H[a0:c - 6]))                      # left rim: the highest week 7-65 weeks before the right rim
        left = H[a]
        if not (0.92 <= rim / left <= 1.05) or H[a + 1:c].max() > max(left, rim):
            continue
        b = a + int(np.argmin(L[a:c + 1]))
        depth = 1 - L[b] / left
        length = c - a
        if not (0.12 <= depth <= 0.45) or b - a < 0.2 * length or c - b < 0.2 * length:
            continue
        handle_low = L[c + 1:w + 1].min()
        h_depth = 1 - handle_low / rim
        if not (0.02 <= h_depth <= min(0.15, depth / 2)) or handle_low < L[b] + 0.5 * (left - L[b]):
            continue
        cand = {"level": float(rim), "base": float(handle_low), "height": float(rim - L[b]), "start": a, "end": w, "weeks": w - a + 1,
                "left": a, "bottom": b, "right": c, "bottom_px": float(L[b]),
                "why": f"cup and handle: {length}-week cup {depth * 100:.0f}% deep, {w - c}-week handle {h_depth * 100:.0f}% deep, rim {rim:,.1f}"}
        if best is None or (cand["level"], cand["weeks"]) > (best["level"], best["weeks"]):
            best = cand                                           # the highest rim is the real one; a lower one is just a week of the handle
    return best


# ---- reversal and continuation shapes. Each is written ONCE, bullish way up; the bearish twin (M pattern, head and shoulders, triple top, bear
# ---- flag) is the same function run on inverted prices (1 / price, highs and lows swapped), so the two sides cannot drift apart.
def multiyear_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """The highest weekly high of the last 2-5 years, set at least `multiyear_min_weeks` (52) weeks ago, with the last close within 15% under it."""
    n = min(w + 1, 260)
    if n < 104:
        return None
    a = w - n + 1
    t = a + int(np.argmax(H[a:w + 1]))
    level = H[t]
    if w - t < p.multiyear_min_weeks or C[w] > level or C[w] < level * 0.85:
        return None
    b = t + int(np.argmin(L[t:w + 1]))
    return {"level": float(level), "base": float(L[b]), "hfrom": float(L[b]), "hto": float(level), "start": t, "end": w, "weeks": w - t + 1,
            "pts": [(t, float(level)), (b, float(L[b]))], "neck": (t, float(level), w + 1, float(level)), "years": n / 52}


def double_bottom_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """W: two weekly swing lows within 4% of each other, 4-40 weeks apart, a peak at least 8% above them in between (the neckline), reached from above."""
    pl = _pivots(L, 2, max(0, w - 60), w, False)
    if len(pl) < 2 or w - pl[-1] > 12:
        return None
    b2 = pl[-1]
    for b1 in reversed(pl[:-1]):
        if b2 - b1 < 4:
            continue
        if b2 - b1 > 40 or b1 < 4:
            break
        low = min(L[b1], L[b2])
        m = b1 + 1 + int(np.argmax(H[b1 + 1:b2]))                  # the rebound high strictly between the two lows (not the high of a low's own week)
        neck = H[m]
        if abs(L[b1] / L[b2] - 1) > 0.04 or neck / low - 1 < 0.08 or L[b1:w + 1].min() < low * 0.985 or C[b2:w + 1].max() > neck:
            continue
        if H[max(0, b1 - 26):b1].max() < neck:                     # it has to come after a fall from above the neckline
            continue
        return {"level": float(neck), "base": float(low), "hfrom": float(low), "hto": float(neck), "start": b1, "end": w, "weeks": w - b1 + 1,
                "pts": [(b1, float(L[b1])), (m, float(neck)), (b2, float(L[b2]))], "neck": (m, float(neck), w + 1, float(neck)), "gap": b2 - b1}
    return None


def triple_bottom_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """Three weekly swing lows within 4% of each other; the neckline is the highest point between them."""
    pl = _pivots(L, 2, max(0, w - 70), w, False)
    if len(pl) < 3:
        return None
    b1, b2, b3 = pl[-3:]
    if w - b3 > 12 or b2 - b1 < 3 or b3 - b2 < 3 or b1 < 4:
        return None
    lows = L[[b1, b2, b3]]
    low = lows.min()
    m1, m2 = b1 + 1 + int(np.argmax(H[b1 + 1:b2])), b2 + 1 + int(np.argmax(H[b2 + 1:b3]))      # the two rebound highs, strictly between the lows
    m = m1 if H[m1] >= H[m2] else m2
    neck = H[m]
    if lows.max() / low - 1 > 0.04 or neck / low - 1 < 0.08 or L[b1:w + 1].min() < low * 0.985 or C[b3:w + 1].max() > neck or H[max(0, b1 - 26):b1].max() < neck:
        return None
    return {"level": float(neck), "base": float(low), "hfrom": float(low), "hto": float(neck), "start": b1, "end": w, "weeks": w - b1 + 1,
            "pts": [(b1, float(L[b1])), (m1, float(H[m1])), (b2, float(L[b2])), (m2, float(H[m2])), (b3, float(L[b3]))], "neck": (m, float(neck), w + 1, float(neck))}


def inv_head_shoulders_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """Three weekly swing lows, the middle one (head) at least 3% below both shoulders, shoulders within 8% of each other; the neckline joins the two
    peaks in between and the level is that line carried to next week."""
    pl = _pivots(L, 2, max(0, w - 70), w, False)
    if len(pl) < 3:
        return None
    ls, hd, rs = pl[-3:]
    if w - rs > 10 or hd - ls < 3 or rs - hd < 3 or ls < 4 or not (1 / 3 <= (hd - ls) / (rs - hd) <= 3):
        return None
    if L[hd] > min(L[ls], L[rs]) * 0.97 or abs(L[ls] / L[rs] - 1) > 0.08:
        return None
    n1, n2 = ls + 1 + int(np.argmax(H[ls + 1:hd])), hd + 1 + int(np.argmax(H[hd + 1:rs]))      # the two rebound highs, strictly between the lows
    if n2 <= n1 or abs(H[n2] / H[n1] - 1) > 0.10:
        return None
    slope = (H[n2] - H[n1]) / (n2 - n1)
    line = lambda x: H[n1] + slope * (x - n1)                       # noqa: E731
    if line(hd) / L[hd] - 1 < 0.10 or L[hd:w + 1].min() < L[hd] or (C[rs:w + 1] > line(np.arange(rs, w + 1))).any() or H[max(0, ls - 26):ls].max() < line(ls):
        return None
    level = float(line(w + 1))
    return {"level": level, "base": float(L[hd]), "hfrom": float(L[hd]), "hto": float(line(hd)), "start": ls, "end": w, "weeks": w - ls + 1,
            "pts": [(ls, float(L[ls])), (n1, float(H[n1])), (hd, float(L[hd])), (n2, float(H[n2])), (rs, float(L[rs]))], "neck": (n1, float(H[n1]), w + 1, level),
            "shoulders": (float(L[ls]), float(L[rs]))}


def flag_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """Bull flag (run on inverted prices it finds the bear flag): a pole of at least 20% in 8 weeks or fewer, then 2-6 weeks that pull back 3% or more but give up no more than half the pole."""
    for f in range(2, 7):
        t = w - f                                                 # the pole's top
        if t < 9:
            break
        if H[t] < H[t - 8:w + 1].max() or C[w] > H[t]:
            continue                                              # the pole's top has to be the highest point of the pole and the flag
        q = t - 8 + int(np.argmin(L[t - 8:t]))
        pole = H[t] - L[q]
        low_i = t + 1 + int(np.argmin(L[t + 1:w + 1]))
        if H[t] / L[q] - 1 < 0.20 or L[low_i] < H[t] - 0.5 * pole or 1 - L[low_i] / H[t] < 0.03:
            continue
        return {"level": float(H[t]), "base": float(L[low_i]), "hfrom": float(L[q]), "hto": float(H[t]), "start": q, "end": w, "weeks": w - q + 1,
                "pts": [(q, float(L[q])), (t, float(H[t])), (low_i, float(L[low_i]))], "neck": (t, float(H[t]), w + 1, float(H[t])), "pole_weeks": t - q, "flag_weeks": f}
    return None


def _unmirror(pat: dict) -> dict:
    """Turn a pattern found on inverted prices back into real prices."""
    out = dict(pat)
    for key in ("level", "base", "hfrom", "hto"):
        out[key] = 1.0 / pat[key]
    out["pts"] = [(i, 1.0 / v) for i, v in pat["pts"]]
    x0, y0, x1, y1 = pat["neck"]
    out["neck"] = (x0, 1.0 / y0, x1, 1.0 / y1)
    if "shoulders" in pat:
        out["shoulders"] = tuple(1.0 / v for v in pat["shoulders"])
    if "shoulders" in pat:
        out["shoulders"] = tuple(1.0 / v for v in pat["shoulders"])
    return out


def describe(kind: str, side: str, pat: dict) -> str:
    """The reason in words (real prices) for the 'new-style' patterns."""
    long = side == "long"
    level, base, move = pat["level"], pat["base"], abs(pat["hto"] / pat["hfrom"] - 1) * 100
    if kind == "multiyear":
        weeks = pat["end"] - pat["start"]
        return f"{pat['years']:.0f}-year high {level:,.1f}, set {weeks} weeks ago (about {weeks / 4.345:.0f} months); the base since then went down to {base:,.1f}"
    if kind == "double":
        return (f"{'W pattern: two lows' if long else 'M pattern: two highs'} near {base:,.1f}, {pat['gap']} weeks apart, with a {'peak' if long else 'trough'} "
                f"{move:.0f}% {'above' if long else 'below'} them - the neckline {level:,.1f}")
    if kind == "triple":
        return f"{'triple bottom: three lows' if long else 'triple top: three highs'} near {base:,.1f} over {pat['weeks']} weeks, neckline {level:,.1f}"
    if kind == "hs":
        a, b = pat["shoulders"]
        return (f"{'inverse head and shoulders' if long else 'head and shoulders'} over {pat['weeks']} weeks: head {base:,.1f}, shoulders {a:,.1f} and {b:,.1f}, "
                f"neckline now {level:,.1f}")
    return (f"{'bull' if long else 'bear'} flag: a {move:.0f}% {'rise' if long else 'fall'} in {pat['pole_weeks']} weeks, then a {pat['flag_weeks']}-week "
            f"{'pullback' if long else 'bounce'} to {base:,.1f} that kept more than half of it")


# (kind, side, detector, new-style): the old-style detectors return `height` / `why` themselves.
PATTERNS = [("range", "long", range_at, False), ("triangle", "long", triangle_at, False), ("cup", "long", cup_at, False), ("multiyear", "long", multiyear_at, True)] + [
    (kind, side, fn, True) for kind, fn in (("hs", inv_head_shoulders_at), ("triple", triple_bottom_at), ("double", double_bottom_at), ("flag", flag_at)) for side in ("long", "short")]
DETECTORS = {"range": range_at, "triangle": triangle_at, "cup": cup_at}


def patterns_at(wH: np.ndarray, wL: np.ndarray, wC: np.ndarray, w: int, p: Params) -> dict[tuple[str, str], dict]:
    """Every structure present as of week w, keyed by (kind, side), in real prices with `height` and `why` filled in."""
    found: dict[tuple[str, str], dict] = {}
    inv = None
    for kind, side, fn, new in PATTERNS:
        if (kind == "triple" and ("hs", side) in found) or (kind == "double" and (("hs", side) in found or ("triple", side) in found)):
            continue                                              # the same lows / highs are already named by the bigger shape
        if side == "long":
            pat = fn(wH, wL, wC, w, p)
        else:                                                      # a short is the long detector on inverted prices (high <-> 1/low), turned back into real prices
            inv = inv or (1.0 / wL, 1.0 / wH, 1.0 / wC)
            pat = fn(*inv, w, p)
            pat = _unmirror(pat) if pat else None
        if pat is None:
            continue
        if new:
            pat = {**pat, "height": abs(pat["hto"] - pat["hfrom"]), "why": describe(kind, side, pat)}
        found[(kind, side)] = pat
    return found


def floor_at(H: np.ndarray, L: np.ndarray, C: np.ndarray, w: int, p: Params) -> dict | None:
    """A base whose FLOOR is a candidate for a breakdown short, as of week w: the shortest stretch of 8-40 weeks, at most `floor_max_width` wide, whose
    lowest low was tested at least `floor_min_tests` times (weekly lows within touch_tol of it, 2+ weeks apart), and whose last week still closed above the
    floor and in the lower 60% of the box. Looser than `range_at` (no requirement on the top), because only the floor matters for the short."""
    for n in range(p.range_min_weeks, min(p.range_max_weeks, w + 1) + 1):                 # the shortest base first: the most recent consolidation, not old lows
        a = w - n + 1
        h, l, c = H[a:w + 1], L[a:w + 1], C[a:w + 1]
        floor, top = float(l.min()), float(h.max())
        width = top / floor - 1
        if not (p.range_min_width <= width <= p.floor_max_width) or c[-1] < floor or c[-1] > floor + 0.6 * (top - floor):
            continue
        tests = np.flatnonzero(l <= floor * (1 + p.touch_tol))
        spaced = [tests[0]] if len(tests) else []
        for t in tests[1:]:
            if t - spaced[-1] >= 2:
                spaced.append(t)
        if len(spaced) < p.floor_min_tests:
            continue
        return {"floor": floor, "top": top, "band_top": float(l[tests].max()), "start": a, "end": w, "weeks": n}
    return None


# ---------------------------------------------------------------- breakdown, retest from below, failure (short)
def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def retest_search(d: dict, atr: np.ndarray, floor: float, band_top: float, s0: int, break_window: int, p: Params) -> tuple[str, dict] | None:
    """After a floor band (`floor` = a range's lowest low or a support zone's low, `band_top` = the top of the band where price bounced) at session s0
    onward: the first daily close below the floor is the breakdown; then, within `retest_window` sessions, a candle whose high gets back to the floor
    (within retest_near_pct), that closes back under the band's top (within retest_fail_close_pct) and has a long upper wick
    (>= retest_wick_frac of its range, close in its lower half) is the failure; the short triggers on a later close under that candle's low within
    `retest_trigger_days` sessions without trading above its high. A close back above the band's top (by more than retest_reclaim_pct) ends the setup.
    Returns ("trigger", info) / ("pending", info) (the failure candle is formed and the trigger is still possible on the latest bar) / None."""
    o, h, l, c = d["o"], d["h"], d["l"], d["c"]
    n = len(c)
    b = None
    for i in range(s0, min(n, s0 + break_window)):
        if c[i] < floor:
            b = i
            break
    if b is None:
        return None
    for j in range(b + 1, min(n, b + 1 + p.retest_window)):
        if c[j] > band_top * (1 + p.retest_reclaim_pct / 100):
            return None                                                          # price got back above the band: the breakdown failed
        rng = h[j] - l[j]
        a = atr[j]
        if rng <= 0 or not a > 0:
            continue
        if (h[j] >= floor * (1 - p.retest_near_pct / 100) and c[j] <= band_top * (1 + p.retest_fail_close_pct / 100) and h[j] - max(o[j], c[j]) >= p.retest_wick_frac * rng
                and c[j] - l[j] <= 0.5 * rng and rng >= p.retest_min_range_atr * a):
            dead = False
            for i in range(j + 1, min(n, j + 1 + p.retest_trigger_days)):
                if h[i] > h[j]:
                    dead = True                                                  # it traded above the failure candle's high: this candle is not the failure
                    break
                if c[i] < l[j]:
                    return "trigger", {"i": i, "b": b, "j": j}
            if not dead and j + p.retest_trigger_days >= n - 1:
                return "pending", {"i": n - 1, "b": b, "j": j}
        # not the failure (or it failed to trigger): keep looking within the retest window
    return None


def _inv_d(d: dict) -> dict:
    """The same candles on inverted prices (1 / price, high <-> low): a bullish structure there is its bearish twin, so each detector is written once."""
    return {"o": 1.0 / d["o"], "h": 1.0 / d["l"], "l": 1.0 / d["h"], "c": 1.0 / d["c"], "v": d["v"]}


def retest_scan(d: dict, dates: pd.DatetimeIndex, wk_of: np.ndarray, wdates: pd.DatetimeIndex, wH: np.ndarray, wL: np.ndarray, wC: np.ndarray, p: Params,
                side: str = "short", df: pd.DataFrame | None = None) -> tuple[list[dict], list[dict]]:
    """Break and retest. SHORT: a structure that held price UP (a weekly base's floor, a weekly support zone, a rising trendline) is broken by a daily close,
    price comes back to retest it from below and a candle with a long upper wick fails there. LONG is the mirror (a base's ceiling, a weekly resistance
    zone or a falling trendline broken upward, a retest from above that holds with a long lower wick) - found by running the same code on inverted prices.
    Returns (triggers, pending) with the raw pieces in REAL prices; scan_stock plans them."""
    n = len(dates)
    long = side == "long"
    dd = _inv_d(d) if long else d
    if long:
        wH_, wL_, wC_ = 1.0 / wL, 1.0 / wH, 1.0 / wC
    else:
        wH_, wL_, wC_ = wH, wL, wC
    real = (lambda v: 1.0 / v) if long else (lambda v: v)              # back to real prices
    atr = _atr(dd["h"], dd["l"], dd["c"])
    out, pend, seen = [], [], set()

    def item(source, floor, top, band_top, start_d, end_d, res, **extra):
        return {"source": source, "side": side, "floor": real(floor), "band_top": real(band_top), "top": real(top), "from_date": start_d, "to_date": end_d,
                "i": res[1]["i"], "b": res[1]["b"], "j": res[1]["j"], "kind": res[0], **extra}

    def add(res, it):
        if res:
            (out if res[0] == "trigger" else pend).append(it)

    # --- source 1: a weekly base's floor (ceiling for the long side)
    for w in range(40, len(wC)):
        pat = floor_at(wH_, wL_, wC_, w, p)
        if pat is None:
            continue
        floor, top, band_top = pat["floor"], pat["top"], pat["band_top"]
        key = ("range", pat["start"], round(np.log(floor) / 0.01))
        if key in seen:
            continue
        seen.add(key)
        s0 = int(np.searchsorted(dates.values, wdates.values[w], side="right"))
        res = retest_search(dd, atr, floor, band_top, s0, p.retest_break_window, p)
        if res:
            add(res, item("range", floor, top, band_top, wdates[pat["start"]], wdates[pat["end"]], res))
    # --- source 2: a weekly zone that was defended, then closed through (a support zone for the short side, a resistance zone for the long side)
    if df is not None:
        zp = ZoneParams(side="short" if long else "long", impulse_atr=p.zone_impulse_atr, expiry_weeks=p.zone_expiry_weeks)
        for z in find_zones(df.set_axis(dates), zp):
            lo_r, hi_r = z["lo"], z["hi"]
            floor, band_top = (1.0 / hi_r, 1.0 / lo_r) if long else (lo_r, hi_r)       # in the working (possibly inverted) prices: the zone's floor and its upper edge
            start = int(dates.searchsorted(z["known"], side="right"))
            end = min(n, int(dates.searchsorted(z["known"] + pd.Timedelta(weeks=p.zone_expiry_weeks), side="right")))
            above = np.flatnonzero(dd["c"][start:end] > band_top)
            if not len(above):
                continue
            s0 = start + int(above[0])                                                 # price has been above the zone: now watch for the close through its floor
            res = retest_search(dd, atr, floor, band_top, s0, end - s0, p)
            if res:
                add(res, item("zone", floor, band_top, band_top, z["formed"], dates[min(res[1]["j"], n - 1)], res))
    # --- source 3: a trendline (a rising support line broken down; a falling resistance line broken up) and the retest of the line
    if df is not None:
        tp = daily_params()
        pivots = swing_lows(dd["l"], tp.swing_bars)
        last_tl = -10 ** 9
        for t in range(260, n):
            if t - last_tl < p.cooldown_days:
                continue
            br = find_break(dd["o"], dd["h"], dd["l"], dd["c"], t, pivots, tp)
            if br is None:
                continue
            last_tl = t
            floor = br.line_at_break
            res = retest_search(dd, atr, floor, floor * 1.005, t, 2, p)
            if res:
                a = br.anchor_idx
                add(res, item("trendline", floor, floor * (1 + br.rise_pct / 100), floor * 1.005, dates[a], dates[t], res,
                              line=(dates[a], real(float(dd["l"][a])), dates[t], real(floor)), touches=[(dates[j], real(float(dd["l"][j]))) for j in br.touch_idxs]))
    return out, pend


def mretest_scan(d: dict, p: Params) -> tuple[list[dict], list[dict]]:
    """A multi-year high is broken, price moves away, comes back to it FOR THE FIRST TIME and holds. Returns (triggers, pending) as dicts {i, b, j, t, level, peak}.
    The old high L = the highest high of the previous `mr_lookback` sessions, at least `mr_min_age` sessions old; the breakout is the daily close above it (the first
    one, since every earlier close is under L). The OLD-HIGH ZONE runs from L x (1 - mr_zone_pct) up to L x (1 + mr_band_pct). The setup needs price to trade `mr_away_pct` %
    above L; the FIRST session after that whose low gets into the zone is the only candidate (a close more than `mr_fail_pct` % under L, a close below the zone, or a weak
    candle uses the breakout up: no trade, and a later return to the same zone is never added). The support candle closes inside or above the zone, in the upper half of its
    range. The trigger is a close above its high within `retest_trigger_days` sessions without trading under its low; a candle that trades under its low first is not support."""
    h, l, c = d["h"], d["l"], d["c"]
    n = len(c)
    out, pend = [], []
    for b in range(max(260, p.mr_min_age + 2), n):
        lo = max(0, b - p.mr_lookback)
        prior = h[lo:b]
        t = lo + int(np.argmax(prior))
        level = float(prior.max())
        if b - t < p.mr_min_age or not (c[b] > level and c[b - 1] <= level):
            continue
        band_top, zone_lo, floor = level * (1 + p.mr_band_pct / 100), level * (1 - p.mr_zone_pct / 100), level * (1 - p.mr_fail_pct / 100)
        peak, away = float(h[b]), h[b] >= level * (1 + p.mr_away_pct / 100)
        for j in range(b + 1, min(n, b + 1 + p.mr_window)):
            if c[j] < floor:
                break                                                              # the breakout failed
            if away and l[j] <= band_top:                                           # the first return to the old high
                rng = h[j] - l[j]
                if rng > 0 and c[j] >= zone_lo and c[j] - l[j] >= 0.5 * rng and l[j] >= floor:
                    dead = False
                    for i in range(j + 1, min(n, j + 1 + p.retest_trigger_days)):
                        if l[i] < l[j]:
                            dead = True                                            # it traded under the support candle's low: not support
                            break
                        if c[i] > h[j]:
                            out.append({"i": i, "b": b, "j": j, "t": t, "level": level, "peak": peak, "zone": (zone_lo, band_top)})
                            dead = True
                            break
                    if not dead and j + p.retest_trigger_days >= n - 1:
                        pend.append({"i": n - 1, "b": b, "j": j, "t": t, "level": level, "peak": peak, "zone": (zone_lo, band_top)})
                break                                                              # first time only
            peak = max(peak, float(h[j]))
            if h[j] >= level * (1 + p.mr_away_pct / 100):
                away = True
    return out, pend


def mretest_why(t: dict, d: dict, dates: pd.DatetimeIndex) -> str:
    j, level = t["j"], t["level"]
    zlo, zhi = t["zone"]
    age = (dates[t["b"]] - dates[t["t"]]).days / 365.25
    return (f"broke its {age:.1f}-year high {level:,.1f} (set {dates[t['t']].strftime('%b %Y')}) on {dates[t['b']].strftime('%d %b')}, rose {(t['peak'] / level - 1) * 100:.0f}% and came back "
            f"into the old-high zone {zlo:,.1f}-{zhi:,.1f} for the first time on {dates[j].strftime('%d %b')} (low {d['l'][j]:,.1f}) - support - and held (close {d['c'][j]:,.1f}); "
            f"buy above its high {d['h'][j]:,.1f}, stop below its low {d['l'][j]:,.1f}")


def mretest_stop(t: dict, d: dict, p: Params) -> float:
    """Under the support candle's low (under the zone's bottom when `mr_stop_zone`), with the usual buffer."""
    low = float(d["l"][t["j"]])
    return (min(low, t["zone"][0]) if p.mr_stop_zone else low) * (1 - p.stop_buffer_pct / 100)


def mretest_geo(t: dict, d: dict, dates: pd.DatetimeIndex, p: Params) -> dict:
    j, level = t["j"], t["level"]
    zlo, zhi = t["zone"]
    return {"from": dates[t["t"]].strftime("%Y-%m-%d"), "to": dates[j].strftime("%Y-%m-%d"), "level": round(float(d["h"][j]), 2), "base": round(float(d["l"][j]), 2), "floor": round(level, 2),
            "band_top": round(zhi, 2), "top": round(zhi, 2), "zone_lo": round(zlo, 2), "zone_hi": round(zhi, 2), "break_date": dates[t["b"]].strftime("%Y-%m-%d"),
            "retest_date": dates[j].strftime("%Y-%m-%d"), "source": "multiyear"}


SOURCE_TEXT =  {"range": "base", "zone": "weekly zone", "trendline": "trendline"}


def retest_why(t: dict, d: dict, dates: pd.DatetimeIndex) -> str:
    h, l, c, o = d["h"], d["l"], d["c"], d["o"]
    j, long = t["j"], t["side"] == "long"
    rng = h[j] - l[j]
    src = SOURCE_TEXT[t["source"]]
    lo, hi = sorted((t["floor"], t["top"]))
    if long:
        wick = (min(o[j], c[j]) - l[j]) / rng * 100
        what = {"range": f"base {lo:,.1f}-{hi:,.1f}", "zone": f"weekly resistance zone {lo:,.1f}-{hi:,.1f}", "trendline": f"falling trendline (now {t['floor']:,.1f})"}[t["source"]]
        return (f"{what} broke out on {dates[t['b']].strftime('%d %b')}; price came back to {l[j]:,.1f} on {dates[j].strftime('%d %b')} - the old {src} as support - and held there "
                f"(lower wick {wick:.0f}% of the candle, close {c[j]:,.1f}); buy above its high {h[j]:,.1f}, stop at its low {l[j]:,.1f}")
    wick = (h[j] - max(o[j], c[j])) / rng * 100
    what = {"range": f"base {lo:,.1f}-{hi:,.1f}", "zone": f"weekly support zone {lo:,.1f}-{hi:,.1f}", "trendline": f"rising trendline (now {t['floor']:,.1f})"}[t["source"]]
    return (f"{what} broke down on {dates[t['b']].strftime('%d %b')}; price came back to {h[j]:,.1f} on {dates[j].strftime('%d %b')} - the old {src} as resistance - and failed there "
            f"(upper wick {wick:.0f}% of the candle, close {c[j]:,.1f}); short below its low {l[j]:,.1f}, stop at its high {h[j]:,.1f}")


def retest_geo(t: dict, d: dict, dates: pd.DatetimeIndex) -> dict:
    j, long = t["j"], t["side"] == "long"
    g = {"from": t["from_date"].strftime("%Y-%m-%d"), "to": t["to_date"].strftime("%Y-%m-%d"), "level": round(float(d["h"][j] if long else d["l"][j]), 2),
         "base": round(float(d["l"][j] if long else d["h"][j]), 2), "floor": round(float(t["floor"]), 2), "band_top": round(float(t["band_top"]), 2), "top": round(float(t["top"]), 2),
         "break_date": dates[t["b"]].strftime("%Y-%m-%d"), "retest_date": dates[j].strftime("%Y-%m-%d"), "source": t["source"]}
    if t.get("line"):
        x0, y0, x1, y1 = t["line"]
        g["line"] = [x0.strftime("%Y-%m-%d"), round(float(y0), 2), x1.strftime("%Y-%m-%d"), round(float(y1), 2)]
        g["touches"] = [[x.strftime("%Y-%m-%d"), round(float(y), 2)] for x, y in t["touches"]]
    return g


# ---------------------------------------------------------------- resistance and the plan
def next_resistance(wH: np.ndarray, w: int, above: float, p: Params) -> float | None:
    """The lowest weekly resistance above `above`, known at week w: confirmed swing highs of the last res_lookback_weeks and the last few weeks' high."""
    lo = max(0, w - p.res_lookback_weeks + 1)
    cands = [wH[i] for i in _pivots(wH, p.pivot_k, lo, w, True)]
    cands.append(wH[max(0, w - p.recent_weeks + 1):w + 1].max())
    higher = [c for c in cands if c > above]
    return float(min(higher)) if higher else None


def next_support(wL: np.ndarray, w: int, below: float, p: Params) -> float | None:
    """The highest weekly support below `below`, known at week w: confirmed swing lows and the last few weeks' low (the mirror of next_resistance)."""
    lo = max(0, w - p.res_lookback_weeks + 1)
    cands = [wL[i] for i in _pivots(wL, p.pivot_k, lo, w, False)]
    cands.append(wL[max(0, w - p.recent_weeks + 1):w + 1].min())
    lower = [c for c in cands if c < below]
    return float(max(lower)) if lower else None


def plan(ref: float, stop: float, wH: np.ndarray, w: int, height: float, level: float, p: Params, side: str = "long", wL: np.ndarray | None = None) -> dict | None:
    """Stop / target / reward:risk for an entry at `ref`. None when the risk is outside the limits or there is no sensible target. A short is the
    mirror: stop above, target = the next weekly support below (or the pattern's height projected down)."""
    if side == "short":
        risk = stop - ref
        if not risk > 0 or not (p.min_risk_pct <= risk / ref * 100 <= p.max_risk_pct):
            return None
        sup = next_support(wL, w, ref - 0.5 * risk, p)
        target, kind = (sup, "support") if sup is not None else (min(level, ref) - height, "measured")
        if not 0 < target < ref:
            return None
        return {"stop": float(stop), "target": float(target), "target_kind": kind, "risk_pct": risk / ref * 100, "reward_pct": (1 - target / ref) * 100,
                "rr": (ref - target) / risk}
    risk = ref - stop
    if not risk > 0 or not (p.min_risk_pct <= risk / ref * 100 <= p.max_risk_pct):
        return None
    res = next_resistance(wH, w, ref + 0.5 * risk, p)             # a level closer than half the risk is noise, not a target
    target, kind = (res, "resistance") if res is not None else (max(level, ref) + height, "measured")
    if not target > ref:
        return None
    return {"stop": float(stop), "target": float(target), "target_kind": kind, "risk_pct": risk / ref * 100, "reward_pct": (target / ref - 1) * 100,
            "rr": (target - ref) / risk}


# ---------------------------------------------------------------- one stock
def _features(d: dict, i: int) -> dict:
    c, v = d["c"], d["v"]
    vol = v[i] / v[i - 20:i].mean() if i >= 20 and v[i - 20:i].mean() > 0 else np.nan
    sma200 = c[i - 199:i + 1].mean() if i >= 199 else np.nan
    hi252, lo252 = d["h"][max(0, i - 251):i + 1].max(), d["l"][max(0, i - 251):i + 1].min()
    return {"vol": float(vol), "above200": float(c[i] > sma200) if not np.isnan(sma200) else np.nan, "off_high": float(c[i] / hi252 - 1), "off_low": float(c[i] / lo252 - 1),
            "r60": float(c[i] / c[i - 60] - 1) if i >= 60 else np.nan, "day_move": float(c[i] / c[i - 1] - 1) if i else np.nan}


def scan_stock(df: pd.DataFrame, p: Params = Params()) -> tuple[list[dict], list[dict]]:
    """(every trigger in the history, the structures armed right now but not triggered). Each trigger: kind, i (daily index of the trigger close),
    level, stop / target / rr planned at that close, reason, features, geometry for the chart."""
    if df is None or len(df) < 260:
        return [], []
    dates = pd.DatetimeIndex(df.index).normalize()
    d = {"o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float), "l": df["low"].to_numpy(float), "c": df["close"].to_numpy(float),
         "v": df["volume"].to_numpy(float) if "volume" in df else np.full(len(df), np.nan)}
    wk = weekly_bars(df.set_axis(dates))
    wH, wL, wC = (wk[c].to_numpy(float) for c in ("high", "low", "close"))
    wdates = wk.index
    n, buf = len(dates), p.stop_buffer_pct / 100
    wk_of = np.searchsorted(wdates.values, dates.values, side="left") - 1           # the last week that closed BEFORE each session's own week
    triggers, pending = [], []
    last_trigger: dict = {}

    def geometry(kind: str, pat: dict) -> dict:
        wd = lambda x: wdates[min(x, len(wdates) - 1)].strftime("%Y-%m-%d")         # noqa: E731
        g = {"from": wd(pat["start"]), "to": wd(pat["end"]), "level": round(pat["level"], 2), "base": round(pat["base"], 2)}
        if kind == "triangle":
            g["shape"] = pat["shape"]
            for key in ("upper", "lower"):
                x0, y0, x1, y1 = pat[key]
                g[key] = [wd(x0), round(y0, 2), wd(x1), round(y1, 2)]
        if kind == "cup":
            g.update(left=wd(pat["left"]), bottom=wd(pat["bottom"]), right=wd(pat["right"]), bottom_px=round(pat["bottom_px"], 2))
        if "pts" in pat:                                                            # the shape's skeleton and its neckline / trigger line
            x0, y0, x1, y1 = pat["neck"]
            g.update(pts=[[wd(x), round(y, 2)] for x, y in pat["pts"]], neck=[wd(x0), round(y0, 2), wd(x1), round(y1, 2)])
        return g

    # --- patterns: the structure as of the last completed week, a daily close beyond its level during the following week (above for a long, below for a short)
    cache: dict[int, dict] = {}
    for i in range(260, n):
        w = int(wk_of[i])
        if w < 40:
            continue
        if w not in cache:
            cache[w] = patterns_at(wH, wL, wC, w, p)
        if not cache[w]:
            continue
        first = int(np.searchsorted(dates.values, wdates.values[w], side="right"))        # first session after that week closed
        c = d["c"][i]
        for (kind, side), pat in cache[w].items():
            long, level = side == "long", pat["level"]
            if i - last_trigger.get((kind, side), -10 ** 9) < p.cooldown_days:
                continue
            beyond = (d["c"][first:i + 1] > level) if long else (d["c"][first:i + 1] < level)
            if not beyond[-1] or beyond[:-1].any():
                continue
            stop = min(level, d["l"][i]) * (1 - buf) if long else max(level, d["h"][i]) * (1 + buf)
            pl = plan(c, stop, wH, w, pat["height"], level, p, side, wL)
            last_trigger[(kind, side)] = i
            if pl is None:
                continue
            triggers.append({"kind": kind, "side": side, "i": i, "w": w, "level": level, **pl, "geo": geometry(kind, pat), "f": _features(d, i),
                             "reason": f"{pat['why']}; daily close {c:,.1f} {'above' if long else 'below'} {level:,.1f}"})
    w_last = int(wk_of[n - 1]) if n else -1
    if n and w_last >= 40:                                                        # armed now: structure intact and the close just short of its level
        first = int(np.searchsorted(dates.values, wdates.values[w_last], side="right"))
        c = d["c"][n - 1]
        for (kind, side), pat in (cache.get(w_last) or patterns_at(wH, wL, wC, w_last, p)).items():
            long, level = side == "long", pat["level"]
            beyond = (d["c"][first:n] > level) if long else (d["c"][first:n] < level)
            near = (level * (1 - p.near_pct / 100) <= c <= level) if long else (level <= c <= level * (1 + p.near_pct / 100))
            if beyond.any() or not near:
                continue
            entry = level * (1 + buf) if long else level * (1 - buf)
            stop = min(level, d["l"][n - 1]) * (1 - buf) if long else max(level, d["h"][n - 1]) * (1 + buf)
            pl = plan(entry, stop, wH, w_last, pat["height"], level, p, side, wL)
            if pl is not None:
                gap = abs(level / c - 1) * 100
                pending.append({"kind": kind, "side": side, "i": n - 1, "w": w_last, "level": level, "entry": entry, **pl, "geo": geometry(kind, pat), "f": _features(d, n - 1),
                                "reason": (f"{pat['why']}; {gap:.1f}% {'below' if long else 'above'} the level - "
                                           f"{'buy on a daily close above' if long else 'sell on a daily close below'} {level:,.1f}")})

    def confluent(i: int, lo: float, hi: float, side: str) -> dict:
        """Does a daily trendline in the trade's direction (a rising support line for a long zone, a falling resistance line for a supply zone) run through the zone
        (+/- 1%) as of session i? Uses only candles up to i."""
        lines = swing_trendlines.analyze(df.iloc[:i + 1])
        zone = {"d": "bull" if side == "long" else "bear", "lo": lo, "hi": hi}
        gap = swing_trendlines.zone_gap_pct(zone, lines)
        return {"cf": bool(swing_trendlines.confluence(zone, lines)), "tl_gap": None if gap is None else round(float(gap), 2)}

    # --- weekly support zones: closed inside, then a daily close back out above the zone
    zp = ZoneParams(side="long", impulse_atr=p.zone_impulse_atr, expiry_weeks=p.zone_expiry_weeks)
    for z in find_zones(df.set_axis(dates), zp):
        start = int(dates.searchsorted(z["known"], side="right"))
        end = min(n, int(dates.searchsorted(z["known"] + pd.Timedelta(weeks=p.zone_expiry_weeks), side="right")))
        lo, hi = z["lo"], z["hi"]
        entered = None
        geo = {"from": z["formed"].strftime("%Y-%m-%d"), "to": None, "level": round(hi, 2), "base": round(lo, 2)}
        why = f"weekly support zone {lo:,.1f}-{hi:,.1f} (formed {z['formed'].strftime('%d %b %Y')}, thrust {z['impulse']:.1f} ATR)"
        done = False
        for i in range(start, end):
            c = d["c"][i]
            if c < lo * (1 - buf):
                done = True
                break                                                             # a close below the zone: it failed
            if entered is None:
                if c <= hi:
                    entered = i
                continue
            if c > hi:
                done = True
                w = int(wk_of[i])
                if i >= 260 and w >= 40 and i - last_trigger.get(("zone", "long"), -10 ** 9) >= p.cooldown_days:
                    pl = plan(c, lo * (1 - buf), wH, w, hi - lo, hi, p)
                    last_trigger[("zone", "long")] = i
                    if pl is not None:
                        triggers.append({"kind": "zone", "side": "long", "i": i, "w": w, "level": hi, **pl, **confluent(i, lo, hi, "long"), "geo": geo, "f": {**_features(d, i), "impulse": z["impulse"]},
                                         "reason": f"{why}; in the zone from {dates[entered].strftime('%d %b')}, daily close {c:,.1f} back above it"})
                break
        if not done and entered is not None and end == n and w_last >= 40:        # in the zone right now
            entry = hi * (1 + buf)
            pl = plan(entry, lo * (1 - buf), wH, w_last, hi - lo, hi, p)
            if pl is not None:
                pending.append({"kind": "zone", "side": "long", "i": n - 1, "w": w_last, "level": hi, "entry": entry, **pl, **confluent(n - 1, lo, hi, "long"), "geo": geo, "f": {**_features(d, n - 1), "impulse": z["impulse"]},
                                "reason": f"{why}; price is in the zone since {dates[entered].strftime('%d %b')} - buy on a daily close back above {hi:,.1f}"})
    # --- weekly resistance (supply) zones, SHORT: price rallied into the zone, then a daily close back out below it; the stop is above the zone
    zs = ZoneParams(side="short", impulse_atr=p.zone_impulse_atr, expiry_weeks=p.zone_expiry_weeks)
    supply = []                                                                   # the zones' triggers, sorted by time before the cooldown is applied (no look-ahead)
    for z in find_zones(df.set_axis(dates), zs):
        start = int(dates.searchsorted(z["known"], side="right"))
        end = min(n, int(dates.searchsorted(z["known"] + pd.Timedelta(weeks=p.zone_expiry_weeks), side="right")))
        lo, hi = z["lo"], z["hi"]
        entered = None
        geo = {"from": z["formed"].strftime("%Y-%m-%d"), "to": None, "level": round(lo, 2), "base": round(hi, 2)}
        why = f"weekly resistance zone {lo:,.1f}-{hi:,.1f} (formed {z['formed'].strftime('%d %b %Y')}, thrust {z['impulse']:.1f} ATR)"
        done = False
        for i in range(start, end):
            c = d["c"][i]
            if c > hi * (1 + buf):
                done = True
                break                                                             # a close above the zone: it failed
            if entered is None:
                if c >= lo:
                    entered = i
                continue
            if c < lo:
                done = True
                w = int(wk_of[i])
                if i >= 260 and w >= 40:
                    supply.append((i, w, plan(c, hi * (1 + buf), wH, w, hi - lo, lo, p, "short", wL), lo, hi, geo, why, entered, z["impulse"]))
                break
        if not done and entered is not None and end == n and w_last >= 40:        # in the zone right now
            entry = lo * (1 - buf)
            pl = plan(entry, hi * (1 + buf), wH, w_last, hi - lo, lo, p, "short", wL)
            if pl is not None:
                pending.append({"kind": "zone", "side": "short", "i": n - 1, "w": w_last, "level": lo, "entry": entry, **pl, **confluent(n - 1, lo, hi, "short"), "geo": geo,
                                "f": {**_features(d, n - 1), "impulse": z["impulse"]},
                                "reason": f"{why}; price is in the zone since {dates[entered].strftime('%d %b')} - sell on a daily close back below {lo:,.1f}"})
    last_sz = -10 ** 9
    for i, w, pl, lo, hi, geo, why, entered, impulse in sorted(supply, key=lambda t: t[0]):
        if i - last_sz < p.cooldown_days:
            continue
        last_sz = i
        if pl is not None:
            triggers.append({"kind": "zone", "side": "short", "i": i, "w": w, "level": lo, **pl, **confluent(i, lo, hi, "short"), "geo": geo, "f": {**_features(d, i), "impulse": impulse},
                             "reason": f"{why}; in the zone from {dates[entered].strftime('%d %b')}, daily close {d['c'][i]:,.1f} back below it"})
    # --- trendline breakdown (short): a rising support line through at least three swing lows is broken by a red daily close, the first one below it
    tp = daily_params()
    pivots = swing_lows(d["l"], tp.swing_bars)
    last_tl = -10 ** 9
    for i in range(260, n):
        if i - last_tl < p.cooldown_days or int(wk_of[i]) < 40:
            continue
        br = find_break(d["o"], d["h"], d["l"], d["c"], i, pivots, tp)
        if br is None:
            continue
        w = int(wk_of[i])
        height = max(br.line_at_break - br.line_at_break / (1 + br.rise_pct / 100), 0.0)
        pl = plan(d["c"][i], br.stop, wH, w, height, br.line_at_break, p, "short", wL)
        last_tl = i
        if pl is None:
            continue
        a, ds = br.anchor_idx, (lambda x: dates[x].strftime("%Y-%m-%d"))
        geo = {"from": ds(a), "to": ds(i), "level": round(br.line_at_break, 2), "base": round(float(d["l"][a]), 2), "line": [ds(a), round(float(d["l"][a]), 2), ds(i), round(br.line_at_break, 2)],
               "touches": [[ds(j), round(float(d["l"][j]), 2)] for j in br.touch_idxs]}
        triggers.append({"kind": "trendline", "side": "short", "i": i, "w": w, "level": br.line_at_break, **pl, "geo": geo, "f": _features(d, i),
                         "reason": (f"rising trendline through {br.touches} swing lows over {br.width_bars} sessions (up {br.rise_pct:.0f}%), broken by a red daily close "
                                    f"{d['c'][i]:,.1f} that is {br.break_depth_pct:.1f}% under the line ({br.line_at_break:,.1f})")})
    # --- break and retest, both ways: a structure (a base, a weekly zone, a trendline) is broken, price comes back to retest it and the rejection candle shows it holding as the
    # --- other side's wall; the trigger is a close beyond that candle (below its low for a short, above its high for a long), the stop the candle's other end
    for side in ("short", "long"):
        long = side == "long"
        rt, rp = retest_scan(d, dates, wk_of, wdates, wH, wL, wC, p, side, df)
        last_rt: dict = {}
        for t in sorted(rt, key=lambda x: x["i"]):
            i, j = t["i"], t["j"]
            if i < 260 or i - last_rt.get(t["source"], -10 ** 9) < p.cooldown_days or int(wk_of[i]) < 40:
                continue
            w = int(wk_of[i])
            stop = d["l"][j] * (1 - buf) if long else d["h"][j] * (1 + buf)
            pl = plan(d["c"][i], stop, wH, w, abs(t["top"] - t["floor"]), t["floor"], p, side, wL)
            last_rt[t["source"]] = i
            if pl is not None:
                triggers.append({"kind": "retest", "side": side, "i": i, "w": w, "level": float(d["h"][j] if long else d["l"][j]), **pl, "geo": retest_geo(t, d, dates), "f": _features(d, i),
                                 "reason": retest_why(t, d, dates) + f"; daily close {d['c'][i]:,.1f} {'above' if long else 'below'} it"})
        for t in rp:
            j = t["j"]
            w = int(wk_of[n - 1])
            if w < 40 or n - 1 < 260:
                continue
            entry = d["h"][j] * (1 + buf) if long else d["l"][j] * (1 - buf)
            stop = d["l"][j] * (1 - buf) if long else d["h"][j] * (1 + buf)
            pl = plan(entry, stop, wH, w, abs(t["top"] - t["floor"]), t["floor"], p, side, wL)
            if pl is not None:
                pending.append({"kind": "retest", "side": side, "i": n - 1, "w": w, "level": float(d["h"][j] if long else d["l"][j]), "entry": entry, **pl, "geo": retest_geo(t, d, dates), "f": _features(d, n - 1),
                                "reason": retest_why(t, d, dates) + f"; waiting for a daily close {'above' if long else 'below'} {d['h'][j] if long else d['l'][j]:,.1f}"})
    # --- a multi-year high broken, then its first retest holds as support (long only)
    mr, mp = mretest_scan(d, p)
    for t in mr:
        i, j = t["i"], t["j"]
        if i < 260 or int(wk_of[i]) < 40:
            continue
        w = int(wk_of[i])
        pl = plan(d["c"][i], mretest_stop(t, d, p), wH, w, max(t["peak"] - t["level"], 0.0), t["level"], p, "long", wL)
        if pl is not None:
            triggers.append({"kind": "mretest", "side": "long", "i": i, "w": w, "level": float(d["h"][j]), **pl, "geo": mretest_geo(t, d, dates, p), "f": _features(d, i),
                             "reason": mretest_why(t, d, dates) + f"; daily close {d['c'][i]:,.1f} above it"})
    for t in mp:
        j = t["j"]
        w = int(wk_of[n - 1])
        if w < 40 or n - 1 < 260:
            continue
        entry = d["h"][j] * (1 + buf)
        pl = plan(entry, mretest_stop(t, d, p), wH, w, max(t["peak"] - t["level"], 0.0), t["level"], p, "long", wL)
        if pl is not None:
            pending.append({"kind": "mretest", "side": "long", "i": n - 1, "w": w, "level": float(d["h"][j]), "entry": entry, **pl, "geo": mretest_geo(t, d, dates, p), "f": _features(d, n - 1),
                            "reason": mretest_why(t, d, dates) + f"; waiting for a daily close above {d['h'][j]:,.1f}"})
    triggers.sort(key=lambda t: t["i"])
    return triggers, pending
