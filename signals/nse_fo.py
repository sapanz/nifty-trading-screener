"""Daily F&O open-interest features from NSE's free F&O bhavcopy (UDiFF).

Why this and not Upstox: Upstox only keeps OI history for LIVE contracts (about
86 days) and its expired-instruments API needs the Plus plan. NSE publishes one
file per trading day with every contract's open interest and change in open
interest, back years, with no login:

  https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip

It is an END-OF-DAY file, so for a session on day D the only usable row is the
one from the last trading day before D (`attach_prior_day_oi`) - using D's own
file would be look-ahead. That makes these "yesterday's positioning" features,
known before the open; they are not intraday OI.

Per underlying (`oi_by_symbol`):
  * fut_ret      near-month futures close vs its previous close
  * oi_total     open interest summed over ALL futures expiries, and
    oi_chg_pct   its change vs the day before. Summing over expiries makes the
                 number rollover-neutral: on roll days the near month bleeds OI
                 into the next month, which would otherwise look like unwinding.
  * pcr          put OI / call OI over the near-month stock options
  * buildup      the classic price x OI read: long build-up (price up, OI up),
                 short build-up, short covering (price up, OI down), long
                 unwinding - only when both moves clear a small materiality bar.
"""
from __future__ import annotations

import io
import logging
import os
import time
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUIRED = ("FinInstrmTp", "TckrSymb", "XpryDt", "OptnTp", "ClsPric", "PrvsClsgPric", "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol")
MIN_PRICE_MOVE = 0.005       # a "build-up" needs the futures to have moved at least 0.5% ...
MIN_OI_MOVE = 0.01           # ... and total OI to have changed at least 1%
REQUEST_DELAY_SECONDS = 0.3
BUILDUP_LABELS = {1: "long build-up", -1: "short build-up", 2: "short covering", -2: "long unwinding", 0: "none"}


def oi_by_symbol(bhav: pd.DataFrame) -> pd.DataFrame:
    """One day's bhavcopy -> one row per underlying (index = symbol)."""
    b = bhav.rename(columns=lambda c: str(c).strip())
    missing = [c for c in REQUIRED if c not in b.columns]
    if missing:
        raise ValueError(f"bhavcopy is missing columns {missing}; has {list(b.columns)}")
    tp = b["FinInstrmTp"].astype(str).str.strip()
    frame = pd.DataFrame({
        "symbol": b["TckrSymb"].astype(str).str.strip(),
        "expiry": pd.to_datetime(b["XpryDt"]),
        "opt": b["OptnTp"].astype(str).str.strip(),
        "close": pd.to_numeric(b["ClsPric"], errors="coerce"),
        "prev": pd.to_numeric(b["PrvsClsgPric"], errors="coerce"),
        "oi": pd.to_numeric(b["OpnIntrst"], errors="coerce"),
        "doi": pd.to_numeric(b["ChngInOpnIntrst"], errors="coerce"),
        "vol": pd.to_numeric(b["TtlTradgVol"], errors="coerce"),
    })

    fut = frame[tp == "STF"]
    near = fut.sort_values("expiry").drop_duplicates("symbol").set_index("symbol")
    agg = fut.groupby("symbol").agg(oi_total=("oi", "sum"), doi_total=("doi", "sum"), fut_vol=("vol", "sum"))
    out = agg.copy()
    out["fut_ret"] = near["close"] / near["prev"] - 1
    prev_oi = agg["oi_total"] - agg["doi_total"]
    out["oi_chg_pct"] = np.where(prev_oi > 0, agg["doi_total"] / prev_oi, np.nan)

    opt = frame[tp == "STO"]
    opt = opt[opt["expiry"] == opt.groupby("symbol")["expiry"].transform("min")]
    by_side = opt.pivot_table(index="symbol", columns="opt", values="oi", aggfunc="sum")
    if {"PE", "CE"} <= set(by_side.columns):
        out["pcr"] = (by_side["PE"] / by_side["CE"].where(by_side["CE"] > 0)).reindex(out.index)
    else:
        out["pcr"] = np.nan
    return out.drop(columns=["doi_total"])


def buildup_code(fut_ret, oi_chg_pct):
    """+1 long build-up, -1 short build-up, +2 short covering, -2 long unwinding, 0 otherwise."""
    fut_ret, oi_chg_pct = np.asarray(fut_ret, dtype=float), np.asarray(oi_chg_pct, dtype=float)
    up, down = fut_ret >= MIN_PRICE_MOVE, fut_ret <= -MIN_PRICE_MOVE
    oi_up, oi_down = oi_chg_pct >= MIN_OI_MOVE, oi_chg_pct <= -MIN_OI_MOVE
    return np.select([up & oi_up, down & oi_up, up & oi_down, down & oi_down], [1, -1, 2, -2], default=0)


def fetch_bhavcopy(session: requests.Session, d: date, retries: int = 3) -> pd.DataFrame | None:
    """The raw bhavcopy for trading day `d`, or None if NSE has no file for it
    (a holiday). Raises on persistent errors rather than silently dropping days."""
    url = URL.format(d=d)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=60)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                return pd.read_csv(z.open(z.namelist()[0]))
        except (requests.RequestException, zipfile.BadZipFile, ValueError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not fetch the F&O bhavcopy for {d}: {last}")


def build_oi_table(start: date, end: date, cache_dir: str) -> pd.DataFrame:
    """Concatenated per-day OI features for every weekday in [start, end],
    cached one small pickle per day (holidays are cached as empty)."""
    os.makedirs(cache_dir, exist_ok=True)
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=20)
    except requests.RequestException as exc:
        logger.warning("NSE homepage warm-up failed (continuing): %s", exc)
    frames, fetched, holidays = [], 0, 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            path = f"{cache_dir}/oi_{d:%Y%m%d}.pkl"
            if os.path.exists(path):
                day = pd.read_pickle(path)
            else:
                raw = fetch_bhavcopy(session, d)
                day = pd.DataFrame() if raw is None else oi_by_symbol(raw)
                day.to_pickle(path)
                time.sleep(REQUEST_DELAY_SECONDS)
                fetched += 1
            if day.empty:
                holidays += 1
            else:
                frames.append(day.assign(date=pd.Timestamp(d)).reset_index())
        d += timedelta(days=1)
    logger.info("F&O bhavcopy: %d trading days with data (%d newly downloaded), %d weekday holidays", len(frames), fetched, holidays)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["date"] = out["date"].astype("datetime64[ns]")
    return out


def attach_prior_day_oi(df: pd.DataFrame, oi: pd.DataFrame) -> pd.DataFrame:
    """Join, onto each (symbol, session), the OI features of the LAST TRADING
    DAY BEFORE that session. Adds oi_date, fut_ret_1d, oi_chg_pct, oi_chg_3d,
    pcr, pcr_chg, buildup (code), oi_dir (signed build-up strength)."""
    if oi.empty:
        return df
    o = oi.sort_values(["symbol", "date"]).copy()
    g = o.groupby("symbol")
    o["oi_chg_3d"] = o["oi_total"] / g["oi_total"].shift(3) - 1
    o["pcr_chg"] = o["pcr"] - g["pcr"].shift(1)
    o["buildup"] = buildup_code(o["fut_ret"], o["oi_chg_pct"])
    # signed strength: price direction x (positive-only) OI growth. Long build-up -> +, short build-up -> -;
    # covering / unwinding (OI shrinking) carry no build-up and sit at 0.
    o["oi_dir"] = np.sign(o["fut_ret"]) * o["oi_chg_pct"].clip(lower=0)
    o = o.rename(columns={"date": "oi_date", "fut_ret": "fut_ret_1d"})
    keep = ["symbol", "oi_date", "fut_ret_1d", "oi_chg_pct", "oi_chg_3d", "pcr", "pcr_chg", "buildup", "oi_dir"]
    left = df.copy()
    left["date"] = left["date"].astype("datetime64[ns]")
    left = left.sort_values("date")
    right = o[keep].astype({"oi_date": "datetime64[ns]"}).sort_values("oi_date")
    return pd.merge_asof(left, right, left_on="date", right_on="oi_date", by="symbol",
                         allow_exact_matches=False, tolerance=pd.Timedelta(days=5)).reset_index(drop=True)
