#!/usr/bin/env python3
"""One-off diagnostic: check a single symbol against Daily Swing's actual
scan() conditions, showing which conditions pass/fail (and by how much)
instead of just a yes/no - useful for answering "how is <stock> looking
for swing right now" without waiting for it to show up in a live scan.

Not part of the regular signals/backtest pipeline - run manually via
.github/workflows/check_stock.yml, passing the symbol as a workflow input.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from signals import config, data, runtime, universe  # noqa: E402
from signals.indicators import (  # noqa: E402
    add_avg_volume,
    add_bollinger_bands,
    add_rsi,
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
    weekly_data = data.fetch_weekly_history(client, instrument_map)
    if symbol not in daily_data:
        raise SystemExit(f"Failed to fetch daily data for {symbol}")

    df = daily_data[symbol].copy()
    print(f"\n=== {symbol} vs Daily Swing (SMA{SMA_SWING}/BB Confluence) ===")
    print(f"Latest candle: {df.index[-1].date()}  O={df['open'].iloc[-1]:.2f} H={df['high'].iloc[-1]:.2f} "
          f"L={df['low'].iloc[-1]:.2f} C={df['close'].iloc[-1]:.2f} V={df['volume'].iloc[-1]:.0f}")

    if len(df) < config.SMA_LONG + 2:
        print(f"FAIL: only {len(df)} daily candles available, need {config.SMA_LONG + 2}+")
        return

    weekly_df = weekly_data.get(symbol)
    weekly_ok = False
    if weekly_df is None or weekly_df.empty:
        print("FAIL: no weekly data available")
    else:
        weekly_df = weekly_df.copy()
        add_sma(weekly_df, config.BREAKOUT_TREND_SMA)
        weekly_ok = is_sma_rising(weekly_df, f"sma{config.BREAKOUT_TREND_SMA}")
        wk_col = f"sma{config.BREAKOUT_TREND_SMA}"
        wk_val = weekly_df[wk_col].iloc[-1] if not pd.isna(weekly_df[wk_col].iloc[-1]) else None
        print(f"{'PASS' if weekly_ok else 'FAIL'}: weekly SMA{config.BREAKOUT_TREND_SMA} rising"
              f"{f' (currently {wk_val:.2f})' if wk_val is not None else ' (not enough weekly history)'}")

    add_sma(df, config.SMA_LONG)
    add_sma(df, SMA_SWING)
    add_bollinger_bands(df)
    add_avg_volume(df, config.VOLUME_LOOKBACK)
    add_rsi(df)
    row = df.iloc[-1]
    prev_row = df.iloc[-2]

    sma_long_col = f"sma{config.SMA_LONG}"
    sma_swing_col = f"sma{SMA_SWING}"

    if pd.isna(row.get(sma_long_col)) or pd.isna(row.get(sma_swing_col)) or pd.isna(row.get("bb_lower")):
        print("FAIL: not enough daily history for SMA200/SMA{}/Bollinger yet".format(SMA_SWING))
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

    avg_vol_val = row.get(f"avg_vol{config.VOLUME_LOOKBACK}")
    vol_ratio = float(row["volume"] / avg_vol_val) if pd.notna(avg_vol_val) and avg_vol_val > 0 else None
    vol_ok = vol_ratio is not None and vol_ratio >= config.DAILY_SWING_MIN_VOL_RATIO
    print(f"{'PASS' if vol_ok else 'FAIL'}: volume ratio {vol_ratio:.2f}x average (min {config.DAILY_SWING_MIN_VOL_RATIO}x)"
          if vol_ratio is not None else "FAIL: no average-volume data yet")

    dist_from_sma200_pct = (row["close"] / row[sma_long_col] - 1) * 100
    dist_ok = dist_from_sma200_pct >= config.DAILY_SWING_MIN_DIST_FROM_SMA200_PCT
    print(f"{'PASS' if dist_ok else 'FAIL'}: {dist_from_sma200_pct:.1f}% above SMA{config.SMA_LONG} "
          f"(min {config.DAILY_SWING_MIN_DIST_FROM_SMA200_PCT}%)")

    rsi_val = row.get("rsi14")
    if pd.notna(rsi_val):
        print(f"(diagnostic only) RSI14: {float(rsi_val):.1f}")

    all_conditions = [weekly_ok, above_200, swing_rising, support_test, proper_close, bullish, confluence_ok, bb_touch, vol_ok, dist_ok]
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
