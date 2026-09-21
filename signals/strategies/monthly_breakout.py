"""Monthly all-time-high breakout strategy (runs on the last trading day of the month).

A stock qualifies when, on the monthly timeframe:
  - this month's close is above every prior month's close (a fresh
    closing-basis all-time high, within the history that was fetched)
  - the breakout candle is bullish (closed above its own open) and closed
    properly (in the top 20% of its own range, i.e. a small upper wick) -
    without this, a month that gapped up hard intramonth and then faded
    back down still counts as a fresh ATH on a closing basis alone, even
    though it closed red
  - volume was elevated

Each result also reports how long (in months) the stock spent below its
old high before finally breaking out, and the list is sorted with the
longest-dormant breakouts first - those tend to be the most explosive.
A breakout within MONTHLY_MIN_GAP_MONTHS of the prior all-time high is
excluded entirely - too soon after the old high to be a genuine breakout
out of a real base, not just short-term noise.

No MIN_REWARD_RISK_RATIO gate here, unlike Weekly Range Breakout - tried
and reverted (see git history): a 5-year backtest showed it cut PF
1.66 -> 1.45 (530 -> 190 signals), removing more net-positive trades than
bad ones. A fresh-ATH breakout apparently carries enough of its own
statistical edge that a snapshot reward:risk ratio doesn't capture well -
unlike Weekly Range Breakout, where the same gate measurably helped
(PF 1.34 -> 1.72).
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, is_bullish, is_proper_close, is_volume_candle
from signals.models import Signal

VOL_COL = f"avg_vol{config.MONTHLY_VOLUME_LOOKBACK}"


def _format_gap(months: int) -> str:
    years, rem_months = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years}y")
    if rem_months or not years:
        parts.append(f"{rem_months}m")
    return " ".join(parts)


def scan(monthly_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []
    min_rows = config.MONTHLY_VOLUME_LOOKBACK + 2

    for symbol, raw_df in monthly_data.items():
        df = raw_df.copy()
        if len(df) < min_rows:
            continue

        add_avg_volume(df, config.MONTHLY_VOLUME_LOOKBACK)
        row = df.iloc[-1]
        if pd.isna(row.get(VOL_COL)):
            continue

        prior = df.iloc[:-1]
        ath_prior = float(prior["close"].max())
        ath_date = prior["close"].idxmax()

        if not row["close"] > ath_prior:
            continue
        if not is_bullish(row):
            continue  # a gap-up-then-fade month can still close at a fresh ATH while red
        if not is_proper_close(row):
            continue
        if not is_volume_candle(row, VOL_COL, config.MONTHLY_VOLUME_MULTIPLIER):
            continue

        current_date = df.index[-1]
        months_gap = (current_date.year - ath_date.year) * 12 + (current_date.month - ath_date.month)
        if months_gap <= config.MONTHLY_MIN_GAP_MONTHS:
            continue  # too soon after the old high - noise, not a genuine breakout

        entry = float(row["close"])
        stop_loss = float(ath_prior * (1 - config.SL_BUFFER))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        vol_ratio = float(row["volume"] / row[VOL_COL])
        targets = [round(entry * (1 + pct), 2) for pct in config.ATH_BREAKOUT_TARGET_PCTS]

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=months_gap,
                note=(
                    f"Prior ATH {ath_prior:.2f} ({ath_date.strftime('%b %Y')}) | "
                    f"Breakout after {_format_gap(months_gap)} | Vol {vol_ratio:.1f}x avg"
                ),
                extra={"months_gap": months_gap, "vol_ratio": round(vol_ratio, 2)},
                candle_date=current_date.date(),
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
