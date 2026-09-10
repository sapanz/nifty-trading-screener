#!/usr/bin/env python3
"""One-off diagnostic: check a single symbol against Daily Swing's actual
scan() conditions, showing which conditions pass/fail (and by how much)
instead of just a yes/no - useful for answering "how is <stock> looking
for swing right now" without waiting for it to show up in a live scan.

Mirrors signals/strategies/daily_swing.py's scan() logic on `main` exactly
(no weekly-trend/volume/distance filters - those exist only on an
in-progress feature branch, not in the live strategy this checks).

Not part of the regular signals/backtest pipeline - run manually via
.github/workflows/check_stock.yml, passing the symbol as a workflow input.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from signals import config, data, runtime, universe  # noqa: E402
from signals.indicators import (  # noqa: E402
    add_bollinger_bands,
    add_sma,
    confluence_gap,
    is_above_sma,
    is_bullish,
    is_proper_close,
    is_sma_rising,
    is_support_test,
    upper_wick_ratio,
)
from signals.upstox_client import UpstoxClient

SMA_SWING = config.SMA_SWING


def main() -> None:
    runtime.setup_logging()
    symbol = os.environ.get("CHECK_SYMBOL", "").strip().upper()
    if not symbol:
        raise SystemExit("CHECK_SYMBOL env var is required")

    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    nifty500 = universe.fetch_nifty500_symbols()
    if symbol not in nifty500:
        print(f"NOTE: {symbol} is not in the Nifty 500 universe list this bot scans - checking anyway.")

    instrument_map = data.build_instrument_map(client, [symbol])
    if symbol not in instrument_map:
        raise SystemExit(f"No Upstox instrument_key found for {symbol}")

    daily_data = data.fetch_daily(client, instrument_map)
    if symbol not in daily_data:
        raise SystemExit(f"Failed to fetch daily data for {symbol}")

    df = daily_data[symbol].copy()
    print(f"\n=== {symbol} vs Daily Swing (SMA{SMA_SWING}/BB Confluence) ===")
    print(f"Latest candle: {df.index[-1].date()}  O={df['open'].iloc[-1]:.2f} H={df['high'].iloc[-1]:.2f} "
          f"L={df['low'].iloc[-1]:.2f} C={df['close'].iloc[-1]:.2f} V={df['volume'].iloc[-1]:.0f}")

    if len(df) < config.SMA_LONG + 2:
        print(f"FAIL: only {len(df)} daily candles available, need {config.SMA_LONG + 2}+")
        return

    add_sma(df, config.SMA_LONG)
    add_sma(df, SMA_SWING)
    add_bollinger_bands(df)
    row = df.iloc[-1]
    prev_row = df.iloc[-2]

    sma_long_col = f"sma{config.SMA_LONG}"
    sma_swing_col = f"sma{SMA_SWING}"

    if pd.isna(row.get(sma_long_col)) or pd.isna(row.get(sma_swing_col)) or pd.isna(row.get("bb_lower")):
        print(f"FAIL: not enough daily history for SMA{config.SMA_LONG}/SMA{SMA_SWING}/Bollinger yet")
        return

    above_200 = is_above_sma(row, sma_long_col)
    print(f"{'PASS' if above_200 else 'FAIL'}: close {row['close']:.2f} above SMA{config.SMA_LONG} "
          f"{row[sma_long_col]:.2f} ({(row['close'] / row[sma_long_col] - 1) * 100:+.1f}%)")

    swing_rising = is_sma_rising(df, sma_swing_col)
    print(f"{'PASS' if swing_rising else 'FAIL'}: SMA{SMA_SWING} rising (currently {row[sma_swing_col]:.2f}, "
          f"{config.SMA_SLOPE_LOOKBACK} candles ago {df[sma_swing_col].iloc[-1 - config.SMA_SLOPE_LOOKBACK]:.2f})")

    support_test = is_support_test(row, sma_swing_col, config.DAILY_SUPPORT_TOLERANCE)
    dist_low_to_sma = (row["low"] / row[sma_swing_col] - 1) * 100
    print(f"{'PASS' if support_test else 'FAIL'}: low {row['low']:.2f} tested SMA{SMA_SWING} support "
          f"(low is {dist_low_to_sma:+.1f}% from SMA{SMA_SWING}, need within +{config.DAILY_SUPPORT_TOLERANCE * 100:.0f}%) "
          f"and closed back above it ({row['close']:.2f} > {row[sma_swing_col]:.2f} = {row['close'] > row[sma_swing_col]})")

    proper_close = is_proper_close(row)
    bullish = is_bullish(row)
    print(f"{'PASS' if proper_close else 'FAIL'}: proper close, upper wick ratio {upper_wick_ratio(row):.2f} "
          f"(max {config.MAX_UPPER_WICK_RATIO})")
    print(f"{'PASS' if bullish else 'FAIL'}: bullish candle (close {row['close']:.2f} vs open {row['open']:.2f})")

    gap = confluence_gap(float(row[sma_swing_col]), float(row["bb_lower"]))
    confluence_ok = gap <= config.CONFLUENCE_TOLERANCE
    print(f"{'PASS' if confluence_ok else 'FAIL'}: SMA{SMA_SWING}/lowerBB confluence, gap {gap * 100:.2f}% "
          f"(SMA{SMA_SWING}={row[sma_swing_col]:.2f}, lowerBB={row['bb_lower']:.2f}, max {config.CONFLUENCE_TOLERANCE * 100:.0f}%)")

    bb_touch = row["low"] <= row["bb_lower"] * (1 + config.DAILY_SUPPORT_TOLERANCE)
    print(f"{'PASS' if bb_touch else 'FAIL'}: candle low also reached lower BB "
          f"({row['low']:.2f} vs {row['bb_lower']:.2f} * {1 + config.DAILY_SUPPORT_TOLERANCE:.2f})")

    all_conditions = [above_200, swing_rising, support_test, proper_close, bullish, confluence_ok, bb_touch]
    matched = all(all_conditions)
    print(f"\n{'>>> MATCHES Daily Swing right now <<<' if matched else '>>> Does NOT currently match Daily Swing <<<'}")

    if not matched:
        entry = float(row["high"])
        stop_loss = float(min(row["low"], prev_row["low"]))
        risk = entry - stop_loss
        if risk > 0:
            targets = [round(entry + risk * mult, 2) for mult in config.RISK_REWARD_TARGETS]
            print(f"(if every condition passed today, entry/SL/targets would be: "
                  f"entry={entry:.2f} SL={stop_loss:.2f} targets={targets})")


if __name__ == "__main__":
    main()
