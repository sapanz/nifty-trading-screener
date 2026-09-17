#!/usr/bin/env python3
"""One-off diagnostic: scan the whole Nifty 500 universe for stocks whose
daily Chaikin Money Flow (CMF) crossed zero on the most recent candle -
either direction (negative-to-positive, i.e. distribution turning into
accumulation, or the reverse) - and print the list.

Not part of the regular signals/backtest pipeline (Money Flow Accumulation
itself only ever cares about the negative-to-positive case, and gates on
several other conditions - trend, support test, candle quality - on top
of it; this is the raw crossover alone, unfiltered, across every symbol).
Run manually via .github/workflows/cmf_crossover.yml.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from signals import config, data, runtime, universe  # noqa: E402
from signals.indicators import add_money_flow_volume  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

CMF_COL = f"cmf{config.MFV_LOOKBACK}"


def main() -> None:
    runtime.setup_logging()
    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    symbols = universe.fetch_nifty500_symbols()
    instrument_map = data.build_instrument_map(client, symbols)
    daily_data = data.fetch_daily(client, instrument_map)

    bullish: list[tuple[str, float, float]] = []  # (symbol, prev_cmf, today_cmf)
    bearish: list[tuple[str, float, float]] = []
    skipped = 0

    for symbol, raw_df in daily_data.items():
        df = raw_df.copy()
        if len(df) < config.MFV_LOOKBACK + 2:
            skipped += 1
            continue
        add_money_flow_volume(df, config.MFV_LOOKBACK)
        today_cmf = df[CMF_COL].iloc[-1]
        prev_cmf = df[CMF_COL].iloc[-2]
        if pd.isna(today_cmf) or pd.isna(prev_cmf):
            skipped += 1
            continue
        if prev_cmf < 0 <= today_cmf:
            bullish.append((symbol, float(prev_cmf), float(today_cmf)))
        elif prev_cmf >= 0 > today_cmf:
            bearish.append((symbol, float(prev_cmf), float(today_cmf)))

    bullish.sort(key=lambda t: t[2] - t[1], reverse=True)  # biggest swing first
    bearish.sort(key=lambda t: t[1] - t[2], reverse=True)

    print(f"\nScanned {len(daily_data)} symbols ({skipped} skipped for insufficient history)")
    print(f"\n=== CMF{config.MFV_LOOKBACK} turned POSITIVE (negative -> positive) - {len(bullish)} stocks ===")
    for symbol, prev_cmf, today_cmf in bullish:
        print(f"  {symbol}: {prev_cmf:+.3f} -> {today_cmf:+.3f}")

    print(f"\n=== CMF{config.MFV_LOOKBACK} turned NEGATIVE (positive -> negative) - {len(bearish)} stocks ===")
    for symbol, prev_cmf, today_cmf in bearish:
        print(f"  {symbol}: {prev_cmf:+.3f} -> {today_cmf:+.3f}")


if __name__ == "__main__":
    main()
