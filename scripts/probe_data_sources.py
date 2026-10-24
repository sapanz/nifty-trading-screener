#!/usr/bin/env python3
"""One-off probe (pickfinder research): which OI / sector sources are actually
reachable from the Actions runner, and in what shape?

Run through .github/workflows/pickfinder_research.yml with
research/run_config.json {"mode": "probe"}. Prints structure and a few sample
rows only - no secrets, no bulk data. Every probe is independent, so one
blocked source doesn't hide the others.

  1. Sector map: NSE's Nifty 500 list ("Industry" column) vs the F&O universe
     and the journal's picks.
  2. NSE F&O bhavcopy (daily OI / change in OI per contract, free): reachable?
     which columns? for an old and a recent date.
  3. Upstox 5-minute / daily candles WITH open interest for a live stock-
     futures contract: how far back does a live contract's history go?
  4. Upstox "expired instruments" API (would give intraday OI for expired
     contracts): allowed on this account? what does it return?
"""
import csv
import gzip
import io
import json
import os
import sys
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import config, data, universe  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

UA = {"User-Agent": universe._HEADERS["User-Agent"], "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
BODY_LIMIT = 700


def section(title: str) -> None:
    print(f"\n===== {title} =====")


def short(text: str, n: int = BODY_LIMIT) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + f"... [{len(text)} chars]"


def guarded(fn):
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as exc:  # noqa: BLE001 - a probe must never abort the others
            print(f"  PROBE ERROR in {fn.__name__}: {type(exc).__name__}: {short(str(exc))}")
    return wrapper


@guarded
def probe_sectors(fo_symbols: set, pick_symbols: set) -> None:
    section("1. Sector map (NSE Nifty 500 list, 'Industry' column)")
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=UA, timeout=20)
    except requests.RequestException as exc:
        print(f"  homepage warm-up failed (continuing): {exc}")
    text = None
    for url in config.NSE_INDEX_LIST_URLS:
        r = session.get(url, headers=UA, timeout=30)
        print(f"  GET {url} -> {r.status_code}, {len(r.content)} bytes")
        if r.ok:
            text = r.text
            break
    if text is None:
        return
    rows = list(csv.DictReader(io.StringIO(text)))
    print(f"  columns: {list(rows[0].keys())}; rows: {len(rows)}")
    sector = {r["Symbol"].strip(): r["Industry"].strip() for r in rows if r.get("Symbol")}
    counts = pd.Series(sector).value_counts()
    print(f"  distinct industries: {len(counts)}")
    print("  " + "; ".join(f"{k}={v}" for k, v in counts.items()))
    in_fo = [s for s in fo_symbols if s in sector]
    print(f"  today's F&O symbols with a sector: {len(in_fo)}/{len(fo_symbols)}; "
          f"missing (first 15): {sorted(fo_symbols - set(sector))[:15]}")
    in_picks = [s for s in pick_symbols if s in sector]
    print(f"  journal-pick symbols with a sector: {len(in_picks)}/{len(pick_symbols)}; "
          f"missing (first 15): {sorted(pick_symbols - set(sector))[:15]}")
    fo_by_sector = pd.Series({s: sector[s] for s in in_fo}).value_counts()
    print("  F&O stocks per industry: " + "; ".join(f"{k}={v}" for k, v in fo_by_sector.items()))


def bhavcopy_url(d: date) -> str:
    return f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


@guarded
def probe_bhavcopy() -> None:
    section("2. NSE F&O bhavcopy (daily OI / change in OI)")
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=UA, timeout=20)
    except requests.RequestException as exc:
        print(f"  homepage warm-up failed (continuing): {exc}")
    for d in (date(2024, 12, 2), date(2025, 6, 17), date(2026, 10, 23)):
        url = bhavcopy_url(d)
        r = session.get(url, headers=UA, timeout=40)
        print(f"  {d} GET {url.split('/')[-1]} -> {r.status_code}, {len(r.content)} bytes, content-type={r.headers.get('content-type')}")
        if not r.ok:
            print(f"    body: {short(r.text, 200)}")
            continue
        try:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            name = z.namelist()[0]
            df = pd.read_csv(z.open(name))
        except Exception as exc:  # noqa: BLE001
            print(f"    could not parse as zip/csv: {exc}; first bytes: {r.content[:120]!r}")
            continue
        print(f"    file {name}: {len(df)} rows; columns: {list(df.columns)}")
        tp = next((c for c in df.columns if c.lower().startswith("fininstrmtp")), None)
        sym = next((c for c in df.columns if c.lower() == "tckrsymb"), None)
        if tp:
            print(f"    rows by instrument type: {df[tp].value_counts().to_dict()}")
        if tp and sym:
            sample = df[(df[sym] == "RELIANCE") & (df[tp] == "STF")]
            print(f"    RELIANCE stock-futures rows: {len(sample)}")
            if len(sample):
                print("    " + short(sample.head(3).to_string(), 1200))
            print(f"    distinct stock-futures underlyings: {df[df[tp] == 'STF'][sym].nunique()}")
            opts = df[(df[sym] == "RELIANCE") & (df[tp] == "STO")]
            print(f"    RELIANCE stock-option rows: {len(opts)}")


def instrument_master() -> pd.DataFrame:
    r = requests.get(config.UPSTOX_INSTRUMENTS_URL, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(gzip.decompress(r.content)))
    df.columns = [c.strip().lower() for c in df.columns]
    return df


@guarded
def probe_live_futures(client: UpstoxClient, master: pd.DataFrame) -> None:
    section("3. Upstox candles with OI for LIVE stock futures")
    fut = master[master["instrument_type"].astype(str).str.upper() == config.UPSTOX_FUTSTK_TYPE].copy()
    fut["underlying"] = fut["tradingsymbol"].astype(str).str.replace(r"\d{2}[A-Z]{3}FUT$", "", regex=True)
    rel = fut[fut["underlying"] == "RELIANCE"].sort_values("expiry")
    print(f"  RELIANCE live futures contracts: {len(rel)}")
    print("  " + short(rel[["tradingsymbol", "instrument_key", "expiry", "lot_size"]].to_string(), 900))
    if rel.empty:
        return
    key = rel.iloc[0]["instrument_key"]
    print(f"  expiry column dtype/sample: {rel['expiry'].dtype} / {rel['expiry'].iloc[0]!r}")
    end = date(2026, 10, 23)
    for label, unit, interval, start in (("5-minute, last 28 days", "minutes", 5, end - timedelta(days=27)),
                                        ("daily, back to 2025-01-01", "days", 1, date(2025, 1, 1))):
        df = client.get_candles_between(key, unit, interval, start, end)
        print(f"  {label}: {len(df)} bars" + (f", {df.index[0]} -> {df.index[-1]}; columns {list(df.columns)}" if len(df) else ""))
        if len(df) and "open_interest" in df.columns:
            print(f"    open_interest: non-null {int(df['open_interest'].notna().sum())}, "
                  f"zeros {int((df['open_interest'] == 0).sum())}; sample last 3: {df['open_interest'].tail(3).tolist()}")
        elif len(df):
            print("    NO open_interest column in the candles")
    # how far back does the history of the CURRENT near-month contract go?
    first = client.get_candles_between(key, "days", 1, date(2025, 1, 1), end)
    if len(first):
        print(f"  earliest daily bar of the live near-month contract: {first.index[0].date()} "
              f"(so a live contract's history is ~{(end - first.index[0].date()).days} days)")


@guarded
def probe_expired(client: UpstoxClient) -> None:
    section("4. Upstox expired-instruments API")
    s = client._session
    base = f"{config.UPSTOX_BASE_URL}/expired-instruments"
    eq_key = "NSE_EQ|INE002A01018"   # RELIANCE
    r = s.get(f"{base}/expiries", params={"instrument_key": eq_key}, timeout=30)
    print(f"  GET expiries (RELIANCE) -> {r.status_code}: {short(r.text)}")
    if not r.ok:
        return
    expiries = r.json().get("data") or []
    print(f"  expiries returned: {len(expiries)}; first/last: {expiries[:2]} .. {expiries[-2:]}")
    if not expiries:
        return
    expiry = sorted(expiries)[-1]
    r = s.get(f"{base}/future/contract", params={"instrument_key": eq_key, "expiry_date": expiry}, timeout=30)
    print(f"  GET future/contract expiry={expiry} -> {r.status_code}: {short(r.text)}")
    if not r.ok:
        return
    contracts = r.json().get("data") or []
    if not contracts:
        return
    ek = contracts[0].get("instrument_key")
    print(f"  expired futures instrument_key: {ek}")
    to_d = date.fromisoformat(expiry)
    url = f"{base}/historical-candle/{ek}/5minute/{to_d.isoformat()}/{(to_d - timedelta(days=3)).isoformat()}"
    r = s.get(url, timeout=30)
    print(f"  GET historical-candle 5minute (3 days before expiry) -> {r.status_code}: {short(r.text, 500)}")
    if r.ok:
        candles = (r.json().get("data") or {}).get("candles") or []
        print(f"  candles: {len(candles)}; first: {candles[:1]}; last: {candles[-1:]}")


def main() -> None:
    picks = pd.read_csv("research/journal_picks.csv")
    client = UpstoxClient(os.environ["UPSTOX_ACCESS_TOKEN"])
    master = instrument_master()
    fo = data.fetch_fo_eligible_symbols(client)
    print(f"F&O universe {len(fo)}; journal-pick symbols {picks['symbol'].nunique()}")
    probe_sectors(set(fo), set(picks["symbol"]))
    probe_bhavcopy()
    probe_live_futures(client, master)
    probe_expired(client)
    print("\nProbe finished.")


if __name__ == "__main__":
    main()
