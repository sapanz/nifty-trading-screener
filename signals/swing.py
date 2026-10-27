"""Swing R-factor: the dashboard's R-factor on daily charts, built after the close.

For each F&O stock, over the last WINDOW (5) sessions:
  vol_vs_avg  - latest session's volume / the average of the 20 sessions before it
  oi_chg      - open interest added, % (total OI of the near + next-month futures)
  px_chg      - price move, % (equity close)
Each is ranked (percentile, ties averaged) among all F&O stocks; R is the
geometric mean of the three ranks (0-100) - high only when volume, OI buildup
and price move are all high - and 0 where OI did not grow. The tag says which
side is building: long (OI up, price up), short (OI up, price down); cover /
unwind (OI down) are not scored. This mirrors the intraday R-factor's
since-the-open version (docs/index.html attachR), measured over sessions
instead of candles.

Open interest across contract rolls is the awkward part. Traders roll positions
from the near to the next-month contract in the days before expiry, so near-
month OI alone collapses for reasons that mean nothing; the SUM of near + next
month does not. But the pair of contracts changes at each expiry, and an
expired contract's history is not available afterwards - so each night's total
is saved to a store (`update_store`) and the series is built from those saved
totals, never recomputed across a roll. A window that includes an expiry date
is flagged `roll` (the OI change then includes positions settled, not
exited): treat it with care.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean, median

import pandas as pd

WINDOW = 5
AVG_DAYS = 20
CHART_DAYS = 20
MIN_STOCKS = 10
CRORE = 1e7


def combined_oi(frames: list[pd.DataFrame]) -> pd.Series:
    """Daily total open interest across the given contracts' daily candle
    frames (near, next month): summed on the dates every contract has a value,
    so a contract listed later does not inject a step."""
    series = []
    for df in frames:
        if df is None or df.empty or "open_interest" not in df.columns:
            continue
        s = df["open_interest"].dropna()
        s = s[s > 0]
        s.index = s.index.normalize()
        series.append(s[~s.index.duplicated(keep="last")])
    if not series:
        return pd.Series(dtype=float)
    total = pd.concat(series, axis=1, join="inner").sum(axis=1)
    return total.sort_index()


def previous_monthly_expiry(near: date) -> date:
    """The monthly stock-F&O expiry before `near`'s: NSE's expiry is the last
    Tuesday of the month, so the last Tuesday of the month before near's. (A
    holiday can shift an expiry by a day; the effect here is one session.)"""
    day = near.replace(day=1) - timedelta(days=1)
    while day.weekday() != 1:
        day -= timedelta(days=1)
    return day


PX_LOOKBACK_DAYS = 45      # equity closes/volumes are kept from this long before the first saved OI date


def update_store(
    store: dict | None, combined: dict[str, pd.Series], near_expiries: set[date],
    equity: dict[str, pd.DataFrame] | None = None, seed_floor: date | None = None,
) -> dict:
    """Add each night's totals to the saved store. A date already saved is
    never overwritten: its total was taken over the contracts live at the time,
    and recomputing it over today's contract pair would mix compositions.

    With `equity`, the store also keeps each stock's daily [close, volume] (from
    PX_LOOKBACK_DAYS before its first saved OI date), so the saved file is
    self-contained: the swing backtest (signals/swing_backtest.py) can score
    and measure every saved date from it alone."""
    store = store or {"version": 3, "oi": {}, "px": {}, "expiries": []}
    store.setdefault("px", {})
    # The totals the API gives for past dates are over TODAY's near + next contract, which for dates
    # before the last expiry were the next-month and far contracts - their OI ramps up as they approach
    # expiry, which reads as a false buildup. So history is only trusted from the session after the last
    # expiry (`seed_floor`); it is applied once, remembered in the store, and also cleans up a store
    # that was seeded without it. Later nights are saved as they happen, so they are unaffected.
    if seed_floor and not store.get("seed_floor"):
        store["seed_floor"] = seed_floor.isoformat()
        for saved in store["oi"].values():
            for key in [k for k in saved if k <= store["seed_floor"]]:
                del saved[key]
        store["px"] = {}
    floor = store.get("seed_floor", "")
    for symbol, series in combined.items():
        saved = store["oi"].setdefault(symbol, {})
        for d, value in series.items():
            key = d.date().isoformat()
            if key > floor and key not in saved and value > 0:
                saved[key] = int(value)
    store["expiries"] = sorted(set(store.get("expiries", [])) | {d.isoformat() for d in near_expiries})
    for symbol, df in (equity or {}).items():
        saved_oi = store["oi"].get(symbol)
        if not saved_oi or df.empty:
            continue
        cutoff = (date.fromisoformat(min(saved_oi)) - pd.Timedelta(days=PX_LOOKBACK_DAYS)).isoformat()
        saved_px = store["px"].setdefault(symbol, {})
        for d, close, volume in zip(df.index.normalize(), df["close"], df["volume"]):
            key = d.date().isoformat()
            if key >= cutoff and key not in saved_px and pd.notna(close):
                saved_px[key] = [round(float(close), 2), int(volume) if pd.notna(volume) else 0]
    return store


def _ranks(values: list[float]) -> list[float]:
    n = len(values)
    if n < 2:
        return [50.0] * n
    ordered = sorted(values)
    return [(sum(x < v for x in ordered) + (sum(x == v for x in ordered) - 1) / 2) / (n - 1) * 100 for v in values]


def build_swing(
    store: dict, equity_daily: dict[str, pd.DataFrame], industries: dict[str, str], now: datetime,
    allow_missing_oi: bool = False,
) -> dict:
    """The swing snapshot as of the last candle of `equity_daily` (so passing frames cut at an earlier date gives that
    day's view). With allow_missing_oi a stock without saved open interest for the window still gets a row (price,
    volume and the order-block zones the caller adds) but no OI fields or R-factor - used for the as-of-date history,
    where dates older than the saved OI series would otherwise have no rows at all."""
    expiries = [date.fromisoformat(d) for d in store.get("expiries", [])]
    rows = []
    symbols = list(equity_daily) if allow_missing_oi else list(store["oi"])
    for symbol in symbols:
        saved = store["oi"].get(symbol, {})
        df = equity_daily.get(symbol)
        if df is None or len(df) < WINDOW + 2:
            continue
        dates = [d.date().isoformat() for d in df.index.normalize()]
        t = len(df) - 1
        oi = {d: saved.get(d) for d in dates[max(0, t - CHART_DAYS - 1): t + 1]}
        has_oi = not (oi.get(dates[t]) is None or oi.get(dates[t - WINDOW]) is None or oi.get(dates[t - 1]) is None)
        if not has_oi and not allow_missing_oi:
            continue
        close, volume = df["close"].to_numpy(dtype=float), df["volume"].to_numpy(dtype=float)   # plain floats: the snapshot is saved as JSON
        prior = volume[max(0, t - AVG_DAYS): t]
        if len(prior) < 10 or prior.mean() <= 0 or close[t - 1] <= 0 or close[t - WINDOW] <= 0:
            continue
        px_chg = float((close[t] / close[t - WINDOW] - 1) * 100)
        if not has_oi:
            rows.append({
                "symbol": symbol, "sector": industries.get(symbol) or "Other", "price": round(float(close[t]), 2),
                "change_pct": round(float(close[t] / close[t - 1] - 1) * 100, 2), "chg_5d": round(px_chg, 2),
                "volume": int(volume[t]), "turnover_cr": round(float(close[t] * volume[t]) / CRORE, 2),
                "vol_vs_avg": round(float(volume[t] / prior.mean()), 2), "oi": None, "oi_1d": None, "oi_chg": None, "bias": None,
                "roll": False, "fo": True, "series": [], "_px": px_chg,
            })
            continue
        oi_t, oi_w, oi_1 = oi[dates[t]], oi[dates[t - WINDOW]], oi[dates[t - 1]]
        oi_chg = float((oi_t / oi_w - 1) * 100)
        d0, d1 = date.fromisoformat(dates[t - WINDOW]), date.fromisoformat(dates[t])
        rows.append({
            "symbol": symbol, "sector": industries.get(symbol) or "Other", "price": round(float(close[t]), 2),
            "change_pct": round(float(close[t] / close[t - 1] - 1) * 100, 2), "chg_5d": round(px_chg, 2),
            "volume": int(volume[t]), "turnover_cr": round(float(close[t] * volume[t]) / CRORE, 2),
            "vol_vs_avg": round(float(volume[t] / prior.mean()), 2),
            "oi": int(oi_t), "oi_1d": round((oi_t / oi_1 - 1) * 100, 2), "oi_chg": round(oi_chg, 2),
            "bias": ("long" if px_chg >= 0 else "short") if oi_chg > 0 else ("cover" if px_chg >= 0 else "unwind"),
            "roll": any(d0 <= e <= d1 for e in expiries), "fo": True,
            "series": [[d, oi[d], round(float(close[dates.index(d)]), 2)] for d in dates[max(0, t - CHART_DAYS + 1): t + 1] if oi.get(d)],
            "_px": px_chg,
        })
    ranked = [r for r in rows if r["oi_chg"] is not None]
    if len(ranked) >= MIN_STOCKS:
        pv = _ranks([r["vol_vs_avg"] for r in ranked])
        po = _ranks([r["oi_chg"] for r in ranked])
        pp = _ranks([abs(r["_px"]) for r in ranked])
        for r, a, b, c in zip(ranked, pv, po, pp):
            r["r"] = round((a * b * c) ** (1 / 3)) if r["oi_chg"] > 0 else 0
        for r in rows:
            r.setdefault("r", None)
    else:
        for r in rows:
            r["r"] = None
    for r in rows:
        del r["_px"]

    by_sector: dict[str, list[dict]] = {}
    for r in rows:
        by_sector.setdefault(r["sector"], []).append(r)
    sectors = sorted((
        {"name": name, "change_pct": round(mean(m["chg_5d"] for m in members), 2),
         "chg_1d": round(mean(m["change_pct"] for m in members), 2),
         "median_pct": round(median(m["chg_5d"] for m in members), 2),
         "advancers": sum(m["chg_5d"] > 0 for m in members), "decliners": sum(m["chg_5d"] < 0 for m in members),
         "stocks": len(members), "turnover_cr": round(sum(m["turnover_cr"] for m in members), 1)}
        for name, members in by_sector.items()
    ), key=lambda s: s["change_pct"], reverse=True)
    asof = max((df.index[-1] for df in equity_daily.values() if len(df)), default=None)
    return {
        "updated_at": now.isoformat(timespec="seconds"), "asof": asof.date().isoformat() if asof is not None else None,
        "window": WINDOW, "stock_count": len(rows), "sectors": sectors,
        "stocks": sorted(rows, key=lambda r: (r["r"] is None, -(r["r"] or 0), -r["vol_vs_avg"])),
    }
