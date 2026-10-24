"""Research: can a morning screen find the stocks a discretionary F&O option
buyer ends up trading?

The question comes from a public Sensibull "verified P&L" journal (one F&O
stock option per day, almost always closed the same session, ~91% winning
days). That journal records WHAT was traded (stock, call/put) but not WHEN or at
what price, so this module answers a narrower, testable question: at a fixed
time in the morning, using only data available by then, how does the stock the
trader ended up in rank among every F&O stock - and is a rules-based ranking
tradable on its own?

Per (stock, session, decision time) it builds features from 5-minute candles
(`session_features`), ranks stocks within each day (`add_scores`), and then:

  * `pick_signature` - where the journal's pick sat on each feature at the
    decision time, plus how much of its final move had already happened (the
    "was it visible, or only obvious in hindsight?" check);
  * `screen_test` - recall of the journal's picks by a simple, untuned top-K
    screen, and what that screen would have earned from the decision time to
    the square-off, split in-sample / out-of-sample.

Nothing here is fitted: the composite is an equal-weight sum of percentile
ranks, so the in-sample / out-of-sample split guards against my own choice of
features, not against optimisation.

Caveats (same spirit as signals/trendline_backtest.py): today's F&O list is
applied across the whole window; returns are on the underlying, not the option
(no premium, delta or theta); fills are assumed at the next bar's open; and the
journal has ~10% of trading days unshared, so "the picks" are a sample.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SLOTS = 75                                  # 5-minute bars 09:15 .. 15:25
SESSION_START_MIN = 9 * 60 + 15
MIN_SESSION_BARS = 60                       # skip half-days / special sessions
DECISION_TIMES = ("09:30", "10:00", "10:30", "11:30")   # bar END times: data up to here
SQUARE_OFF_BAR = "15:10"                    # exit at the close of the bar starting here (15:15)
MAX_GAP_PCT = 0.25                          # a bigger overnight gap is almost surely a split/bonus
ATR_DAYS = 14
VOL_DAYS = 20
ROUND_TRIP_COST = 0.001                     # 0.10%: intraday equity incl. a slippage allowance
OOS_START = pd.Timestamp("2026-01-01")
COLS = ("open", "high", "low", "close", "volume")


def slot_of(hhmm: str) -> int:
    """Index of the 5-minute bar that STARTS at hh:mm (09:15 -> 0)."""
    h, m = (int(x) for x in hhmm.split(":"))
    return (h * 60 + m - SESSION_START_MIN) // 5


def to_grid(bars: pd.DataFrame) -> tuple[pd.DatetimeIndex, dict[str, np.ndarray]]:
    """5-minute bars (index = IST bar-start time) -> {col: [sessions x 75]}.

    Sessions with fewer than MIN_SESSION_BARS bars are dropped; holes inside a
    kept session are filled (close carried forward, open/high/low = close,
    volume 0) so cumulative features stay defined.
    """
    idx = pd.DatetimeIndex(bars.index)
    minutes = np.asarray(idx.hour) * 60 + np.asarray(idx.minute) - SESSION_START_MIN
    slot = minutes // 5
    ok = (minutes >= 0) & (slot < SLOTS) & (minutes % 5 == 0)
    day = idx.normalize()[ok]
    days, pos = np.unique(day.to_numpy(), return_inverse=True)
    grids: dict[str, np.ndarray] = {}
    for col in COLS:
        g = np.full((len(days), SLOTS), np.nan)
        g[pos, slot[ok]] = bars[col].to_numpy(dtype=float)[ok]
        grids[col] = g
    keep = np.isfinite(grids["close"]).sum(axis=1) >= MIN_SESSION_BARS
    days = days[keep]
    grids = {k: v[keep] for k, v in grids.items()}
    close = pd.DataFrame(grids["close"]).ffill(axis=1).bfill(axis=1).to_numpy()
    for col in ("open", "high", "low"):
        grids[col] = np.where(np.isfinite(grids[col]), grids[col], close)
    grids["close"] = close
    grids["volume"] = np.nan_to_num(grids["volume"])
    return pd.DatetimeIndex(days), grids


def session_features(symbol: str, bars: pd.DataFrame, decision_times=DECISION_TIMES) -> pd.DataFrame:
    """One row per (session, decision time) for one stock. Features use only
    bars that have completed by the decision time; `fwd_ret` and the `day_*` /
    `mfe`/`mae` columns are OUTCOMES (never inputs to a screen)."""
    days, g = to_grid(bars)
    if len(days) < ATR_DAYS + 2:
        return pd.DataFrame()
    o, h, l, c, v = (g[k] for k in COLS)
    day_open, day_close = o[:, 0], c[:, -1]
    day_high, day_low = h.max(axis=1), l.min(axis=1)

    gap_days = np.r_[np.nan, np.diff(days.to_numpy().astype("datetime64[D]").astype(np.int64))]
    contiguous = gap_days <= 6
    prev_close = np.r_[np.nan, day_close[:-1]]
    prev_high = np.r_[np.nan, day_high[:-1]]
    prev_low = np.r_[np.nan, day_low[:-1]]
    for arr in (prev_close, prev_high, prev_low):
        arr[~contiguous] = np.nan

    range_pct = (day_high - day_low) / prev_close
    atr_pct = pd.Series(range_pct).rolling(ATR_DAYS, min_periods=10).mean().shift(1).to_numpy()
    sma20 = pd.Series(day_close).rolling(20, min_periods=15).mean().shift(1).to_numpy()
    sma50 = pd.Series(day_close).rolling(50, min_periods=40).mean().shift(1).to_numpy()
    cumvol = np.cumsum(v, axis=1)
    base_vol = pd.DataFrame(cumvol).rolling(VOL_DAYS, min_periods=15).mean().shift(1).to_numpy()

    sq = slot_of(SQUARE_OFF_BAR)
    frames = []
    for t in decision_times:
        nb = slot_of(t)              # bars [0, nb) are complete; the bar at slot nb is the entry bar
        price = c[:, nb - 1]
        hi, lo = h[:, :nb].max(axis=1), l[:, :nb].min(axis=1)
        entry, exit_ = o[:, nb], c[:, sq]
        with np.errstate(divide="ignore", invalid="ignore"):
            frames.append(pd.DataFrame({
                "date": days, "symbol": symbol, "t": t,
                "atr_pct": atr_pct,
                "gap": day_open / prev_close - 1,
                "ret_pc": price / prev_close - 1,
                "ret_atr": (price / prev_close - 1) / atr_pct,
                "range_atr": ((hi - lo) / prev_close) / atr_pct,
                "relvol": np.where(base_vol[:, nb - 1] > 0, cumvol[:, nb - 1] / base_vol[:, nb - 1], np.nan),
                "pos": np.where(hi > lo, (price - lo) / (hi - lo), 0.5),
                "pdh_break": price > prev_high,
                "pdl_break": price < prev_low,
                "dist20": prev_close / sma20 - 1,
                "dist50": prev_close / sma50 - 1,
                # outcomes
                "fwd_ret": exit_ / entry - 1,
                "mfe": h[:, nb:sq + 1].max(axis=1) / entry - 1,
                "mae": l[:, nb:sq + 1].min(axis=1) / entry - 1,
                "day_ret": day_close / prev_close - 1,
            }))
    out = pd.concat(frames, ignore_index=True)
    out = out[np.isfinite(out["atr_pct"]) & np.isfinite(out["ret_pc"]) & np.isfinite(out["fwd_ret"])]
    out = out[out["gap"].abs() < MAX_GAP_PCT]
    return out.reset_index(drop=True)


def add_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional (per day and decision time) percentile ranks and the
    equal-weight composite scores. No fitted parameters."""
    df = df.dropna(subset=["ret_atr", "range_atr", "relvol"]).copy()
    g = df.groupby(["date", "t"], sort=False)
    df["mkt"] = g["ret_pc"].transform("median")          # "the market" = median F&O stock that day
    df["r_ret"] = g["ret_atr"].rank(pct=True)
    df["r_vol"] = g["relvol"].rank(pct=True)
    df["r_rng"] = g["range_atr"].rank(pct=True)
    df["long_score"] = df["r_ret"] + df["r_vol"] + df["r_rng"]
    df["short_score"] = (1 - df["r_ret"]) + df["r_vol"] + df["r_rng"]
    # the day's own outcome rank in each direction (hindsight yardstick)
    df["r_day"] = df.groupby(["date", "t"], sort=False)["day_ret"].rank(pct=True)
    return df


def attach_picks(df: pd.DataFrame, picks: pd.DataFrame) -> pd.DataFrame:
    """Left-join the journal (date, symbol, side) onto the feature table."""
    p = picks.copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p.drop_duplicates(["date", "symbol"])[["date", "symbol", "side"]]
    out = df.merge(p, on=["date", "symbol"], how="left")
    out["dir"] = out["side"].map({"Call": 1.0, "Put": -1.0})
    return out


def _fmt(x: float, pct: bool = False, nd: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x * 100:.{nd}f}%" if pct else f"{x:.{nd}f}"


def pick_signature(df: pd.DataFrame) -> str:
    """Where the journal's picks sit on each morning feature (percentile
    within that day's F&O cross-section; 0.50 = indistinguishable from a random
    stock) and how visible the move already was."""
    lines = []
    for t in sorted(df["t"].unique()):
        d = df[df["t"] == t]
        p = d[d["dir"].notna()].copy()
        if p.empty:
            continue
        up = p["dir"] > 0
        strength = np.where(up, p["r_ret"], 1 - p["r_ret"])          # percentile of the move in the pick's own direction
        day_pct = np.where(up, p["r_day"], 1 - p["r_day"])           # the same, for the whole day's close-to-close move
        aligned_break = np.where(up, p["pdh_break"], p["pdl_break"])
        # base rates over the same days: how often ANY stock is already through yesterday's high / low
        days = p["date"].unique()
        base = d[d["date"].isin(days)]
        base_break = np.mean(np.r_[base["pdh_break"].to_numpy(), base["pdl_break"].to_numpy()])
        # forward return from the decision time, in the pick's direction, vs. the average stock on those days
        fwd = p["dir"] * p["fwd_ret"]
        base_fwd = p.merge(base.groupby("date")["fwd_ret"].mean().rename("mkt_fwd"), on="date")
        excess = fwd.to_numpy() - (base_fwd["dir"] * base_fwd["mkt_fwd"]).to_numpy()
        big = p["day_ret"].abs() > 0.01
        done = (p["ret_pc"] / p["day_ret"])[big].clip(0, 1.5)
        lines.append(
            f"[{t}] n={len(p)}\n"
            f"  directional move vs peers : median pctile {_fmt(np.median(strength))}  | top decile {_fmt((strength >= 0.9).mean(), True, 0)} | top 3% {_fmt((strength >= 0.97).mean(), True, 0)}\n"
            f"  relative volume vs peers  : median pctile {_fmt(p['r_vol'].median())}  | top decile {_fmt((p['r_vol'] >= 0.9).mean(), True, 0)}\n"
            f"  range expansion vs peers  : median pctile {_fmt(p['r_rng'].median())}  | top decile {_fmt((p['r_rng'] >= 0.9).mean(), True, 0)}\n"
            f"  already through prior day's high/low in its direction: {_fmt(float(np.mean(aligned_break)), True, 0)}  (any stock: {_fmt(float(base_break), True, 0)})\n"
            f"  hindsight check: pick's full-day move ranks pctile {_fmt(np.median(day_pct))} (top decile {_fmt((day_pct >= 0.9).mean(), True, 0)}); "
            f"median share of the final move already done by {t}: {_fmt(done.median(), True, 0)}\n"
            f"  {t} -> 15:15 in pick direction: mean {_fmt(fwd.mean(), True)} win {_fmt((fwd > 0).mean(), True, 0)} "
            f"| excess over avg stock {_fmt(float(np.mean(excess)), True)}"
        )
    return "\n".join(lines)


def _top_k(day: pd.DataFrame, k: int) -> pd.DataFrame:
    """Top-k candidates for one (date, t): each stock enters in its better
    direction (long if long_score >= short_score)."""
    long_better = day["long_score"] >= day["short_score"]
    cand = day.assign(score=np.where(long_better, day["long_score"], day["short_score"]),
                      d=np.where(long_better, 1.0, -1.0))
    return cand.nlargest(k, "score")


def screen_test(df: pd.DataFrame, ks=(1, 3, 5)) -> str:
    """Untuned top-K screen: (a) how often it contains the journal's pick in
    the journal's direction, (b) what the screen's picks earned from the
    decision time to 15:15 on the underlying, net of cost."""
    lines = []
    for t in sorted(df["t"].unique()):
        d = df[df["t"] == t]
        n_universe = d.groupby("date").size().median()
        for k in ks:
            picks_rows = [_top_k(day, k) for _, day in d.groupby("date", sort=False)]
            top = pd.concat(picks_rows)
            top["net"] = top["d"] * top["fwd_ret"] - ROUND_TRIP_COST
            # recall on journal days
            jd = d[d["dir"].notna()]
            hit_sym = hit_dir = 0
            for (_, row) in jd.iterrows():
                m = top[(top["date"] == row["date"]) & (top["symbol"] == row["symbol"])]
                hit_sym += len(m) > 0
                hit_dir += bool(len(m) and (m["d"] == row["dir"]).any())
            parts = [f"[{t}] top-{k} of ~{n_universe:.0f} stocks/day (random recall {_fmt(k / n_universe, True, 1)})",
                     f"recall of journal pick: symbol {_fmt(hit_sym / max(len(jd), 1), True, 1)}, symbol+direction {_fmt(hit_dir / max(len(jd), 1), True, 1)} (n={len(jd)})"]
            for label, sub in (("all", top), ("in-sample", top[top["date"] < OOS_START]), ("out-of-sample", top[top["date"] >= OOS_START])):
                if sub.empty:
                    continue
                m, s, n = sub["net"].mean(), sub["net"].std(), len(sub)
                parts.append(f"{label}: n={n} mean {_fmt(m, True)} win {_fmt((sub['net'] > 0).mean(), True, 0)} t={_fmt(m / (s / np.sqrt(n)) if s > 0 else np.nan)}")
            lines.append("  " + " | ".join(parts))
    return "\n".join(lines)


def coverage(picks: pd.DataFrame, df: pd.DataFrame) -> str:
    p = picks.copy()
    p["date"] = pd.to_datetime(p["date"])
    have = df[["date", "symbol"]].drop_duplicates()
    m = p.merge(have, on=["date", "symbol"], how="left", indicator=True)
    miss = m[m["_merge"] == "left_only"]
    return (f"journal picks: {len(p)}; with morning data: {len(p) - len(miss)} ({(len(p) - len(miss)) / max(len(p), 1):.0%}); "
            f"missing symbols: {sorted(miss['symbol'].unique())[:25]}")
