#!/usr/bin/env python3
"""One-off diagnostic: plot a symbol's price alongside its Chaikin Money
Flow (Money Flow Accumulation's indicator) so it can actually be *seen*,
not just gated on inside a scan() function.

Not part of the regular signals/backtest pipeline - run manually via
.github/workflows/plot_money_flow.yml, passing the symbol as a workflow
input. Uses matplotlib, installed as a one-off step in that workflow
rather than added to requirements.txt for a throwaway visualization tool.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from signals import config, data, runtime, universe  # noqa: E402
from signals.indicators import add_money_flow_volume, add_sma  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402


def main() -> None:
    runtime.setup_logging()
    symbol = os.environ.get("CHECK_SYMBOL", "").strip().upper()
    if not symbol:
        raise SystemExit("CHECK_SYMBOL env var is required")

    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    universe.fetch_nifty500_symbols()  # side-effect free; just confirms NSE list is reachable
    instrument_map = data.build_instrument_map(client, [symbol])
    if symbol not in instrument_map:
        raise SystemExit(f"No Upstox instrument_key found for {symbol}")

    daily_data = data.fetch_daily(client, instrument_map)
    if symbol not in daily_data:
        raise SystemExit(f"Failed to fetch daily data for {symbol}")

    df = daily_data[symbol].copy()
    add_sma(df, config.SMA_LONG)
    add_sma(df, config.SMA_SWING)
    add_money_flow_volume(df, config.MFV_LOOKBACK)
    cmf_col = f"cmf{config.MFV_LOOKBACK}"

    plot_df = df.tail(180)  # last ~180 trading days - enough to see recent structure without crowding the chart

    fig, (ax_price, ax_cmf) = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )

    ax_price.plot(plot_df.index, plot_df["close"], color="black", linewidth=1.2, label="Close")
    ax_price.plot(plot_df.index, plot_df[f"sma{config.SMA_LONG}"], color="tab:blue", linewidth=1, label=f"SMA{config.SMA_LONG}")
    ax_price.plot(plot_df.index, plot_df[f"sma{config.SMA_SWING}"], color="tab:orange", linewidth=1, label=f"SMA{config.SMA_SWING}")
    ax_price.set_title(f"{symbol} - price vs Chaikin Money Flow (CMF{config.MFV_LOOKBACK})")
    ax_price.legend(loc="upper left")
    ax_price.grid(alpha=0.3)

    colors = ["tab:green" if v >= 0 else "tab:red" for v in plot_df[cmf_col].fillna(0)]
    ax_cmf.bar(plot_df.index, plot_df[cmf_col], color=colors, width=1.0)
    ax_cmf.axhline(0, color="black", linewidth=0.8)
    ax_cmf.axhline(config.CMF_MIN_VALUE, color="gray", linewidth=0.8, linestyle="--")
    ax_cmf.set_ylabel(f"CMF{config.MFV_LOOKBACK}")
    ax_cmf.grid(alpha=0.3)

    fig.autofmt_xdate()
    fig.tight_layout()
    out_path = f"{symbol}_money_flow.png"
    fig.savefig(out_path, dpi=150)
    print(f"Wrote {out_path}")

    row = df.iloc[-1]
    print(f"Latest ({row.name.date()}): close={row['close']:.2f} CMF{config.MFV_LOOKBACK}={row[cmf_col]:+.3f}")


if __name__ == "__main__":
    main()
