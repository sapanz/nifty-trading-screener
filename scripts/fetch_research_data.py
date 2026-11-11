#!/usr/bin/env python3
"""Download the raw history a research session needs and publish it to the `research-data` branch (one commit, force-pushed):

  equity_daily.csv.gz    symbol,date,open,high,low,close,volume            RD_YEARS of daily candles, every F&O stock (+ NIFTY)
  futures_daily.csv.gz   symbol,expiry,date,open,high,low,close,volume,oi  the current (up to 3) futures contracts of each stock
  intraday30.csv.gz      symbol,ts,open,high,low,close,volume              30-minute candles, RD_INTRADAY_QUARTERS quarters back (0 = skip)

Run through .github/workflows/research_data.yml (Upstox TOTP login first). The branch is temporary - delete it when the research is done.
"""
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, runtime  # noqa: E402
from signals.calendar_utils import ist_today  # noqa: E402
from signals.publish import publish_directory  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

OUT = Path("research_out")
BRANCH = "research-data"
NIFTY_KEY = "NSE_INDEX|Nifty 50"


def _long(frames: dict, extra: dict | None = None) -> pd.DataFrame:
    parts = []
    for name, df in frames.items():
        d = df.reset_index().rename(columns={"timestamp": "ts", "index": "ts"})
        d.insert(0, "symbol", name if extra is None else extra[name][0])
        if extra is not None:
            d.insert(1, "expiry", extra[name][1])
        parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def main() -> None:
    runtime.setup_logging()
    years = int(os.environ.get("RD_YEARS", "6"))
    quarters = int(os.environ.get("RD_INTRADAY_QUARTERS", "8"))
    limit = int(os.environ.get("RD_SYMBOL_LIMIT", "0"))
    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    contracts = client.fetch_futures_contracts(per_symbol=3)
    symbols = sorted(contracts)[:limit] if limit else sorted(contracts)
    equity_map = data.build_instrument_map(client, symbols)
    OUT.mkdir(exist_ok=True)

    equity = data._fetch_history(equity_map, fetch_one=lambda key: client.get_daily_history(key, years=years), throttle=client.throttle, label="daily")
    try:
        equity["NIFTY"] = client.get_daily_history(NIFTY_KEY, years=years)
    except Exception as exc:  # noqa: BLE001
        print(f"index skipped: {exc}")
    eq = _long(equity)
    eq.to_csv(OUT / "equity_daily.csv.gz", index=False)
    print(f"equity: {len(equity)} symbols, {len(eq)} rows, {eq['ts'].min()} to {eq['ts'].max()}")

    fut, meta = {}, {}
    for symbol in symbols:
        for expiry, key in contracts[symbol]:
            try:
                df = client.get_daily_history(key, years=1)
                if not df.empty:
                    fut[key] = df
                    meta[key] = (symbol, expiry.isoformat())
            except Exception as exc:  # noqa: BLE001
                print(f"futures {symbol} {expiry} skipped: {exc}")
            client.throttle()
    fu = _long(fut, meta)
    fu.to_csv(OUT / "futures_daily.csv.gz", index=False)
    print(f"futures: {len(fut)} contracts, {len(fu)} rows")

    rows = 0
    if quarters:
        today, parts, failed = ist_today(), [], 0
        for n, (symbol, key) in enumerate(equity_map.items(), start=1):
            for q in range(quarters):
                to_date = today - timedelta(days=85 * q)
                try:
                    df = client.get_candles_between(key, "minutes", 30, to_date - timedelta(days=84), to_date)
                    if not df.empty:
                        d = df.reset_index().rename(columns={"timestamp": "ts"})[["ts", "open", "high", "low", "close", "volume"]]
                        d.insert(0, "symbol", symbol)
                        parts.append(d)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    if failed <= 5:
                        print(f"intraday {symbol} q{q} skipped: {exc}")
                client.throttle()
            if n == 5 and failed >= 5 * quarters:
                print("intraday: every early request failed - skipping the rest")
                break
        if parts:
            intra = pd.concat(parts, ignore_index=True).drop_duplicates(["symbol", "ts"])
            intra.to_csv(OUT / "intraday30.csv.gz", index=False)
            rows = len(intra)
        print(f"intraday 30-minute: {rows} rows, {failed} failed requests")

    (OUT / "meta.json").write_text(json.dumps({"asof": ist_today().isoformat(), "years": years, "equity_symbols": len(equity), "futures_contracts": len(fut),
                                               "intraday_rows": rows}))
    token, repository = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if token and repository:
        publish_directory(str(OUT), repository, token, BRANCH, f"research data {ist_today().isoformat()}")
        print(f"published to {BRANCH}")


if __name__ == "__main__":
    main()
