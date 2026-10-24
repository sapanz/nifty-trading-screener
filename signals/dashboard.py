"""Market dashboard data: sector day change and the day's highest-volume
stocks, built from live quotes.

`build_snapshot` is pure (quotes + reference data in, one JSON-able dict out)
so it can be tested without any network. The page in docs/index.html does all
filtering and sorting client side from that single snapshot - so the sector
filter, F&O toggle, search and sort cost no extra data requests.

Definitions, so the numbers aren't mysterious:
  * change % - last price vs the previous close (Upstox's net_change).
  * sector - the Nifty 500 list's own "Industry" column; a sector's day change
    is the equal-weighted average change of its members (median is included
    too, since one outlier can drag an average), with advancers/decliners.
  * turnover - last price x day volume so far, in Rs crore: a better
    "where is the money trading" measure than raw share count, which favours
    cheap stocks.
  * volume vs 20-day average - day volume so far divided by the average FULL
    day volume of the prior 20 sessions. It is naturally well below 1x early
    in the session (a normal day is ~15% done by 10am and ~50% by midday), so
    read it as "how fast is today filling up", not as a final number.
"""
from __future__ import annotations

from datetime import datetime
from statistics import mean, median
from typing import Any

import pandas as pd

AVG_VOLUME_DAYS = 20
CRORE = 1e7


def average_volumes(daily: dict[str, pd.DataFrame], today: pd.Timestamp | None = None) -> dict[str, float]:
    """{symbol: mean volume of the last AVG_VOLUME_DAYS completed sessions},
    leaving out `today`'s own (possibly partial) row if present."""
    out: dict[str, float] = {}
    for symbol, df in daily.items():
        if today is not None:
            df = df[df.index.normalize() < today.normalize()]
        vols = df["volume"].tail(AVG_VOLUME_DAYS)
        if len(vols) >= 5 and vols.mean() > 0:
            out[symbol] = float(vols.mean())
    return out


def _change(quote: dict[str, Any], last: float) -> tuple[float, float] | None:
    """(previous close, change %) from a raw quote, or None if unusable."""
    net = quote.get("net_change")
    prev = last - net if net is not None else (quote.get("ohlc") or {}).get("close")
    if not prev or prev <= 0:
        return None
    return float(prev), (last / prev - 1) * 100


def build_snapshot(
    quotes: list[dict[str, Any]],
    key_to_symbol: dict[str, str],
    industries: dict[str, str],
    avg_volume: dict[str, float],
    fo_symbols: set[str] | None,
    now: datetime,
) -> dict[str, Any]:
    stocks = []
    for q in quotes:
        symbol = key_to_symbol.get(q.get("instrument_token", ""))
        last = q.get("last_price")
        volume = q.get("volume")
        if not symbol or not last or volume is None:
            continue
        change = _change(q, float(last))
        if change is None:
            continue
        prev_close, change_pct = change
        avg = avg_volume.get(symbol)
        ohlc = q.get("ohlc") or {}
        stocks.append({
            "symbol": symbol,
            "sector": industries.get(symbol) or "Other",
            "price": round(float(last), 2),
            "change_pct": round(change_pct, 2),
            "volume": int(volume),
            "turnover_cr": round(float(last) * float(volume) / CRORE, 2),
            "vol_vs_avg": round(float(volume) / avg, 2) if avg else None,
            "high": ohlc.get("high"), "low": ohlc.get("low"),
            "fo": bool(fo_symbols and symbol in fo_symbols),
            "last_trade": q.get("last_trade_time"),
        })

    by_sector: dict[str, list[dict]] = {}
    for s in stocks:
        by_sector.setdefault(s["sector"], []).append(s)
    sectors = [
        {
            "name": name,
            "change_pct": round(mean(m["change_pct"] for m in members), 2),
            "median_pct": round(median(m["change_pct"] for m in members), 2),
            "advancers": sum(m["change_pct"] > 0 for m in members),
            "decliners": sum(m["change_pct"] < 0 for m in members),
            "stocks": len(members),
            "turnover_cr": round(sum(m["turnover_cr"] for m in members), 1),
        }
        for name, members in by_sector.items()
    ]
    sectors.sort(key=lambda s: s["change_pct"], reverse=True)

    return {
        "updated_at": now.isoformat(timespec="seconds"),
        "stock_count": len(stocks),
        "sectors": sectors,
        "stocks": sorted(stocks, key=lambda s: s["volume"], reverse=True),
        "has_fo": fo_symbols is not None,
    }
