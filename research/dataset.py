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
# Strategy variants: target and stop as multiples of the stock's 14-day average daily range (target None = no target) and an exit
# policy. T15 = flat at 15:15; T12 / T13 = flat at 12:00 / 13:00; DYN12 / DYN15 = the dynamic exit below, capped at 12:00 / 15:15.
# Dynamic exit, judged on completed bars from 15 minutes after entry: leave at the close of the first bar where (a) the trade gave
# back half of a profit that had reached 0.15 ADR (reversal), (b) price closed back through VWAP against the position after the entry
# was on the right side of it, or (c) 45+ minutes in, the trade is within 0.1 ADR of entry and the last 9 bars' range is under 0.3 ADR (sideways).
VARIANTS = [
    {"name": "T0.5x/S1.5x flat 15:15", "tk": 0.5, "sk": 1.5, "pol": "T15"},
    {"name": "T0.5x/S1.5x flat 12:00", "tk": 0.5, "sk": 1.5, "pol": "T12"},
    {"name": "T0.5x/S1.5x dynamic<=12:00", "tk": 0.5, "sk": 1.5, "pol": "DYN12"},
    {"name": "T0.5x/S1.0x dynamic<=12:00", "tk": 0.5, "sk": 1.0, "pol": "DYN12"},
    {"name": "noT/S1.0x dynamic<=12:00", "tk": None, "sk": 1.0, "pol": "DYN12"},
    {"name": "noT/S1.0x flat 12:00", "tk": None, "sk": 1.0, "pol": "T12"},
    {"name": "noT/S1.0x dynamic<=15:15", "tk": None, "sk": 1.0, "pol": "DYN15"},
    {"name": "T0.3x/S1.2x flat 12:00", "tk": 0.3, "sk": 1.2, "pol": "T12"},
    {"name": "T0.3x/S1.2x dynamic<=12:00", "tk": 0.3, "sk": 1.2, "pol": "DYN12"},
    {"name": "T1.0x/S1.0x dynamic<=12:00", "tk": 1.0, "sk": 1.0, "pol": "DYN12"},
    {"name": "T0.25x/S0.75x flat 12:00", "tk": 0.25, "sk": 0.75, "pol": "T12"},
    {"name": "T0.7x/S1.4x dynamic<=15:15", "tk": 0.7, "sk": 1.4, "pol": "DYN15"},
    # dynamic-exit family around the best first result (v11): giveback fraction, profit trigger, sideways rule, cap time, target
    {"name": "v11 giveback 0.4", "tk": 0.7, "sk": 1.4, "pol": "DYN15", "gb": 0.4},
    {"name": "v11 giveback 0.6", "tk": 0.7, "sk": 1.4, "pol": "DYN15", "gb": 0.6},
    {"name": "v11 trigger 0.25ADR", "tk": 0.7, "sk": 1.4, "pol": "DYN15", "mfe": 0.25},
    {"name": "v11 trigger 0.10ADR", "tk": 0.7, "sk": 1.4, "pol": "DYN15", "mfe": 0.10},
    {"name": "v11 without sideways rule", "tk": 0.7, "sk": 1.4, "pol": "DYN15", "side": False},
    {"name": "v11 capped 12:00", "tk": 0.7, "sk": 1.4, "pol": "DYN12"},
    {"name": "v11 capped 13:00", "tk": 0.7, "sk": 1.4, "pol": "DYN13"},
    {"name": "T1.2x/S1.4x dynamic<=15:15", "tk": 1.2, "sk": 1.4, "pol": "DYN15"},
    {"name": "noT/S1.4x dynamic<=15:15", "tk": None, "sk": 1.4, "pol": "DYN15"},
]
GEOMS = VARIANTS                       # kept for the older name
CAP_IDX = {"T15": EXIT_IDX, "T12": 32, "T13": 44, "DYN12": 32, "DYN13": 44, "DYN15": EXIT_IDX}   # last bar index a policy may hold to
EX_REASON = {0: "target", 1: "stop", 2: "dynamic", 3: "time"}
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


def _first(mask: np.ndarray) -> int:
    return int(mask.argmax()) if mask.any() else 10**6


def _simulate(o, h, l, c, vw, ent: int, entry: float, adr: float, long: bool):
    """For every variant: win (net pnl > 0), net pnl %, bars held, exit reason (0 target, 1 stop, 2 dynamic, 3 time)."""
    V = len(VARIANTS)
    n_all = EXIT_IDX - ent + 1
    seg_o, seg_h, seg_l, seg_c, seg_v = o[ent:EXIT_IDX + 1], h[ent:EXIT_IDX + 1], l[ent:EXIT_IDX + 1], c[ent:EXIT_IDX + 1], vw[ent:EXIT_IDX + 1]
    sign = 1.0 if long else -1.0
    prof = sign * (seg_c / entry - 1)                                       # profit if closed at each bar's close
    fav = sign * ((seg_h if long else seg_l) / entry - 1)                   # best excursion within each bar
    mfe = np.maximum.accumulate(fav)
    idx = np.arange(n_all)
    prev_right = (c[ent - 1] > vw[ent - 1]) if long else (c[ent - 1] < vw[ent - 1])
    cross = prev_right & ((seg_c < seg_v) if long else (seg_c > seg_v))
    if n_all > 9:
        win_hi = np.lib.stride_tricks.sliding_window_view(seg_h, 9).max(1)
        win_lo = np.lib.stride_tricks.sliding_window_view(seg_l, 9).min(1)
        quiet = np.zeros(n_all, dtype=bool)
        quiet[8:] = ((win_hi - win_lo) / entry < 0.3 * adr)
        flat = quiet & (np.abs(prof) < 0.1 * adr) & (idx >= 9)
    else:
        flat = np.zeros(n_all, dtype=bool)
    dyn_cache: dict = {}

    def dyn_at(gb: float, trig: float, use_side: bool) -> int:
        key = (gb, trig, use_side)
        if key not in dyn_cache:
            cond = ((mfe >= trig * adr) & (prof <= (1 - gb) * mfe)) | cross
            if use_side:
                cond = cond | flat
            dyn_cache[key] = _first(cond & (idx >= 3))
        return dyn_cache[key]

    wins = np.zeros(V, dtype=np.float32); pnls = np.zeros(V, dtype=np.float32); bars = np.zeros(V, dtype=np.float32); why = np.zeros(V, dtype=np.float32)
    for v, var in enumerate(VARIANTS):
        cap = min(CAP_IDX[var["pol"]] - ent, n_all - 1)
        sk = var["sk"] * adr
        stp = entry * (1 - sk) if long else entry * (1 + sk)
        fs = _first((seg_l[:cap + 1] <= stp) if long else (seg_h[:cap + 1] >= stp))
        if var["tk"] is None:
            ft = 10**6
        else:
            tgt = entry * (1 + var["tk"] * adr) if long else entry * (1 - var["tk"] * adr)
            ft = _first((seg_h[:cap + 1] >= tgt) if long else (seg_l[:cap + 1] <= tgt))
        e = dyn_at(var.get("gb", 0.5), var.get("mfe", 0.15), var.get("side", True)) if var["pol"].startswith("DYN") else 10**6
        first_hit = min(fs, ft)
        if first_hit <= min(e, cap) and first_hit < 10**6:
            if ft < fs:
                exit_, x, r = tgt, ft, 0
            else:
                gap_o = seg_o[fs]
                exit_ = stp if fs == 0 else (min(stp, gap_o) if long else max(stp, gap_o))
                x, r = fs, 1
        elif e < cap:
            exit_, x, r = seg_c[e], e, 2
        else:
            exit_, x, r = seg_c[cap], cap, 3
        gross = (exit_ / entry - 1) * 100 if long else (1 - exit_ / entry) * 100
        net = gross - COST_PCT
        wins[v], pnls[v], bars[v], why[v] = float(net > 0), net, x, r
    return wins, pnls, bars, why


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
        cum_v = np.cumsum(v)
        vwap = np.where(cum_v > 0, np.cumsum(((h + l + c) / 3) * v) / np.maximum(cum_v, 1.0), c)
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
            for side, long in (("L", True), ("S", False)):
                w_, p_, b_, x_ = _simulate(o, h, l, c, vwap, ent, entry, adr, long)
                for g in range(len(VARIANTS)):
                    row[f"win_{side}{g}"], row[f"pnl_{side}{g}"], row[f"bars_{side}{g}"], row[f"ex_{side}{g}"] = w_[g], p_[g], b_[g], x_[g]
                    row[f"rk_{side}{g}"] = VARIANTS[g]["sk"] * adr * 100          # the stop distance (% of entry) = 1R
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
    ev = ev.copy()                                       # defragment
    return ev.sort_values(["date", "ent", "symbol"]).reset_index(drop=True)
