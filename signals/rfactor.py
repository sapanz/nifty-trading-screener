"""R-factor, and a backtest of it on the dashboard's Replay day files.

`snapshot_rows` is the Python twin of the page's attachR (docs/index.html):
for one day file and one 5-minute slot, every F&O stock with OI gets
  vol_vs_avg  - volume so far / its 20-day average full-day volume (2 dp)
  oi_chg      - OI added since the first slot of the day, % (2 dp)
  px_chg      - price move since that first slot, %
and three percentile ranks (ties averaged) among those stocks. R is the
geometric mean of the three ranks, 0 where OI has not built up. Keep the two
implementations in step: the page's numbers were checked against this one.

The backtest asks the plain question: *if you took the stocks with the highest R at time T, in the direction
of their buildup (long if price is up with OI, short if down), what did price do
over the next 30 minutes / 60 minutes / to the close?* - against two baselines
(every stock with OI building, and the top stocks by volume alone). Limits, said
plainly: the replay window is ~20 sessions (the futures contract's life), the
moments tested are correlated within a day, and fills are at the signal
candle's close with a flat cost - so read it as a sanity check, not proof.
"""
from __future__ import annotations

import math
from statistics import mean, pstdev

SESSION_START_MINUTES = 9 * 60 + 15
STEP = 5
LAST_SLOT = 74


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
        volume = sum(s["v"][: slot + 1])
        o = [x for x in s["o"][: slot + 1] if x is not None]
        p = [x for x in s["p"][: slot + 1] if x is not None]
        if len(o) < 2 or len(p) < 2 or not o[0] or not p[0]:
            continue
        oi_chg = _r2((o[-1] - o[0]) / o[0] * 100)
        px_chg = (p[-1] - p[0]) / p[0] * 100
        rows.append({
            "symbol": symbol, "vol_vs_avg": _r2(volume / s["av"]), "oi_chg": oi_chg, "px_chg": px_chg,
            "bias": ("long" if px_chg >= 0 else "short") if oi_chg > 0 else ("cover" if px_chg >= 0 else "unwind"),
            "price": s["c"][slot],
        })
    if len(rows) < min_stocks:
        return []
    pv = percentile_ranks([r["vol_vs_avg"] for r in rows])
    po = percentile_ranks([r["oi_chg"] for r in rows])
    pp = percentile_ranks([abs(r["px_chg"]) for r in rows])
    for r, a, b, c in zip(rows, pv, po, pp):
        r["r"] = round((a * b * c) ** (1 / 3)) if r["oi_chg"] > 0 else 0
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
    groups = ["Top by R-factor", "R >= 70", "R 40-69", "R < 40 (OI building)", "Every stock with OI building",
              "Top by volume only (direction = price since open)"]
    out = {g: {h: [] for h in HORIZONS} for g in groups}
    for day_index, day in enumerate(days):
        for hhmm in times:
            slot = slot_for(hhmm)
            rows = snapshot_rows(day, slot)
            if not rows:
                continue
            building = [r for r in rows if r["bias"] in ("long", "short")]
            by_r = sorted(building, key=lambda r: (r["r"], r["vol_vs_avg"]), reverse=True)
            by_vol = sorted(rows, key=lambda r: r["vol_vs_avg"], reverse=True)
            picks = {
                "Top by R-factor": by_r[:top_n],
                "R >= 70": [r for r in building if r["r"] >= 70],
                "R 40-69": [r for r in building if 40 <= r["r"] < 70],
                "R < 40 (OI building)": [r for r in building if r["r"] < 40],
                "Every stock with OI building": building,
                "Top by volume only (direction = price since open)": by_vol[:top_n],
            }
            for group, chosen in picks.items():
                for r in chosen:
                    direction = 1 if r["px_chg"] >= 0 else -1
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
