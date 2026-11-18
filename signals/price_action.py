"""Price-action setups: weekly structure, traded on the daily chart (long side).

Nine kinds, all found on COMPLETED weekly candles and triggered by a DAILY close outside the structure. Bullish ones are longs; the bearish
twins (M pattern, head and shoulders, triple top, bear flag) are shorts - the same detectors run on inverted prices:
  multiyear a close above the highest weekly high of the last 2-5 years, set at least 26 weeks ago.
  double    W pattern (double bottom) / M pattern (double top): two swing lows (highs) at the same level; a close through the neckline.
  hs        inverse head and shoulders / head and shoulders; a close through the neckline joining the two peaks (troughs).
  triple    triple bottom / triple top; a close through the neckline.
  flag      bull / bear flag: a sharp pole, a short shallow pullback; a close through the pole's end.
and the four below:
  zone      weekly support (a weekly demand zone - signals/weekly_zone_backtest.find_zones): price closed inside the zone and then a daily
            close comes back out above it.
  range     8-40 weeks inside a box no wider than RANGE_MAX_WIDTH with at least two touches of each side; a daily close above the top.
  triangle  falling (or flat) highs and rising lows that converge - symmetric / ascending; a daily close above the upper line.
  cup       cup and handle: two rims at a similar level around a rounded 12-45% dip, then a shallow handle; a daily close above the rim.

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

from signals.weekly_zone_backtest import Params as ZoneParams
from signals.weekly_zone_backtest import find_zones, weekly_bars

KINDS = ("zone", "range", "triangle", "cup", "multiyear", "double", "hs", "triple", "flag", "candle", "pause")
NAMES = {("zone", "long"): "Weekly support zone", ("range", "long"): "Weekly range breakout", ("triangle", "long"): "Triangle breakout", ("cup", "long"): "Cup and handle",
         ("multiyear", "long"): "Multi-year breakout", ("double", "long"): "W pattern (double bottom)", ("double", "short"): "M pattern (double top)",
         ("hs", "long"): "Inverse head and shoulders", ("hs", "short"): "Head and shoulders", ("triple", "long"): "Triple bottom", ("triple", "short"): "Triple top",
         ("flag", "long"): "Bull flag", ("flag", "short"): "Bear flag", ("candle", "long"): "Candle at weekly support", ("candle", "short"): "Candle at weekly resistance", ("pause", "short"): "Bearish continuation (pause candle)"}
KIND_NAMES = {k: next(n for (kk, _), n in NAMES.items() if kk == k) for k in KINDS}      # the name of each kind (its long side when it has one)


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
    candle_cooldown: int = 10           # a candle signal at the same zone is not repeated within this many sessions
    candle_min_range_atr: float = 0.6   # the signal candle's range must be this many ATRs (a doji in a tight range is noise)
    candle_arrive_back: int = 6         # the zone must have been reached from outside: the close this many sessions earlier was beyond it
    zone_impulse_atr: float = 2.0
    zone_expiry_weeks: int = 104
    near_pct: float = 3.0               # "pending": the close is within this % below the breakout level
    pause_min_run: int = 3              # bearish continuation: at least this many consecutive strong red candles before the pause candle
    pause_max_run: int = 8
    pause_min_drop_atr: float = 2.0     # the run must have fallen at least this many ATRs, open of the first red candle to close of the last
    pause_body_frac: float = 0.4        # each red candle's body is at least this share of its range ...
    pause_close_frac: float = 0.4       # ... and it closes in the lowest 40% of its range (sellers in control to the close)
    pause_doji_body: float = 0.3        # a doji: body at most this share of its range
    pause_min_range_atr: float = 0.3    # the pause candle is a real candle (not a flat bar) ...
    pause_max_range_atr: float = 1.3    # ... and not a big one
    pause_window_days: int = 3          # a daily pause candle: the short must trigger within this many sessions
    pause_cooldown: int = 10            # one continuation signal per timeframe in this many sessions


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
    """The highest weekly high of the last 2-5 years, set at least 26 weeks ago, with the last close within 15% under it."""
    n = min(w + 1, 260)
    if n < 104:
        return None
    a = w - n + 1
    t = a + int(np.argmax(H[a:w + 1]))
    level = H[t]
    if w - t < 26 or C[w] > level or C[w] < level * 0.85:
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
    """Bull flag: a pole of at least 20% in 8 weeks or fewer, then 2-6 weeks that pull back 3% or more but give up no more than half the pole."""
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
    return out


def describe(kind: str, side: str, pat: dict) -> str:
    """The reason in words (real prices)."""
    long = side == "long"
    level, base, move = pat["level"], pat["base"], abs(pat["hto"] / pat["hfrom"] - 1) * 100
    if kind == "multiyear":
        return f"{pat['years']:.0f}-year high {level:,.1f}, set {pat['end'] - pat['start']} weeks ago; the base since then went down to {base:,.1f}"
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


# (kind, side, detector, new-style). Order matters only for the reversal shapes: a head and shoulders outranks a triple, a triple outranks a double.
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
        else:
            inv = inv or (1.0 / wL, 1.0 / wH, 1.0 / wC)
            pat = fn(*inv, w, p)
            pat = _unmirror(pat) if pat else None
        if pat is None:
            continue
        if new:
            pat = {**pat, "height": abs(pat["hto"] - pat["hfrom"]), "why": describe(kind, side, pat)}
        found[(kind, side)] = pat
    return found


# ---------------------------------------------------------------- reversal candles at a zone
CANDLE_NAMES = {"long": {"hammer": "Hammer", "engulfing": "Bullish engulfing", "piercing": "Piercing line", "star": "Morning star"},
                "short": {"hammer": "Shooting star", "engulfing": "Bearish engulfing", "piercing": "Dark cloud cover", "star": "Evening star"}}


def reversal_candle(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, i: int, atr: float, min_range_atr: float = 0.6, side: str = "long") -> dict | None:
    """The bullish reversal candle that ends at session i, or None: hammer, bullish engulfing, piercing line or morning star (the first that applies).
    The bearish ones are the same function on NEGATED prices (open, close -> -open, -close; high, low -> -low, -high), so every wick and body
    keeps its size and the two sides are exact mirrors. `side` only words the explanation for the real chart. Returns {"kind", "low" (the lowest
    low of the candles in the pattern, in the mirrored world), "why"}."""
    short = side == "short"
    up, down, wick, color, pcolor = ("lower", "upper", "lower wick", "green", "red") if not short else ("upper", "lower", "upper wick", "red", "green")
    rng = h[i] - l[i]
    if i < 3 or not rng > 0 or not atr > 0 or rng < min_range_atr * atr:
        return None
    body, lower, upper = abs(c[i] - o[i]), min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
    p_body = abs(c[i - 1] - o[i - 1])
    p_red, p_green = c[i - 1] < o[i - 1], c[i] > o[i]
    # engulfing: a red candle with a real body, then a green one whose body covers it
    if p_red and c[i] > o[i] and p_body >= 0.3 * atr and o[i] <= c[i - 1] and c[i] >= o[i - 1] and body > p_body:
        return {"kind": "engulfing", "low": float(min(l[i], l[i - 1])), "why": f"its {color} body ({body:,.2f}) swallowed the previous {pcolor} one ({p_body:,.2f})"}
    # piercing line: red candle with a real body, opens below its close, closes back through its midpoint without passing its open
    mid = (o[i - 1] + c[i - 1]) / 2
    if p_red and p_green and p_body >= 0.5 * atr and o[i] < c[i - 1] and mid < c[i] < o[i - 1]:
        return {"kind": "piercing", "low": float(min(l[i], l[i - 1])), "why": (f"it opened {'above' if short else 'below'} the previous close and closed back {'below' if short else 'above'} that {pcolor} candle's midpoint ({abs(mid):,.2f})")}
    # morning star: long red, a small candle below it, then a green close back above the red one's midpoint
    mid1 = (o[i - 2] + c[i - 2]) / 2
    if (c[i - 2] < o[i - 2] and abs(c[i - 2] - o[i - 2]) >= 0.5 * atr and abs(c[i - 1] - o[i - 1]) <= 0.4 * abs(c[i - 2] - o[i - 2])
            and max(o[i - 1], c[i - 1]) <= c[i - 2] + 0.1 * atr and p_green and c[i] > mid1):
        return {"kind": "star", "low": float(min(l[i], l[i - 1], l[i - 2])), "why": f"a long {pcolor} candle, a small candle {'above' if short else 'below'} it, then a {color} close back {'below' if short else 'above'} the first one's midpoint"}
    # hammer: a small body in the top third of the range, a lower wick at least twice the body, almost no upper wick
    if lower >= 2 * max(body, 0.05 * rng) and upper <= 0.35 * lower and min(o[i], c[i]) >= l[i] + 0.55 * rng:
        return {"kind": "hammer", "low": float(l[i]), "why": f"{'an' if short else 'a'} {wick} of {lower:,.2f} against a body of {body:,.2f}: {'buyers pushed it up and sellers' if short else 'sellers pushed it down and buyers'} took all of it back"}
    return None


def candle_triggers(d: dict, zones: list[dict], dates: pd.DatetimeIndex, wk_of: np.ndarray, wH: np.ndarray, wL: np.ndarray, side: str, p: Params) -> list[dict]:
    """Reversal candles that form as price reaches a live weekly zone of the side (demand for a long, supply for a short). The mirror is exact:
    for a short everything is negated, which turns a supply zone into a demand zone and a bearish candle into a bullish one."""
    sg, buf, n = (1 if side == "long" else -1), p.stop_buffer_pct / 100, len(dates)
    o, h, l, c = (sg * d["o"], sg * d["h"], sg * d["l"], sg * d["c"]) if sg == 1 else (-d["o"], -d["l"], -d["h"], -d["c"])
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    out = []
    for z in zones:
        lo, hi = (z["lo"], z["hi"]) if sg == 1 else (-z["hi"], -z["lo"])            # in the mirrored world the zone is always a demand zone: lo < hi
        start = int(dates.searchsorted(z["known"], side="right"))
        end = min(n, int(dates.searchsorted(z["known"] + pd.Timedelta(weeks=p.zone_expiry_weeks), side="right")))
        shown = {"from": z["formed"].strftime("%Y-%m-%d"), "to": None, "level": round(max(z["hi"], z["lo"]), 2), "base": round(min(z["hi"], z["lo"]), 2)}
        for i in range(max(start, 260, p.candle_arrive_back + 1), end):
            if c[i] < lo - abs(lo) * buf:
                break                                                                 # a close beyond the far edge: the zone failed
            if not (l[i] <= hi and h[i] >= lo and c[i] >= lo):                        # the candle must reach the zone and not close under it
                continue
            if not c[i - p.candle_arrive_back] > hi:                                  # it must have come from outside: a pullback to the zone
                continue
            sig = reversal_candle(o, h, l, c, i, float(atr[i]), p.candle_min_range_atr, side)
            if sig is None:
                continue
            stop_m = sig["low"] - abs(sig["low"]) * buf                               # just beyond the pattern's extreme (mirrored world)
            name = CANDLE_NAMES[side][sig["kind"]]
            w = int(wk_of[i])
            if w < 40:
                continue
            real_low = sg * stop_m if sg == 1 else -stop_m                            # back to real prices
            out.append({"i": i, "w": w, "zone": z, "name": name, "kind_code": sig["kind"], "stop": float(real_low), "level": float(shown["level"] if sg == 1 else shown["base"]),
                        "geo": {**shown, "candle": name, "candle_date": dates[i].strftime("%Y-%m-%d")},
                        "why": f"{name} at the weekly {'support' if sg == 1 else 'resistance'} zone {shown['base']:,.1f}-{shown['level']:,.1f} (formed {z['formed'].strftime('%d %b %Y')}, thrust {z['impulse']:.1f} ATR): "
                               f"{sig['why']}; the day {'low' if sg == 1 else 'high'} reached the zone and it closed {d['c'][i]:,.1f}"})
    out.sort(key=lambda t: (t["i"], t["zone"]["formed"]))                           # the cooldown is applied in date order, whatever order the zones came in
    kept, last = [], -10 ** 9
    for t in out:
        if t["i"] - last >= p.candle_cooldown:
            kept.append(t)
            last = t["i"]
    return kept


# ---------------------------------------------------------------- bearish continuation: strong selling, then a pause candle
def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def pause_at(O: np.ndarray, H: np.ndarray, L: np.ndarray, C: np.ndarray, atr: np.ndarray, sma: np.ndarray, j: int, p: Params) -> dict | None:
    """The pause candle at bar j after clear selling, or None. Selling pressure = at least `pause_min_run` consecutive red candles, each with a real body
    (>= pause_body_frac of its range), closing in the lowest part of its range and lower than the candle before, together falling at least
    `pause_min_drop_atr` ATRs. The pause = a doji, or a small green candle (body at most half the run's average body), that does not exceed the last red
    candle's high, is of ordinary size, and closes under its moving average (the stock is in a downtrend). Works on any timeframe's bars."""
    a = atr[j] if j < len(atr) else float("nan")
    if j < 20 or not a > 0 or np.isnan(sma[j]):
        return None
    rng, body = H[j] - L[j], abs(C[j] - O[j])
    if not (p.pause_min_range_atr * a <= rng <= p.pause_max_range_atr * a) or not C[j] < sma[j] or H[j] > H[j - 1]:
        return None
    k = 0
    while k < p.pause_max_run and j - 1 - k >= 1:
        m = j - 1 - k
        r = H[m] - L[m]
        if not (C[m] < O[m] and r > 0 and O[m] - C[m] >= p.pause_body_frac * r and C[m] - L[m] <= p.pause_close_frac * r and C[m] < C[m - 1]):
            break
        k += 1
    if k < p.pause_min_run:
        return None
    first = j - k
    drop = O[first] - C[j - 1]
    if drop < p.pause_min_drop_atr * a:
        return None
    avg_body = float(np.mean(O[first:j] - C[first:j]))
    doji = body <= p.pause_doji_body * rng
    if not doji and not (C[j] > O[j] and body <= 0.5 * avg_body):
        return None
    return {"j": j, "run": k, "from": first, "drop": float(drop), "drop_atr": float(drop / a), "drop_pct": float((1 - C[j - 1] / O[first]) * 100),
            "pause": "Doji" if doji else "Small green candle", "low": float(L[j]), "high": float(H[j]), "avg_body": avg_body}


def pause_scan(d: dict, dates: pd.DatetimeIndex, wk_of: np.ndarray, wdates: pd.DatetimeIndex, wO: np.ndarray, wH: np.ndarray, wL: np.ndarray, wC: np.ndarray,
               p: Params) -> tuple[list[dict], list[dict]]:
    """Bearish continuation shorts on the daily and the weekly chart. The short triggers on a DAILY close below the pause candle's low and its stop is the
    pause candle's high: a daily pause candle must trigger within `pause_window_days` sessions, a weekly one during the following week; if price trades above
    the pause candle's high first, the setup is dead. Returns (triggers, pending); each item has the raw pause info under "pat" and "tf"."""
    n, out, pend = len(dates), [], []
    o, h, l, c = d["o"], d["h"], d["l"], d["c"]
    # daily pause candles
    atr_d, sma_d = _atr(h, l, c), pd.Series(c).rolling(20).mean().to_numpy()
    for j in range(260, n):
        pat = pause_at(o, h, l, c, atr_d, sma_d, j, p)
        if pat is None:
            continue
        hit = None
        for i in range(j + 1, min(n, j + 1 + p.pause_window_days)):
            if h[i] > pat["high"]:
                hit = -1
                break
            if c[i] < pat["low"]:
                hit = i
                break
        if hit is not None and hit > 0:
            out.append({"tf": "daily", "i": hit, "pat": pat, "from_date": dates[pat["from"]], "pause_date": dates[j]})
        elif hit is None and j + p.pause_window_days >= n - 1 and n - 1 >= j:      # still armed on the latest bar
            pend.append({"tf": "daily", "i": n - 1, "pat": pat, "from_date": dates[pat["from"]], "pause_date": dates[j]})
    # weekly pause candles: the week is complete, the trigger is a daily close in the next week
    atr_w, sma_w = _atr(wH, wL, wC), pd.Series(wC).rolling(10).mean().to_numpy()
    w_last = int(wk_of[n - 1]) if n else -1
    for w in range(40, len(wC)):
        pat = pause_at(wO, wH, wL, wC, atr_w, sma_w, w, p)
        if pat is None:
            continue
        first = int(np.searchsorted(dates.values, wdates.values[w], side="right"))       # the first session after that week closed
        hit, i = None, first
        while i < n and int(wk_of[i]) == w:                                              # the sessions of the following week
            if h[i] > pat["high"]:
                hit = -1
                break
            if c[i] < pat["low"]:
                hit = i
                break
            i += 1
        if hit is not None and hit > 0 and hit >= 260:
            out.append({"tf": "weekly", "i": hit, "w": w, "pat": pat, "from_date": wdates[pat["from"]], "pause_date": wdates[w]})
        elif hit is None and w == w_last and n - 1 >= 260:
            pend.append({"tf": "weekly", "i": n - 1, "w": w, "pat": pat, "from_date": wdates[pat["from"]], "pause_date": wdates[w]})
    out.sort(key=lambda t: (t["tf"], t["i"]))
    kept, last = [], {}
    for t in out:                                                                        # cooldown per timeframe, in date order
        if t["i"] - last.get(t["tf"], -10 ** 9) >= p.pause_cooldown:
            kept.append(t)
            last[t["tf"]] = t["i"]
    return kept, pend


def pause_geo(t: dict) -> dict:
    pat, wk = t["pat"], t["tf"] == "weekly"
    return {"tf": t["tf"], "from": t["from_date"].strftime("%Y-%m-%d"), "to": t["pause_date"].strftime("%Y-%m-%d"), "level": round(pat["low"], 2), "base": round(pat["high"], 2),
            "pause": pat["pause"], "run": int(pat["run"]), "candle_date": t["pause_date"].strftime("%Y-%m-%d"), "candle": pat["pause"]}


def pause_why(t: dict) -> str:
    pat, wk = t["pat"], t["tf"] == "weekly"
    unit = "week" if wk else "day"
    return (f"selling pressure: {pat['run']} red {unit}s in a row, each closing near its low and lower than the one before, down {pat['drop_pct']:.1f}% ({pat['drop_atr']:.1f} ATR); "
            f"then a {pat['pause'].lower()} ({unit} of {pat['low']:,.1f}-{pat['high']:,.1f}) - the pause - "
            f"short below its low {pat['low']:,.1f}, stop at its high {pat['high']:,.1f}")


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
                        triggers.append({"kind": "zone", "side": "long", "i": i, "w": w, "level": hi, **pl, "geo": geo, "f": {**_features(d, i), "impulse": z["impulse"]},
                                         "reason": f"{why}; in the zone from {dates[entered].strftime('%d %b')}, daily close {c:,.1f} back above it"})
                break
        if not done and entered is not None and end == n and w_last >= 40:        # in the zone right now
            entry = hi * (1 + buf)
            pl = plan(entry, lo * (1 - buf), wH, w_last, hi - lo, hi, p)
            if pl is not None:
                pending.append({"kind": "zone", "side": "long", "i": n - 1, "w": w_last, "level": hi, "entry": entry, **pl, "geo": geo, "f": {**_features(d, n - 1), "impulse": z["impulse"]},
                                "reason": f"{why}; price is in the zone since {dates[entered].strftime('%d %b')} - buy on a daily close back above {hi:,.1f}"})
    # --- reversal candles at a weekly zone: demand zones give longs, supply zones give shorts
    for side in ("long", "short"):
        zs = find_zones(df.set_axis(dates), ZoneParams(side=side, impulse_atr=p.zone_impulse_atr, expiry_weeks=p.zone_expiry_weeks))
        for t in candle_triggers(d, zs, dates, wk_of, wH, wL, side, p):
            i, long = t["i"], side == "long"
            c = d["c"][i]
            height = abs(t["zone"]["hi"] - t["zone"]["lo"])
            pl = plan(c, t["stop"], wH, t["w"], height, t["level"], p, side, wL)
            if pl is None:
                continue
            triggers.append({"kind": "candle", "side": side, "i": i, "w": t["w"], "level": t["level"], **pl, "geo": t["geo"], "f": {**_features(d, i), "impulse": t["zone"]["impulse"]},
                             "reason": t["why"]})
    # --- bearish continuation (shorts): a run of strong red candles, then a doji or a small green one; sell below its low, the stop is its high
    pz, pp = pause_scan(d, dates, wk_of, wdates, wk["open"].to_numpy(float), wH, wL, wC, p)
    for t in pz:
        i, pat = t["i"], t["pat"]
        w = t.get("w", int(wk_of[i]))
        pl = plan(d["c"][i], pat["high"], wH, w, pat["drop"], pat["low"], p, "short", wL)
        if pl is not None:
            triggers.append({"kind": "pause", "side": "short", "i": i, "w": w, "level": pat["low"], **pl, "geo": pause_geo(t), "f": _features(d, i),
                             "reason": pause_why(t) + f"; daily close {d['c'][i]:,.1f} below it"})
    for t in pp:
        pat, w = t["pat"], t.get("w", int(wk_of[n - 1]))
        entry = pat["low"] * (1 - buf)
        pl = plan(entry, pat["high"], wH, w, pat["drop"], pat["low"], p, "short", wL)
        if pl is not None:
            gap = (d["c"][n - 1] / pat["low"] - 1) * 100
            pending.append({"kind": "pause", "side": "short", "i": n - 1, "w": w, "level": pat["low"], "entry": entry, **pl, "geo": pause_geo(t), "f": _features(d, n - 1),
                            "reason": pause_why(t) + f"; the close is {gap:.1f}% above it" if gap > 0 else pause_why(t)})
    triggers.sort(key=lambda t: t["i"])
    return triggers, pending
