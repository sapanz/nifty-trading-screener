"""Event table for the intraday research loop.

One event = one stock on one day at one decision time (entry at the OPEN of the entry bar, with
everything below computed from bars COMPLETED before it - no look-ahead). Entry bars: 10:00, 10:15, 10:30.
For every event and both sides, a bracket trade is simulated for each geometry (target / stop as multiples of the
stock's 14-day average daily range): stop first if a bar reaches both, a gap through the stop exits at the open, flat at
the close of the 15:10 bar (15:15). The label is whether the target was hit first; pnl is net of costs.
"""
from __future__ import annotations

from multiprocessing import Pool

import numpy as np
import pandas as pd

from signals import config
from signals.intraday_blocks import block_score
from signals.orderblocks import find_order_blocks, first_touch

SLOTS = 75
START_MIN = 9 * 60 + 15
ENTRIES = (9, 12, 15)                  # bar index of the entry bar: 10:00, 10:15, 10:30
EXIT_IDX = 71                          # the 15:10 bar closes at 15:15
COST_PCT = 0.10                        # round-trip, % of entry
GEOMS = [(0.25, 0.75), (0.25, 1.0), (0.3, 1.2), (0.4, 1.2), (0.5, 1.0), (0.5, 1.5), (0.7, 1.4), (1.0, 1.0)]
MIN_HISTORY_DAYS = 21
OB_IMPULSE = config.INTRADAY_OB_IMPULSE_ATR

FEATURES = [
    "ent", "gap", "ret_open", "ret15", "ret30", "day_chg", "rng_pos", "range_pct", "bar_rng", "vwap_dist", "volx", "vol_last",
    "or_hi", "or_lo", "pdh_dist", "pdl_dist", "eff", "up_share", "r1", "r3", "r5", "dist20", "dist50", "hi52_dist", "lo52_dist",
    "pos20", "adr", "dow", "dom",
    "mkt_ret_open", "mkt_breadth", "mkt_gap", "mkt_day_chg", "mkt_r1", "rs_mkt", "rs_day", "rank_ret", "rank_volx", "rank_gap",
    "sec_ret_open", "rs_sec",
    "obb_sc", "obb_state", "obb_dist", "obb_stop", "obr_sc", "obr_state", "obr_dist", "obr_stop",
]


def day_matrices(df: pd.DataFrame) -> tuple[list, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-session 75-slot arrays (o, h, l, c, v) with missing bars filled (close carried, volume 0); sessions with < 60 bars are dropped."""
    slot = ((df.index.hour * 60 + df.index.minute - START_MIN) // 5).to_numpy()
    ok = (slot >= 0) & (slot < SLOTS)
    for col in ("open", "high", "low", "close"):                 # bad prints (zero / negative / missing) are dropped
        ok &= (df[col].to_numpy(dtype=float) > 0)
    df, slot = df[ok], slot[ok]
    days = df.index.normalize()
    cols = [df[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close", "volume")]
    dates, O, H, L, C, V = [], [], [], [], [], []
    order = np.argsort(days.values, kind="stable")
    uniq, starts = np.unique(days.values[order], return_index=True)
    bounds = list(starts) + [len(order)]
    for k, day in enumerate(uniq):
        idx = order[bounds[k]:bounds[k + 1]]
        if len(idx) < 60:
            continue
        o = np.full(SLOTS, np.nan); h = o.copy(); l = o.copy(); c = o.copy(); v = np.zeros(SLOTS)
        s = slot[idx]
        o[s], h[s], l[s], c[s], v[s] = cols[0][idx], cols[1][idx], cols[2][idx], cols[3][idx], np.nan_to_num(cols[4][idx])
        # carry the close through missing bars
        for i in range(SLOTS):
            if np.isnan(c[i]):
                c[i] = c[i - 1] if i > 0 and not np.isnan(c[i - 1]) else np.nan
        first = int(np.argmax(~np.isnan(c)))
        c[:first] = c[first]
        miss = np.isnan(o)
        o[miss], h[miss], l[miss] = c[miss], c[miss], c[miss]
        dates.append(pd.Timestamp(day)); O.append(o); H.append(h); L.append(l); C.append(c); V.append(v)
    return dates, np.array(O), np.array(H), np.array(L), np.array(C), np.array(V)


def _ob_features(o, h, l, c, v, prev, slot):
    """Strongest active bull / bear order block at `slot` as features (see signals/intraday_blocks.py)."""
    zero = (0.0, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0)
    if prev is None:
        return zero
    O = np.concatenate([prev[0], o]); H = np.concatenate([prev[1], h]); Lw = np.concatenate([prev[2], l])
    C = np.concatenate([prev[3], c]); V = np.concatenate([prev[4], v])
    first_today = SLOTS
    pc = float(prev[3][-1])
    last = first_today + slot
    out = {}
    for b in find_order_blocks(O[:last + 1], H[:last + 1], Lw[:last + 1], C[:last + 1], impulse_atr=OB_IMPULSE, volume=V[:last + 1]):
        if b.formed_idx < first_today or last - b.formed_idx > config.INTRADAY_OB_EXPIRY_BARS:
            continue
        t = first_touch(b, O[:last + 1], H[:last + 1], Lw[:last + 1], config.INTRADAY_OB_EXPIRY_BARS)
        buf = config.OB_STOP_BUFFER_PCT / 100
        if t is None:
            state = 1
        else:
            thru = (Lw[t:last + 1] <= b.low * (1 - buf)).any() if b.direction == "bull" else (H[t:last + 1] >= b.high * (1 + buf)).any()
            if thru or last - t > config.INTRADAY_OB_RECENT_BARS:
                continue
            state = 2
        wd = (C[b.formed_idx] > pc) if b.direction == "bull" else (C[b.formed_idx] < pc)
        sc = block_score(b.impulse_atr, b.vol_ratio, b.swept, wd)
        price = C[last]
        if b.direction == "bull":
            dist, stop = (price / b.high - 1) * 100, (price - b.low * (1 - buf)) / price * 100
        else:
            dist, stop = (b.low / price - 1) * 100, (b.high * (1 + buf) - price) / price * 100
        if stop <= 0:
            continue
        rank = (state, b.formed_idx)
        if b.direction not in out or rank > out[b.direction][0]:
            out[b.direction] = (rank, sc, state, dist, stop)
    bull = out.get("bull"); bear = out.get("bear")
    return (bull[1], bull[2], bull[3], bull[4]) + (bear[1], bear[2], bear[3], bear[4]) if (bull and bear) else (
        (bull[1], bull[2], bull[3], bull[4]) + zero[4:] if bull else (zero[:4] + (bear[1], bear[2], bear[3], bear[4]) if bear else zero))


def _simulate(o, h, l, c, ent: int, entry: float, adr: float, long: bool) -> tuple[np.ndarray, np.ndarray]:
    """win (target first) and net pnl % for every geometry, one side."""
    G = len(GEOMS)
    tk = np.array([g[0] for g in GEOMS]) * adr
    sk = np.array([g[1] for g in GEOMS]) * adr
    seg_o, seg_h, seg_l = o[ent:EXIT_IDX + 1][:, None], h[ent:EXIT_IDX + 1][:, None], l[ent:EXIT_IDX + 1][:, None]
    n = seg_o.shape[0]
    if long:
        tgt, stp = entry * (1 + tk), entry * (1 - sk)
        hit_s, hit_t = seg_l <= stp, seg_h >= tgt
    else:
        tgt, stp = entry * (1 - tk), entry * (1 + sk)
        hit_s, hit_t = seg_h >= stp, seg_l <= tgt
    inf = n + 5
    fs = np.where(hit_s.any(0), hit_s.argmax(0), inf)
    ft = np.where(hit_t.any(0), hit_t.argmax(0), inf)
    win = (ft < fs).astype(np.float32)
    exit_ = np.full(G, c[EXIT_IDX])
    for g in range(G):
        if ft[g] < fs[g]:
            exit_[g] = tgt[g]
        elif fs[g] < inf:
            x = fs[g]
            gap_o = seg_o[x, 0]
            exit_[g] = stp[g] if x == 0 else (min(stp[g], gap_o) if long else max(stp[g], gap_o))
    gross = (exit_ / entry - 1) * 100 if long else (1 - exit_ / entry) * 100
    return win, (gross - COST_PCT).astype(np.float32)


def symbol_events(args) -> pd.DataFrame | None:
    symbol, df = args
    dates, O, H, L, C, V = day_matrices(df)
    n = len(dates)
    if n < MIN_HISTORY_DAYS + 5:
        return None
    dclose, dopen = C[:, -1], O[:, 0]
    dhigh, dlow = H.max(1), L.min(1)
    cumV = np.cumsum(V, axis=1)
    rows = []
    for i in range(MIN_HISTORY_DAYS, n):
        if (dates[i] - dates[i - 1]).days > 6:        # a gap in the data (missing weeks): skip until history rebuilds
            continue
        pc, pdh, pdl = dclose[i - 1], dhigh[i - 1], dlow[i - 1]
        o, h, l, c, v = O[i], H[i], L[i], C[i], V[i]
        base_cum = cumV[i - 20:i].mean(0)
        adr = float(np.mean((dhigh[i - 14:i] - dlow[i - 14:i]) / dclose[i - 14:i]))
        r1 = dclose[i - 1] / dclose[i - 2] - 1
        r3 = dclose[i - 1] / dclose[i - 4] - 1
        r5 = dclose[i - 1] / dclose[i - 6] - 1
        sma20 = dclose[i - 20:i].mean()
        sma50 = dclose[i - 50:i].mean() if i >= 50 else np.nan
        lo_hi = slice(max(0, i - 250), i)
        hi52, lo52 = dhigh[lo_hi].max(), dlow[lo_hi].min()
        w = slice(i - 20, i)
        pos20 = (pc - dlow[w].min()) / max(dhigh[w].max() - dlow[w].min(), 1e-9)
        prev = (O[i - 1], H[i - 1], L[i - 1], C[i - 1], V[i - 1])
        for ent in ENTRIES:
            s = ent - 1
            hi, lo, cl = h[:ent].max(), l[:ent].min(), c[s]
            tp = (h[:ent] + l[:ent] + c[:ent]) / 3
            vw = (tp * v[:ent]).sum() / max(v[:ent].sum(), 1.0)
            path = np.abs(np.diff(c[:ent])).sum()
            orh, orl = h[:3].max(), l[:3].min()
            ob = _ob_features(o, h, l, c, v, prev, s)
            row = {
                "symbol": symbol, "date": dates[i], "ent": ent, "gap": o[0] / pc - 1, "ret_open": cl / o[0] - 1,
                "ret15": cl / c[s - 3] - 1, "ret30": cl / c[s - 6] - 1, "day_chg": cl / pc - 1,
                "rng_pos": (cl - lo) / (hi - lo) if hi > lo else 0.5, "range_pct": (hi - lo) / o[0],
                "bar_rng": float(np.mean((h[:ent] - l[:ent]) / c[:ent])), "vwap_dist": cl / vw - 1,
                "volx": cumV[i, s] / base_cum[s] if base_cum[s] > 0 else np.nan,
                "vol_last": v[s - 5:s + 1].mean() / (v[:ent - 6].mean() + 1.0),
                "or_hi": cl / orh - 1, "or_lo": cl / orl - 1, "pdh_dist": cl / pdh - 1, "pdl_dist": cl / pdl - 1,
                "eff": abs(cl - o[0]) / path if path > 0 else 0.0, "up_share": float((c[1:ent] > c[:ent - 1]).mean()),
                "r1": r1, "r3": r3, "r5": r5, "dist20": pc / sma20 - 1, "dist50": pc / sma50 - 1 if i >= 50 else np.nan,
                "hi52_dist": pc / hi52 - 1, "lo52_dist": pc / lo52 - 1, "pos20": pos20, "adr": adr,
                "dow": dates[i].dayofweek, "dom": dates[i].day,
                "obb_sc": ob[0], "obb_state": ob[1], "obb_dist": ob[2], "obb_stop": ob[3],
                "obr_sc": ob[4], "obr_state": ob[5], "obr_dist": ob[6], "obr_stop": ob[7],
            }
            entry = o[ent]
            wl, pl = _simulate(o, h, l, c, ent, entry, adr, True)
            ws, ps = _simulate(o, h, l, c, ent, entry, adr, False)
            for g in range(len(GEOMS)):
                row[f"win_L{g}"], row[f"pnl_L{g}"], row[f"win_S{g}"], row[f"pnl_S{g}"] = wl[g], pl[g], ws[g], ps[g]
            rows.append(row)
    if not rows:
        return None
    out = pd.DataFrame(rows)
    num = out.columns.difference(["symbol", "date"])
    out[num] = out[num].astype(np.float32).replace([np.inf, -np.inf], np.nan)
    return out


def build_events(intraday: dict[str, pd.DataFrame], sectors: dict[str, str] | None = None, processes: int = 4) -> pd.DataFrame:
    items = list(intraday.items())
    if processes > 1:
        with Pool(processes) as pool:
            frames = pool.map(symbol_events, items, chunksize=4)
    else:
        frames = [symbol_events(x) for x in items]
    ev = pd.concat([f for f in frames if f is not None], ignore_index=True)
    del frames
    ev["sector"] = ev["symbol"].map(sectors or {}).fillna("Other")
    g = ev.groupby(["date", "ent"])
    ev["mkt_ret_open"] = g["ret_open"].transform("mean")
    ev["mkt_breadth"] = g["ret_open"].transform(lambda x: (x > 0).mean())
    ev["mkt_gap"] = g["gap"].transform("mean")
    ev["mkt_day_chg"] = g["day_chg"].transform("mean")
    ev["mkt_r1"] = g["r1"].transform("mean")
    ev["rs_mkt"] = ev["ret_open"] - ev["mkt_ret_open"]
    ev["rs_day"] = ev["day_chg"] - ev["mkt_day_chg"]
    ev["rank_ret"] = g["ret_open"].rank(pct=True)
    ev["rank_volx"] = g["volx"].rank(pct=True)
    ev["rank_gap"] = g["gap"].rank(pct=True)
    ev["sec_ret_open"] = ev.groupby(["date", "ent", "sector"])["ret_open"].transform("mean")
    ev["rs_sec"] = ev["ret_open"] - ev["sec_ret_open"]
    return ev.sort_values(["date", "ent", "symbol"]).reset_index(drop=True)
