"""R-factor, and a backtest of it on the dashboard's Replay day files.

`snapshot_rows` is the Python twin of the page's attachR (docs/index.html).
R asks whether fresh positioning is building *right now*: for one day file and
one 5-minute slot t, every F&O stock with OI gets, over the last WINDOW candles
(30 min):
  oi_w        - open interest added, %
  px_w        - price move, %
  persist     - share of those candles in which OI rose
  vol_vs_avg  - volume so far / its 20-day average full-day volume (2 dp)
and four percentile ranks (ties averaged) among those stocks. R is the
geometric mean of the four ranks - but 0 where OI did not grow over the window,
or where OI fell over the last EXIT_CANDLES candles (positions being closed:
`exiting`, flagged rather than ranked). An earlier version used OI change
since the open and so could score a stock 90+ while its OI was already
falling; this one follows the recent OI trajectory. Keep this and the page's
attachR in step.

The backtest asks the plain question: *if you took the stocks with the highest R at time T, in the direction
of their buildup (long if price is up, short if down), what did price do
over the next 30 minutes / 60 minutes / to the close?* - against baselines
(every stock with OI building; the same split by the exit flag; the top stocks
by volume alone). Limits, said plainly: the replay window is ~20 sessions (the
futures contract's life), the moments tested are correlated within a day, and
fills are at the signal candle's close with a flat cost - a sanity check, not proof.
"""
from __future__ import annotations

import math
from statistics import mean, pstdev

SESSION_START_MINUTES = 9 * 60 + 15
STEP = 5
LAST_SLOT = 74
WINDOW = 6
EXIT_CANDLES = 3


def slot_for(hhmm: str) -> int:
    """Slot whose candle ENDS at hh:mm (the page's time list)."""
    h, m = hhmm.split(":")
    return (int(h) * 60 + int(m) - SESSION_START_MINUTES) // STEP - 1


def _r2(x: float) -> float:
    return round(x * 100) / 100


def percentile_ranks(values: list[float]) -> list[float]:
    n = len(values)
    if n < 2:
        return [50.0] * n
    ordered = sorted(values)
    out = []
    for v in values:
        below = sum(1 for x in ordered if x < v)
        equal = sum(1 for x in ordered if x == v)
        out.append((below + (equal - 1) / 2) / (n - 1) * 100)
    return out


def snapshot_rows(day: dict, slot: int, min_stocks: int = 10) -> list[dict]:
    rows = []
    for symbol, s in day["stocks"].items():
        if not s.get("fo") or not s.get("o") or not s.get("av") or s["c"][slot] is None:
            continue
        w = min(WINDOW, slot - 1)
        if w < EXIT_CANDLES:
            continue
        o, p = s["o"], s["p"]
        first = next((x for x in o[: slot + 1] if x is not None), None)
        o_t, o_w, p_t, p_w = o[slot], o[slot - w], p[slot], p[slot - w]
        if not first or not o_t or not o_w or not p_t or not p_w:
            continue
        window = o[slot - w: slot + 1]
        if any(x is None for x in window):
            continue
        up = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
        o_exit = o[slot - EXIT_CANDLES]
        oi_w = _r2((o_t - o_w) / o_w * 100)
        px_w = (p_t - p_w) / p_w * 100
        rows.append({
            "symbol": symbol, "vol_vs_avg": _r2(sum(s["v"][: slot + 1]) / s["av"]),
            "oi_chg": _r2((o_t - first) / first * 100), "oi_w": oi_w, "px_w": px_w, "persist": up / w,
            "px_open": (p_t - next(x for x in p[: slot + 1] if x is not None)) / next(x for x in p[: slot + 1] if x is not None) * 100,
            "exiting": o_exit is not None and o_t - o_exit <= 0,
            "bias": ("long" if px_w >= 0 else "short") if oi_w > 0 else ("cover" if px_w >= 0 else "unwind"),
            "price": s["c"][slot],
        })
    if len(rows) < min_stocks:
        return []
    pv = percentile_ranks([r["vol_vs_avg"] for r in rows])
    po = percentile_ranks([r["oi_w"] for r in rows])
    pp = percentile_ranks([abs(r["px_w"]) for r in rows])
    pq = percentile_ranks([r["persist"] for r in rows])
    for r, a, b, c, d in zip(rows, pv, po, pp, pq):
        r["r"] = round((a * b * c * d) ** 0.25) if r["oi_w"] > 0 and not r["exiting"] else 0
    return rows


def forward_return(day: dict, symbol: str, slot: int, horizon_slots: int | None, direction: int) -> float | None:
    """Direction-adjusted % move of the stock's close from `slot` to `slot + horizon` (None = to the close)."""
    closes = day["stocks"][symbol]["c"]
    exit_slot = LAST_SLOT if horizon_slots is None else slot + horizon_slots
    if exit_slot > LAST_SLOT or closes[slot] is None or closes[exit_slot] is None:
        return None
    return direction * (closes[exit_slot] / closes[slot] - 1) * 100


HORIZONS = {"next 30 min": 6, "next 60 min": 12, "to the close": None}


def run_backtest(days: list[dict], times: list[str], top_n: int = 5, cost_pct: float = 0.10) -> dict:
    """{group: {horizon: [(day index, net directional return %), ...]}}"""
    groups = ["Top by R-factor", "R >= 70", "R 40-69", "R < 40 (OI building, not exiting)",
              "Every stock with OI building", "  ... of which still building (not exiting)",
              "  ... of which exiting (R = 0, flagged)", "Top by volume only (direction = price since open)"]
    out = {g: {h: [] for h in HORIZONS} for g in groups}
    for day_index, day in enumerate(days):
        for hhmm in times:
            slot = slot_for(hhmm)
            rows = snapshot_rows(day, slot)
            if not rows:
                continue
            building = [r for r in rows if r["bias"] in ("long", "short")]
            ranked = [r for r in building if not r["exiting"]]
            by_r = sorted(ranked, key=lambda r: (r["r"], r["vol_vs_avg"]), reverse=True)
            by_vol = sorted(rows, key=lambda r: r["vol_vs_avg"], reverse=True)
            picks = {
                "Top by R-factor": by_r[:top_n],
                "R >= 70": [r for r in ranked if r["r"] >= 70],
                "R 40-69": [r for r in ranked if 40 <= r["r"] < 70],
                "R < 40 (OI building, not exiting)": [r for r in ranked if r["r"] < 40],
                "Every stock with OI building": building,
                "  ... of which still building (not exiting)": ranked,
                "  ... of which exiting (R = 0, flagged)": [r for r in building if r["exiting"]],
                "Top by volume only (direction = price since open)": by_vol[:top_n],
            }
            for group, chosen in picks.items():
                for r in chosen:
                    move = r["px_open"] if group.startswith("Top by volume only") else r["px_w"]
                    direction = 1 if move >= 0 else -1
                    for horizon, hs in HORIZONS.items():
                        ret = forward_return(day, r["symbol"], slot, hs, direction)
                        if ret is not None:
                            out[group][horizon].append((day_index, ret - cost_pct))
    return out


def _t(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    sd = pstdev(values)
    return mean(values) / (sd / math.sqrt(n)) if sd > 0 else 0.0


def _stats(records: list[tuple[int, float]]) -> str:
    """Per-trade stats plus a day-clustered t: average each session first, then
    t-test the session averages - the honest one, since a day's picks move together."""
    values = [v for _, v in records]
    n = len(values)
    if n < 2:
        return f"n={n}"
    per_day: dict[int, list[float]] = {}
    for d, v in records:
        per_day.setdefault(d, []).append(v)
    day_means = [mean(v) for v in per_day.values()]
    return (f"n={n:<5} avg {mean(values):+.3f}%  win {sum(v > 0 for v in values) / n * 100:3.0f}%  "
            f"t(day-clustered)={_t(day_means):+.1f} over {len(day_means)} days")


def summarize(results: dict, days: int, times: list[str], top_n: int, cost_pct: float) -> str:
    lines = [
        f"R-factor backtest: {days} sessions x {len(times)} moments ({', '.join(times)}), top {top_n}, "
        f"entry at the signal candle's close, net of {cost_pct}% round-trip cost. "
        "Returns are in the direction of the buildup (long if price up with OI up, short if down).",
    ]
    for horizon in HORIZONS:
        lines += ["", f"<b>{horizon}</b>"]
        for group, by_h in results.items():
            lines.append(f"  {group:<52} {_stats(by_h[horizon])}")
    lines += ["", "Day-clustered t treats each session as one observation; with ~20 sessions, |t| below ~2 is noise, and with this many comparisons even 2-3 can be chance."]
    return "\n".join(lines)
