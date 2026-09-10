#!/usr/bin/env python3
"""One-off diagnostic: verify the F&O (stock futures) data assumptions
behind data.build_futures_instrument_map / upstox_client.fetch_fo_instrument_master
before building a real OI-based strategy on top of them.

Not part of the regular signals/backtest pipeline - run manually
(.github/workflows/debug_futures.yml) whenever those assumptions need
re-checking, e.g. if Upstox changes the instrument master's shape.

Answers three open questions:
  1. Does `name` (not `tradingsymbol`) actually hold the plain underlying
     symbol for FUTSTK rows, matching our Nifty 500 list?
  2. Does historical-candle actually return a populated open_interest
     column for a futures instrument_key (equities never have one)?
  3. How far back does that history actually go? A stock futures contract
     only exists ~3 months before expiring, and the instrument master only
     lists currently-live contracts (confirmed: 629 FUTSTK rows / ~210
     unique underlyings = ~3 rows/stock, i.e. near/next/far month only,
     no expired ones) - so if a "years=2" request comes back cut off at
     that contract's own listing date, multi-year backtesting the way the
     other three strategies use isn't straightforwardly possible from this
     data source alone (no way to discover an expired contract's
     instrument_key to fetch its history either).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, runtime, universe  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

SAMPLE_SYMBOLS = ["RELIANCE", "SBIN", "TCS", "HDFCBANK", "INFY"]


def main() -> None:
    runtime.setup_logging()
    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    symbols = universe.fetch_nifty500_symbols()

    fo_map = data.build_futures_instrument_map(client, symbols)
    print(f"\n=== F&O universe: {len(fo_map)}/{len(symbols)} Nifty 500 symbols have a current futures contract ===")
    for sym in SAMPLE_SYMBOLS:
        print(f"{sym}: {fo_map.get(sym, 'NOT FOUND')}")

    for sym in SAMPLE_SYMBOLS:
        info = fo_map.get(sym)
        if info is None:
            print(f"\n{sym}: no current futures contract found, skipping candle check")
            continue
        df = client.get_daily_history(info["instrument_key"], years=2)
        print(f"\n=== {sym} futures ({info['instrument_key']}, expiry {info['expiry'].date()}) ===")
        print(f"Rows: {len(df)}, columns: {list(df.columns)}")
        if df.empty:
            continue
        print(f"Date range: {df.index.min().date()} -> {df.index.max().date()}")
        if "open_interest" in df.columns:
            print(f"open_interest non-null: {df['open_interest'].notna().sum()}/{len(df)}")
            print(df[["close", "volume", "open_interest"]].tail(10))
        else:
            print(
                "NO open_interest column in the response - either historical-candle doesn't "
                "include OI for this instrument, or the response's candle-row length doesn't "
                "match CANDLE_COLUMNS' assumed field order/count."
            )


if __name__ == "__main__":
    main()
