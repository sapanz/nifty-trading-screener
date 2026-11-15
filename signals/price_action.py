"""Price-action setups: weekly structure, traded on the daily chart (long side).

Four kinds, all found on COMPLETED weekly candles and triggered by a DAILY close outside the structure:
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

KINDS = ("zone", "range", "triangle", "cup")
KIND_NAMES = {"zone": "Weekly support zone", "range": "Weekly range breakout", "triangle": "Triangle breakout", "cup": "Cup and handle"}


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


DETECTORS = {"range": range_at, "triangle": triangle_at, "cup": cup_at}


# ---------------------------------------------------------------- resistance and the plan
def next_resistance(wH: np.ndarray, w: int, above: float, p: Params) -> float | None:
    """The lowest weekly resistance above `above`, known at week w: confirmed swing highs of the last res_lookback_weeks and the last few weeks' high."""
    lo = max(0, w - p.res_lookback_weeks + 1)
    cands = [wH[i] for i in _pivots(wH, p.pivot_k, lo, w, True)]
    cands.append(wH[max(0, w - p.recent_weeks + 1):w + 1].max())
    higher = [c for c in cands if c > above]
    return float(min(higher)) if higher else None


def plan(ref: float, stop: float, wH: np.ndarray, w: int, height: float, level: float, p: Params) -> dict | None:
    """Stop / target / reward:risk for an entry at `ref`. None when the risk is outside the limits or nothing sensible is overhead."""
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
    hi252 = d["h"][max(0, i - 251):i + 1].max()
    return {"vol": float(vol), "above200": float(c[i] > sma200) if not np.isnan(sma200) else np.nan, "off_high": float(c[i] / hi252 - 1),
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
    last_trigger = {k: -10 ** 9 for k in KINDS}

    def geometry(kind: str, pat: dict) -> dict:
        g = {"from": wdates[pat["start"]].strftime("%Y-%m-%d"), "to": wdates[pat["end"]].strftime("%Y-%m-%d"), "level": round(pat["level"], 2), "base": round(pat["base"], 2)}
        if kind == "triangle":
            g["shape"] = pat["shape"]
            for key in ("upper", "lower"):
                x0, y0, x1, y1 = pat[key]
                g[key] = [wdates[x0].strftime("%Y-%m-%d"), round(y0, 2), wdates[min(x1, len(wdates) - 1)].strftime("%Y-%m-%d"), round(y1, 2)]
        if kind == "cup":
            g.update(left=wdates[pat["left"]].strftime("%Y-%m-%d"), bottom=wdates[pat["bottom"]].strftime("%Y-%m-%d"), right=wdates[pat["right"]].strftime("%Y-%m-%d"),
                     bottom_px=round(pat["bottom_px"], 2))
        return g

    # --- breakouts: the pattern as of the last completed week, a daily close above its level during the following week
    cache: dict[tuple[str, int], dict | None] = {}
    for i in range(260, n):
        w = int(wk_of[i])
        if w < 40:
            continue
        for kind, detect in DETECTORS.items():
            if (kind, w) not in cache:
                cache[(kind, w)] = detect(wH, wL, wC, w, p)
            pat = cache[(kind, w)]
            if pat is None or i - last_trigger[kind] < p.cooldown_days:
                continue
            first = int(np.searchsorted(dates.values, wdates.values[w], side="right"))    # first session after that week closed
            if d["c"][i] > pat["level"] and not (d["c"][first:i] > pat["level"]).any():
                stop = min(pat["level"], d["l"][i]) * (1 - buf)
                pl = plan(d["c"][i], stop, wH, w, pat["height"], pat["level"], p)
                last_trigger[kind] = i
                if pl is None:
                    continue
                triggers.append({"kind": kind, "i": i, "w": w, "level": pat["level"], **pl, "geo": geometry(kind, pat), "f": _features(d, i),
                                 "reason": f"{pat['why']}; daily close {d['c'][i]:,.1f} above {pat['level']:,.1f}"})
    w_last = int(wk_of[n - 1]) if n else -1
    if n and w_last >= 40:                                                        # armed now: pattern intact and the close just under its level
        first = int(np.searchsorted(dates.values, wdates.values[w_last], side="right"))
        for kind, detect in DETECTORS.items():
            pat = cache.get((kind, w_last)) or detect(wH, wL, wC, w_last, p)
            c = d["c"][n - 1]
            if pat is None or (d["c"][first:n] > pat["level"]).any() or not (pat["level"] * (1 - p.near_pct / 100) <= c <= pat["level"]):
                continue
            entry = pat["level"] * (1 + buf)
            pl = plan(entry, min(pat["level"], d["l"][n - 1]) * (1 - buf), wH, w_last, pat["height"], pat["level"], p)
            if pl is not None:
                pending.append({"kind": kind, "i": n - 1, "w": w_last, "level": pat["level"], "entry": entry, **pl, "geo": geometry(kind, pat), "f": _features(d, n - 1),
                                "reason": f"{pat['why']}; {((pat['level'] / c) - 1) * 100:.1f}% below the breakout level - buy on a daily close above {pat['level']:,.1f}"})

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
                if i >= 260 and w >= 40 and i - last_trigger["zone"] >= p.cooldown_days:
                    pl = plan(c, lo * (1 - buf), wH, w, hi - lo, hi, p)
                    last_trigger["zone"] = i
                    if pl is not None:
                        triggers.append({"kind": "zone", "i": i, "w": w, "level": hi, **pl, "geo": geo, "f": {**_features(d, i), "impulse": z["impulse"]},
                                         "reason": f"{why}; in the zone from {dates[entered].strftime('%d %b')}, daily close {c:,.1f} back above it"})
                break
        if not done and entered is not None and end == n and w_last >= 40:        # in the zone right now
            entry = hi * (1 + buf)
            pl = plan(entry, lo * (1 - buf), wH, w_last, hi - lo, hi, p)
            if pl is not None:
                pending.append({"kind": "zone", "i": n - 1, "w": w_last, "level": hi, "entry": entry, **pl, "geo": geo, "f": {**_features(d, n - 1), "impulse": z["impulse"]},
                                "reason": f"{why}; price is in the zone since {dates[entered].strftime('%d %b')} - buy on a daily close back above {hi:,.1f}"})
    triggers.sort(key=lambda t: t["i"])
    return triggers, pending
