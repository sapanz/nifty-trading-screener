#!/usr/bin/env python3
"""Research: can a morning screen find the F&O stocks a discretionary option
buyer ends up trading? See signals/pickfinder.py for the question and method.

  PICKFINDER_MONTHS=24 python scripts/run_pickfinder_research.py
  # smoke test of the Upstox 5-minute fetch on a few symbols first:
  PICKFINDER_MONTHS=3 PICKFINDER_SYMBOL_LIMIT=5 python scripts/run_pickfinder_research.py

Intended to be run through .github/workflows/pickfinder_research.yml, which does
the Upstox TOTP login. Everything is printed to the Actions log; the feature
table is uploaded as an artifact. Nothing is sent to Telegram.

Per-symbol feature tables are cached in .cache/pickfinder (keyed by symbol,
window and FEATURES_VERSION - bump it when session_features changes) so a
re-run that only changes the analysis doesn't re-fetch over an hour of
candles. Sector and prior-day OI features (NSE data, signals/nse_fo.py) are
joined on top, so changing them never needs a re-fetch; if either source is
unreachable the run carries on without it and says so.
"""
import json
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, nse_fo, pickfinder, runtime, universe  # noqa: E402
from signals.calendar_utils import ist_today  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

FEATURES_VERSION = 2
PICKS_CSV = "research/journal_picks.csv"
RUN_CONFIG = "research/run_config.json"
CACHE_DIR = ".cache/pickfinder"
OI_CACHE_DIR = ".cache/nse_fo"
OI_LOOKBACK_DAYS = 10                    # prior-day OI, plus a 3-trading-day change, needs a little history before the window
FEATURES_OUT = "pickfinder_features.csv.gz"


def fetch_5min(client: UpstoxClient, key: str, start: date, end: date) -> pd.DataFrame:
    """5-minute candles for [start, end] in 28-day chunks (Upstox caps one
    request at about a month for 1-15 minute candles)."""
    frames, chunk_end = [], end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - timedelta(days=data.INTRADAY_CHUNK_DAYS - 1))
        df = client.get_candles_between(key, "minutes", 5, chunk_start, chunk_end)
        if not df.empty:
            frames.append(df)
        client.throttle()
        chunk_end = chunk_start - timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.concat(frames).sort_index().loc[lambda d: ~d.index.duplicated(keep="last")]


def describe(symbol: str, bars: pd.DataFrame) -> str:
    per_day = bars.groupby(bars.index.normalize()).size()
    return (f"  {symbol}: {len(bars)} bars over {len(per_day)} sessions, {bars.index[0]} -> {bars.index[-1]}, "
            f"{per_day.median():.0f} bars/session median (full = 75), first bar of day {bars.index[0].time()}")


def setting(env_name: str, key: str, default):
    """A manual-dispatch input (env var) wins; otherwise research/run_config.json,
    which is how a push to the research branch configures its own run."""
    if os.environ.get(env_name):
        return os.environ[env_name]
    if os.path.exists(RUN_CONFIG):
        with open(RUN_CONFIG) as f:
            value = json.load(f).get(key)
        if value not in (None, ""):
            return value
    return default


def load_sector_map() -> dict:
    """NSE industry per symbol; {} (and a loud line in the log) if unreachable."""
    try:
        sectors = universe.fetch_sector_map()
        print(f"Sector map: {len(sectors)} symbols in {len(set(sectors.values()))} industries")
        return sectors
    except Exception as exc:  # noqa: BLE001 - the run is still useful without sector features
        print(f"SECTOR FEATURES UNAVAILABLE: {exc}")
        return {}


def load_oi_table(start: date, end: date) -> pd.DataFrame:
    """NSE F&O bhavcopy OI features for the window; empty (and a loud line) if unreachable."""
    try:
        table = nse_fo.build_oi_table(start - timedelta(days=OI_LOOKBACK_DAYS), end, OI_CACHE_DIR)
        print(f"F&O bhavcopy OI table: {len(table)} rows over {table['date'].nunique() if len(table) else 0} trading days")
        return table
    except Exception as exc:  # noqa: BLE001
        print(f"PRIOR-DAY OI FEATURES UNAVAILABLE: {exc}")
        return pd.DataFrame()


def feature_coverage(df: pd.DataFrame) -> str:
    """How many feature rows actually carry the sector / OI signals (the rest rank neutral)."""
    lines = []
    for label, col in (("sector peers", "sec_ret_atr"), ("prior-day futures OI", "oi_dir"), ("prior-day put/call OI", "pcr")):
        lines.append(f"{label}: " + (f"{df[col].notna().mean():.0%} of stock-days" if col in df.columns else "NOT AVAILABLE this run"))
    return "feature coverage -> " + "; ".join(lines)


def main() -> None:
    runtime.setup_logging()
    months = int(setting("PICKFINDER_MONTHS", "months", 24))
    limit = int(setting("PICKFINDER_SYMBOL_LIMIT", "symbol_limit", 0))
    end_raw = setting("PICKFINDER_END", "end_date", "")
    end = date.fromisoformat(end_raw) if end_raw else ist_today()
    start = end - timedelta(days=round(months * 30.5))
    os.makedirs(CACHE_DIR, exist_ok=True)
    for stale in os.listdir(CACHE_DIR):                       # feature tables from an older FEATURES_VERSION are dead weight
        if not stale.startswith(f"v{FEATURES_VERSION}_"):
            os.remove(os.path.join(CACHE_DIR, stale))

    picks = pd.read_csv(PICKS_CSV)
    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    fo = sorted(data.fetch_fo_eligible_symbols(client))
    full_map = client.fetch_instrument_map()
    wanted = fo[:limit] if limit else sorted(set(fo) | set(picks["symbol"]))
    instrument_map = {s: full_map[s] for s in wanted if s in full_map}
    print(f"Window {start} -> {end}. Universe: {len(instrument_map)} symbols "
          f"({len(fo)} currently in F&O; journal picks add {len(set(picks['symbol']) - set(fo))} not in today's list). "
          f"No instrument key for: {sorted(set(wanted) - set(instrument_map))[:30]}")

    sector_map = load_sector_map()
    oi_table = load_oi_table(start, end)

    frames, failures, described, earliest = [], [], 0, None
    started = time.time()
    for i, (symbol, key) in enumerate(instrument_map.items(), start=1):
        cache = f"{CACHE_DIR}/v{FEATURES_VERSION}_{symbol}_{start}_{end}.pkl.gz".replace("&", "and")
        try:
            if os.path.exists(cache):
                feats = pd.read_pickle(cache)
            else:
                bars = fetch_5min(client, key, start, end)
                if bars.empty:
                    raise RuntimeError("no 5-minute candles returned")
                if described < 3:
                    print(describe(symbol, bars))
                    described += 1
                earliest = bars.index[0] if earliest is None else min(earliest, bars.index[0])
                feats = pickfinder.session_features(symbol, bars)
                feats.to_pickle(cache)
            if not feats.empty:
                frames.append(feats)
        except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't kill a long run
            failures.append(symbol)
            print(f"  FAILED {symbol}: {exc}")
            if i == 10 and len(failures) >= 8:
                raise RuntimeError(f"{len(failures)}/10 symbols failed - Upstox looks like it is rejecting the 5-minute requests: {failures}")
        if i % 10 == 0 or i == len(instrument_map):
            print(f"  progress {i}/{len(instrument_map)} symbols, {(time.time() - started) / 60:.1f} min elapsed")

    if not frames:
        raise RuntimeError("No features built for any symbol")
    print(f"\nEarliest 5-minute bar actually returned this run: {earliest}")
    df = pd.concat(frames, ignore_index=True)
    print(f"Feature rows: {len(df)} across {df['symbol'].nunique()} symbols, "
          f"{df['date'].nunique()} sessions ({df['date'].min().date()} -> {df['date'].max().date()}); failed: {failures}")

    if sector_map:
        df = pickfinder.add_sector(df, sector_map)
    if not oi_table.empty:
        df = nse_fo.attach_prior_day_oi(df, oi_table)
    df = pickfinder.add_scores(df)
    df = pickfinder.attach_picks(df, picks)
    print("\n=== Coverage ===\n" + pickfinder.coverage(picks, df))
    print(feature_coverage(df))
    print("\n=== Price and volume: where the journal's picks sit in the morning (percentile vs the day's F&O stocks) ===\n" + pickfinder.pick_signature(df))
    print("\n=== Volume flow, sector and prior-day OI: the picks vs any stock on the same days ===\n" + pickfinder.pick_context(df))
    print("\n=== Untuned top-K screens, one per score variant: recall of the journal's picks, and what each screen itself would have earned ===\n"
          f"(equal-weight percentile-rank sums fixed in advance; variants: {', '.join(pickfinder.VARIANTS)}; all of them are shown)\n" + pickfinder.screen_test(df))
    df.drop(columns=["r_day"], errors="ignore").to_csv(FEATURES_OUT, index=False, compression="gzip", float_format="%.5g")
    print(f"\nWrote {FEATURES_OUT}")


if __name__ == "__main__":
    main()
