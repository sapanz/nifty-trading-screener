"""The intraday order-block test restricted to how the setup is actually traded:
entries before 11:00 only, and only in stocks whose R-factor is high at that moment.

R-factor (signals/rfactor.py) needs futures open interest, which the Replay day files
hold for only ~20 sessions. So the question is asked two ways:

  R-lite  (the whole 12 months): the same idea without OI - each stock is ranked among all F&O stocks
          on volume vs its 20-day average and on the size of the move since the first 5-minute candle;
          R-lite is the geometric mean of the two ranks. Direction = the move since open.
  full R  (the sessions that have OI): exactly the dashboard's R-factor and buildup direction, from the
          day files - a handful of sessions, so only an indication.

For both, R is measured with the candles COMPLETED before the entry bar (no look-ahead), and a trade is
"aligned" when it goes with the stock's direction (bullish block while the stock is up since open / long
buildup; bearish block while it is down / short buildup). Each group is compared with a baseline that
takes the SAME stocks on the SAME days, with the same direction and stop/target distances, but enters at a
random bar between 09:30 and 11:00 - so the difference is what waiting for the order-block retest adds.
"""
from __future__ import annotations

import random
from statistics import mean

import numpy as np
import pandas as pd

from signals import config, rfactor
from signals.intraday_ob_backtest import COST, IBTrade, _gross, _plain, _stats, _walk_to_exit, _clustered_t

SLOTS = 75
START = 9 * 60 + 15
CUTOFF_MINUTES = 105          # entries before 11:00 (minutes after 09:15)


def build_day_tables(intraday: dict[str, pd.DataFrame], avg_days: int = 20, min_history: int = 5) -> dict:
    """{date: {symbol: {"c": [close per slot], "v": [volume per slot], "av": 20-day avg full-day volume or None}}}"""
    tables: dict = {}
    for symbol, df in intraday.items():
        history: list[float] = []
        slots_all = ((df.index.hour * 60 + df.index.minute - START) // 5).to_numpy()
        for day, pos in df.groupby(df.index.normalize()).indices.items():
            closes: list[float | None] = [None] * SLOTS
            volumes = [0.0] * SLOTS
            for i in pos:
                slot = slots_all[i]
                if 0 <= slot < SLOTS and pd.notna(df["close"].iat[i]):
                    closes[slot] = float(df["close"].iat[i])
                    volumes[slot] += float(df["volume"].iat[i]) if pd.notna(df["volume"].iat[i]) else 0.0
            last = None
            for i in range(SLOTS):
                if closes[i] is None:
                    closes[i] = last
                else:
                    last = closes[i]
            window = history[-avg_days:]
            av = float(np.mean(window)) if len(window) >= min_history else None
            tables.setdefault(pd.Timestamp(day).normalize(), {})[symbol] = {"c": closes, "v": volumes, "av": av}
            history.append(sum(volumes))
    return tables


def _pct_rank(values: list[float]) -> np.ndarray:
    n = len(values)
    if n < 2:
        return np.full(n, 50.0)
    return (pd.Series(values).rank(method="average").to_numpy() - 1) / (n - 1) * 100


def lite_r(table: dict, slot: int, min_stocks: int = 30) -> dict[str, tuple[int, int]]:
    """{symbol: (R-lite 0-100, direction +1/-1)} using candles up to and including `slot`."""
    rows = []
    for symbol, s in table.items():
        if s["av"] is None or s["av"] <= 0 or s["c"][slot] is None or s["c"][0] is None:
            continue
        volume = sum(s["v"][: slot + 1])
        move = (s["c"][slot] / s["c"][0] - 1) * 100
        rows.append((symbol, volume / s["av"], move))
    if len(rows) < min_stocks:
        return {}
    pv, pp = _pct_rank([r[1] for r in rows]), _pct_rank([abs(r[2]) for r in rows])
    return {r[0]: (int(round(float(np.sqrt(a * b)))), 1 if r[2] >= 0 else -1) for r, a, b in zip(rows, pv, pp)}


def _signal_slot(trade: IBTrade) -> int:
    return trade.minutes_in // 5 - 1          # the last candle completed before the entry bar


def annotate(trades: list[IBTrade], tables: dict, oi_days: dict | None) -> list[dict]:
    """Entries before 11:00 with R-lite, and full R where the day has OI."""
    cache_lite: dict = {}
    cache_full: dict = {}
    out = []
    for t in trades:
        if t.minutes_in >= CUTOFF_MINUTES or t.minutes_in < 15 or t.day not in tables:
            continue
        slot = _signal_slot(t)
        key = (t.day, slot)
        if key not in cache_lite:
            cache_lite[key] = lite_r(tables[t.day], slot)
        r, direction = cache_lite[key].get(t.symbol, (None, None))
        if r is None:
            continue
        rec = {"trade": t, "r_lite": r, "aligned_lite": (t.direction == "bull") == (direction > 0), "r_full": None, "aligned_full": None}
        day_file = (oi_days or {}).get(t.day.strftime("%Y-%m-%d"))
        if day_file:
            if key not in cache_full:
                cache_full[key] = {row["symbol"]: row for row in rfactor.snapshot_rows(day_file, slot)}
            row = cache_full[key].get(t.symbol)
            if row:
                rec["r_full"] = row["r"]
                rec["aligned_full"] = (t.direction == "bull" and row["bias"] == "long") or (t.direction == "bear" and row["bias"] == "short")
        out.append(rec)
    return out


def morning_baseline(trades: list[IBTrade], arrays: dict, draws: int = 5, seed: int = 11) -> list[float]:
    """Same stock, same day, same direction and stop/target distances - entered at a random bar 09:30-10:55."""
    rng = random.Random(seed)
    out = []
    for t in trades:
        times, ids, info, o, h, l, c, _ = arrays[t.symbol]
        first, exit_idx = info[t.session]
        for _ in range(draws):
            bar = first + rng.randint(3, 20)
            if bar >= exit_idx:
                continue
            entry = float(o[bar])
            risk = entry * t.risk_pct / 100
            stop, target = (entry - risk, entry + config.OB_TARGET_R * risk) if t.direction == "bull" else (entry + risk, entry - config.OB_TARGET_R * risk)
            _, price, _ = _walk_to_exit(t.direction, o, h, l, c, bar, entry, stop, target, exit_idx)
            out.append(_gross(t.direction, entry, price) - COST)
    return out


def _group(label: str, recs: list[dict], arrays: dict) -> str:
    trades = [r["trade"] for r in recs]
    if not trades:
        return f"  {label:<44} n=0"
    base = morning_baseline(trades, arrays)
    return (f"  {label:<44} {_stats(trades)}  {_clustered_t(trades)}\n"
            f"  {'':<44} same stocks/days, random entry before 11:  {_plain(base)}")


def summarize_r(recs: list[dict], arrays: dict, oi_sessions: int) -> str:
    lines = ["", "<b>Morning only (entries 09:30-11:00) and high R-factor</b>",
             f"{len(recs)} order-block retests before 11:00 where R could be measured. 'Aligned' = with the stock's direction.",
             "", "R-lite (volume + move since open, no OI) - the whole period"]
    for label, pick in (
        ("Aligned, R-lite at least 85", lambda r: r["aligned_lite"] and r["r_lite"] >= 85),
        ("Aligned, R-lite at least 70", lambda r: r["aligned_lite"] and r["r_lite"] >= 70),
        ("Aligned, R-lite 40-69", lambda r: r["aligned_lite"] and 40 <= r["r_lite"] < 70),
        ("Aligned, R-lite under 40", lambda r: r["aligned_lite"] and r["r_lite"] < 40),
        ("Against direction, R-lite at least 70", lambda r: not r["aligned_lite"] and r["r_lite"] >= 70),
    ):
        lines.append(_group(label, [r for r in recs if pick(r)], arrays))
    day_cut = sorted({r["trade"].day for r in recs})
    if day_cut:
        cut = day_cut[int(len(day_cut) * 0.6) - 1]
        lines += ["", f"R-lite at least 70 and aligned, split at {cut.date()}"]
        core = [r for r in recs if r["aligned_lite"] and r["r_lite"] >= 70]
        lines.append(_group("In-sample", [r for r in core if r["trade"].day <= cut], arrays))
        lines.append(_group("Out-of-sample", [r for r in core if r["trade"].day > cut], arrays))
        lines += ["", "Bullish only, aligned, R-lite at least 70, by stop size"]
        for lo, hi in ((0, 0.3), (0.3, 0.5), (0.5, 3.1)):
            lines.append(_group(f"stop {lo:.1f}-{hi:.1f}%", [r for r in core if r["trade"].direction == "bull" and lo <= r["trade"].risk_pct < hi], arrays))
    full = [r for r in recs if r["r_full"] is not None]
    lines += ["", f"Full R-factor with open interest - only the {oi_sessions} sessions that have OI (an indication, not proof)",
              f"{len(full)} retests before 11:00 with a full R"]
    for label, pick in (
        ("Aligned (long/short buildup), R at least 70", lambda r: r["aligned_full"] and r["r_full"] >= 70),
        ("Aligned, R 40-69", lambda r: r["aligned_full"] and 40 <= r["r_full"] < 70),
        ("Aligned, R under 40", lambda r: r["aligned_full"] and r["r_full"] < 40),
        ("Not aligned, R at least 70", lambda r: not r["aligned_full"] and r["r_full"] >= 70),
    ):
        lines.append(_group(label, [r for r in full if pick(r)], arrays))
    return "\n".join(lines)
