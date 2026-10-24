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
(`session_features`: move vs ATR, relative volume, range expansion, VWAP side,
recent-volume surge, opening-range breakout, momentum), adds sector context
(`add_sector`) and - from signals/nse_fo.py - the PRIOR day's futures OI build-up
and put/call OI ratio, ranks stocks within each day (`add_scores`), and then:

  * `pick_signature` / `pick_context` - where the journal's pick sat on each
    feature at the decision time, plus how much of its final move had already
    happened (the "was it visible, or only obvious in hindsight?" check);
  * `screen_test` - recall of the journal's picks by simple, untuned top-K
    screens (one per entry of VARIANTS), and what each screen would have earned
    from the decision time to the square-off, split in-sample / out-of-sample.

Nothing here is fitted: every composite is an equal-weight sum of percentile
ranks fixed in advance, so the in-sample / out-of-sample split guards against my
own choice of features, not against optimisation. All variants are printed, not
just the best one.

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
OPENING_RANGE_BARS = 3                      # first 15 minutes
RECENT_BARS = 3                             # "recent" volume = the last 15 minutes
MOMENTUM_BARS = 6                           # short-term momentum = last 30 minutes
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
    cum_pv = np.cumsum((h + l + c) / 3 * v, axis=1)         # for the session VWAP
    or_hi, or_lo = h[:, :OPENING_RANGE_BARS].max(axis=1), l[:, :OPENING_RANGE_BARS].min(axis=1)

    sq = slot_of(SQUARE_OFF_BAR)
    frames = []
    for t in decision_times:
        nb = slot_of(t)              # bars [0, nb) are complete; the bar at slot nb is the entry bar
        price = c[:, nb - 1]
        hi, lo = h[:, :nb].max(axis=1), l[:, :nb].min(axis=1)
        entry, exit_ = o[:, nb], c[:, sq]
        # volume of the last RECENT_BARS completed bars vs the same bars on prior sessions: is it speeding up?
        a = max(0, nb - RECENT_BARS)
        recent = v[:, a:nb].sum(axis=1)
        base_recent = pd.Series(recent).rolling(VOL_DAYS, min_periods=15).mean().shift(1).to_numpy()
        ref = c[:, nb - 1 - MOMENTUM_BARS] if nb - 1 - MOMENTUM_BARS >= 0 else prev_close
        with np.errstate(divide="ignore", invalid="ignore"):
            vwap = np.where(cumvol[:, nb - 1] > 0, cum_pv[:, nb - 1] / cumvol[:, nb - 1], np.nan)
            outside_or = np.where(price > or_hi, price - or_hi, np.where(price < or_lo, price - or_lo, 0.0))
            frames.append(pd.DataFrame({
                "date": days, "symbol": symbol, "t": t,
                "atr_pct": atr_pct,
                "gap": day_open / prev_close - 1,
                "gap_atr": (day_open / prev_close - 1) / atr_pct,
                "ret_pc": price / prev_close - 1,
                "ret_atr": (price / prev_close - 1) / atr_pct,
                "mom_atr": (price / ref - 1) / atr_pct,
                "range_atr": ((hi - lo) / prev_close) / atr_pct,
                "relvol": np.where(base_vol[:, nb - 1] > 0, cumvol[:, nb - 1] / base_vol[:, nb - 1], np.nan),
                "relvol_recent": np.where(base_recent > 0, recent / base_recent, np.nan),
                "turnover_cr": cumvol[:, nb - 1] * price / 1e7,
                "vwap_atr": (price / vwap - 1) / atr_pct,
                "orb_atr": outside_or / prev_close / atr_pct,
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


MIN_SECTOR_PEERS = 4        # a sector needs this many OTHER F&O stocks that day, else its signal is neutral

# (feature column, rank column, is the feature directional?). Directional ranks are used as-is for a long and
# flipped (1 - r) for a short; the rest (volume, range) count the same either way.
RANKS = (
    ("ret_atr", "r_ret", True), ("relvol", "r_vol", False), ("range_atr", "r_rng", False),
    ("relvol_recent", "r_volrecent", False), ("vwap_atr", "r_vwap", True), ("orb_atr", "r_orb", True),
    ("mom_atr", "r_mom", True), ("sec_ret_atr", "r_sec", True), ("oi_dir", "r_oi", True),
)
DIRECTIONAL = {rank for _, rank, directional in RANKS if directional}
BASE = ("r_ret", "r_vol", "r_rng")
FLOW = ("r_volrecent", "r_vwap", "r_orb")
# Equal-weight sums of percentile ranks, fixed in advance (nothing is fitted). Reported side by side so the
# number of variants tried is visible - more variants means more chances for one to look good by luck.
VARIANTS = {
    "base": BASE,
    "volume/flow": BASE + FLOW,
    "sector": BASE + ("r_sec",),
    "oi": BASE + ("r_oi",),
    "all": BASE + FLOW + ("r_sec", "r_oi"),
}


def add_sector(df: pd.DataFrame, sector_map: dict[str, str]) -> pd.DataFrame:
    """Sector context per (session, decision time): the average move (in ATR
    units) and the share of stocks up among the stock's sector PEERS - the
    stock itself is left out, so a stock leading its sector does not inflate the
    sector's own signal. Sectors with fewer than MIN_SECTOR_PEERS other stocks
    that day get NaN (neutral in the scores)."""
    df = df.copy()
    df["sector"] = df["symbol"].map(sector_map)
    keys = [df["date"], df["t"], df["sector"]]
    n = df.groupby(keys, sort=False)["ret_atr"].transform("count")
    total = df.groupby(keys, sort=False)["ret_atr"].transform("sum")
    up = (df["ret_pc"] > 0).astype(float)
    up_total = up.groupby(keys, sort=False).transform("sum")
    peers = n - 1
    ok = peers >= MIN_SECTOR_PEERS
    safe = peers.where(peers > 0)
    df["sec_ret_atr"] = np.where(ok, (total - df["ret_atr"]) / safe, np.nan)
    df["sec_breadth"] = np.where(ok, (up_total - up) / safe, np.nan)
    df["rel_sec"] = df["ret_atr"] - df["sec_ret_atr"]
    return df


def add_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional (per day and decision time) percentile ranks of every
    feature and the equal-weight composite scores of each VARIANT. A feature a
    stock lacks (no sector peers, no OI row) ranks a neutral 0.5 rather than
    being penalised. No fitted parameters."""
    df = df.dropna(subset=["ret_atr", "range_atr", "relvol"]).copy()
    g = df.groupby(["date", "t"], sort=False)
    df["mkt"] = g["ret_pc"].transform("median")          # "the market" = median F&O stock that day
    for col, rank, _ in RANKS:
        df[rank] = (g[col].rank(pct=True) if col in df.columns else 0.5)
        df[rank] = df[rank].fillna(0.5)
    for name, ranks in VARIANTS.items():
        df[f"long_{name}"] = sum(df[r] for r in ranks)
        df[f"short_{name}"] = sum((1 - df[r]) if r in DIRECTIONAL else df[r] for r in ranks)
    df["long_score"], df["short_score"] = df["long_base"], df["short_base"]
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


def _rate(p: pd.DataFrame, base: pd.DataFrame, call_mask, put_mask) -> tuple[float, float]:
    """Share of the picks meeting their direction's condition, and the share
    of ALL stocks on the same days meeting it, weighted by the picks' own
    call / put mix so the two numbers are comparable."""
    call_share = float((p["dir"] > 0).mean())
    picks = float(np.where(p["dir"] > 0, call_mask(p), put_mask(p)).mean())
    peers = call_share * float(call_mask(base).mean()) + (1 - call_share) * float(put_mask(base).mean())
    return picks, peers


def pick_context(df: pd.DataFrame) -> str:
    """Where the journal's picks sit on the volume-flow, sector and prior-day
    OI features, in their own direction, against all stocks on the same days."""
    lines = []
    has_oi = "buildup" in df.columns and df["buildup"].notna().any()
    for t in sorted(df["t"].unique()):
        d = df[df["t"] == t]
        p = d[d["dir"].notna()]
        if p.empty:
            continue
        base = d[d["date"].isin(p["date"].unique())]
        own = lambda col: np.where(p["dir"] > 0, p[col], 1 - p[col])      # percentile in the pick's own direction
        row = [f"[{t}] n={len(p)}  (pick | any stock, same call/put mix)"]
        pk, bs = _rate(p, base, lambda f: f["vwap_atr"] > 0, lambda f: f["vwap_atr"] < 0)
        row.append(f"  trading on the right side of VWAP        : {pk:.0%} | {bs:.0%}")
        pk, bs = _rate(p, base, lambda f: f["orb_atr"] > 0, lambda f: f["orb_atr"] < 0)
        row.append(f"  outside the opening range, its direction : {pk:.0%} | {bs:.0%}")
        row.append(f"  volume in the last 15 min vs normal      : median pctile {np.median(p['r_volrecent']):.2f}, top decile {(p['r_volrecent'] >= 0.9).mean():.0%}")
        row.append(f"  short-term (30 min) momentum, its direction: median pctile {np.median(own('r_mom')):.2f}")
        if "sec_ret_atr" in p.columns and p["sec_ret_atr"].notna().any():
            sec = p[p["sec_ret_atr"].notna()]
            pk, bs = _rate(sec, base[base["sec_ret_atr"].notna()], lambda f: f["sec_ret_atr"] > 0, lambda f: f["sec_ret_atr"] < 0)
            lead_pk, lead_bs = _rate(sec, base[base["rel_sec"].notna()], lambda f: f["rel_sec"] > 0, lambda f: f["rel_sec"] < 0)
            row.append(f"  its sector moving the same way           : {pk:.0%} | {bs:.0%}   (sector move pctile, its direction: median {np.median(np.where(sec['dir'] > 0, sec['r_sec'], 1 - sec['r_sec'])):.2f})")
            row.append(f"  stock leading its own sector             : {lead_pk:.0%} | {lead_bs:.0%}")
        if has_oi:
            o = p[p["buildup"].notna()]
            b = base[base["buildup"].notna()]
            if len(o):
                pk, bs = _rate(o, b, lambda f: f["buildup"] == 1, lambda f: f["buildup"] == -1)
                row.append(f"  PRIOR-DAY build-up in its direction      : {pk:.0%} | {bs:.0%}   (long build-up for calls, short build-up for puts)")
                pk, bs = _rate(o, b, lambda f: f["buildup"] == 2, lambda f: f["buildup"] == -2)
                row.append(f"  PRIOR-DAY short covering / long unwinding: {pk:.0%} | {bs:.0%}   (move into the pick, on shrinking OI)")
                pk, bs = _rate(o, b, lambda f: f["buildup"] == -1, lambda f: f["buildup"] == 1)
                row.append(f"  PRIOR-DAY build-up AGAINST its direction : {pk:.0%} | {bs:.0%}")
                row.append(f"  prior-day OI build-up strength, its direction: median pctile {np.median(np.where(o['dir'] > 0, o['r_oi'], 1 - o['r_oi'])):.2f}; "
                           f"put/call OI ratio: picks median {o['pcr'].median():.2f} vs any stock {b['pcr'].median():.2f}")
        lines.append("\n".join(row))
    return "\n".join(lines)


def _select(d: pd.DataFrame, variant: str, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Top-k stocks per day for a score variant; each stock enters in its
    better direction (long if its long score >= its short score)."""
    long_s, short_s = d[f"long_{variant}"].to_numpy(), d[f"short_{variant}"].to_numpy()
    long_better = long_s >= short_s
    score = pd.Series(np.where(long_better, long_s, short_s), index=d.index)
    rank = score.groupby(d["date"]).rank(ascending=False, method="first")
    return (rank <= k).to_numpy(), np.where(long_better, 1.0, -1.0)


def screen_test(df: pd.DataFrame, ks=(1, 3, 5), variants=None) -> str:
    """Untuned top-K screens, one per score variant: (a) how often the screen
    contains the journal's pick (symbol / symbol+direction), (b) what the
    screen's own picks earned from the decision time to 15:15 on the
    underlying, net of cost, in-sample (before 2026) and out-of-sample.
    t-stats pool picks across stocks, so they are optimistic when k > 1."""
    variants = list(variants or VARIANTS)
    lines = []
    for t in sorted(df["t"].unique()):
        d = df[df["t"] == t].reset_index(drop=True)
        n_universe = d.groupby("date").size().median()
        is_pick = d["dir"].notna().to_numpy()
        n_picks = max(int(is_pick.sum()), 1)
        oos = (d["date"] >= OOS_START).to_numpy()
        lines.append(f"[{t}] ~{n_universe:.0f} stocks/day, {int(is_pick.sum())} journal picks   (recall = symbol / symbol+direction)")
        for name in variants:
            for k in ks:
                sel, dirn = _select(d, name, k)
                net = dirn * d["fwd_ret"].to_numpy() - ROUND_TRIP_COST
                parts = [f"  {name:<12} top-{k}: recall {(sel & is_pick).sum() / n_picks:5.1%} / {(sel & is_pick & (dirn == d['dir'].to_numpy())).sum() / n_picks:5.1%} (random {k / n_universe:.1%})"]
                for label, m in (("all", sel), ("IS", sel & ~oos), ("OOS", sel & oos)):
                    x = net[m]
                    if len(x) == 0:
                        continue
                    t_stat = f" t={x.mean() / (x.std() / np.sqrt(len(x))):+.1f}" if len(x) > 1 and x.std() > 0 else ""
                    parts.append(f"{label} n={len(x)} {x.mean():+.2%}{t_stat}")
                lines.append(" | ".join(parts))
    return "\n".join(lines)


def coverage(picks: pd.DataFrame, df: pd.DataFrame) -> str:
    p = picks.copy()
    p["date"] = pd.to_datetime(p["date"])
    have = df[["date", "symbol"]].drop_duplicates()
    m = p.merge(have, on=["date", "symbol"], how="left", indicator=True)
    miss = m[m["_merge"] == "left_only"]
    return (f"journal picks: {len(p)}; with morning data: {len(p) - len(miss)} ({(len(p) - len(miss)) / max(len(p), 1):.0%}); "
            f"missing symbols: {sorted(miss['symbol'].unique())[:25]}")
