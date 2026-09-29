"""Weekly Value Stocks Breakout: a fundamental value screen
(signals/value_universe.py, scraping screener.in - VALUE_SCREEN_QUERY in
config.py: profit growth > 25%, debt/equity < 0.5, market cap > 5000 Cr)
gates which symbols are even considered, then a stock qualifies when, on
the weekly timeframe:
  - this week's close is above the highest weekly close in at least
    VALUE_BREAKOUT_MIN_GAP_WEEKS candles (~1 year) - a genuinely old high,
    not just a recent rolling-window peak, so "multi-year breakout" is
    literal: the high being broken could be from many years back if the
    stock spent that whole time below it
  - the breakout candle is bullish (closed above its own open) and closed
    properly (in the top 20% of its own range, i.e. a small upper wick)
  - volume was elevated (WEEKLY_VOLUME_MULTIPLIER - the same convention
    Weekly Range Breakout uses)

The breakout condition mirrors Monthly ATH Breakout's mechanic (see that
module), just weekly-timeframe with a bounded (not literally all-time)
lookback, per explicit direction ("at least a year or multi-year breakout
with proper closing above previous high and also add volume
confirmation"). Unlike Monthly ATH Breakout, this doesn't need to check
being above the 200-period SMA - by the time a stock closes above a high
it hasn't touched in a year or more, on a proper close and real volume,
it's definitionally already well above any such SMA.

Entry is the breakout candle's own close (open-ended breakouts have no
prior resistance to set a resting buy-stop against). There is NO fixed
stop-loss price and NO profit target at all - per explicit direction, the
exit is entirely a trailing trend-following rule instead: stay in the
trade as long as the weekly close holds above its own BREAKOUT_TREND_SMA
(30-week SMA, the same convention Weekly Range Breakout uses for its own
trend filter), exiting the first week the close falls back below it - see
backtest.simulate_weekly_trailing_sma for the walk-forward mechanics
(including the next-trading-day fill delay: the Friday close that
triggers the exit can't be acted on until markets reopen). `Signal.
stop_loss` is still set, to the 30-week SMA's value *at signal time* -
purely informational (what the trailing level starts at / is reported as
in the Telegram message), not a hard exit price the backtest checks
directly, since the real exit condition recomputes the SMA every week
going forward.

Built to eventually replace Price Action Breakout (Weekly) - per explicit
direction, pending a backtest comparing the two; both run live until that
decision is made. Entirely unbacktested so far: the value-universe
scraper itself is unverified against the live screener.in site (see
signals/value_universe.py), and even once it works, a backtest can only
ever apply *today's* value-screen result uniformly across the whole
historical window - screener.in has no historical snapshot of past
fundamentals, so a stock that only recently started passing the screen
would incorrectly look like it always did (and vice versa) - the same
kind of approximation Price Action Breakout's F&O short leg already lives
with for a similar reason.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, add_sma, is_bullish, is_proper_close, is_volume_candle
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"
SMA_COL = f"sma{config.BREAKOUT_TREND_SMA}"


def scan(weekly_data: dict[str, pd.DataFrame], value_universe: set[str]) -> list[Signal]:
    """`value_universe` (see signals/value_universe.fetch_value_stock_symbols)
    is the fundamentally-screened set of symbols this strategy is even
    allowed to consider - a symbol outside it is skipped regardless of its
    price action, since combining that filter with the breakout trigger is
    this strategy's whole premise."""
    signals: list[Signal] = []
    min_rows = max(config.VOLUME_LOOKBACK, config.BREAKOUT_TREND_SMA) + 2

    for symbol, raw_df in weekly_data.items():
        if symbol not in value_universe:
            continue
        df = raw_df.copy()
        if len(df) < min_rows:
            continue

        add_avg_volume(df, config.VOLUME_LOOKBACK)
        add_sma(df, config.BREAKOUT_TREND_SMA)
        row = df.iloc[-1]
        if pd.isna(row.get(VOL_COL)) or pd.isna(row.get(SMA_COL)):
            continue

        prior = df.iloc[:-1]
        prior_high = float(prior["close"].max())
        prior_high_date = prior["close"].idxmax()

        if not row["close"] > prior_high:
            continue
        if not is_bullish(row):
            continue  # a gap-up-then-fade week can still close at a fresh high while red
        if not is_proper_close(row):
            continue
        if not is_volume_candle(row, VOL_COL, config.WEEKLY_VOLUME_MULTIPLIER):
            continue

        current_date = df.index[-1]
        weeks_gap = int(round((current_date - prior_high_date).days / 7))
        if weeks_gap < config.VALUE_BREAKOUT_MIN_GAP_WEEKS:
            continue  # too recent a high - not the "at least a year" breakout this strategy targets

        entry = float(row["close"])
        # Informational only - see module docstring. The real exit is the
        # trailing SMA condition backtest.simulate_weekly_trailing_sma
        # recomputes every future week, not this fixed number. No "entry
        # above its own SMA" check needed here: the SMA window's other 29
        # values are all part of `prior` (a superset the SMA window draws
        # from), so each is <= prior_high < entry already - entry is
        # therefore always the max of the 30-value window, which makes
        # entry > this SMA algebraically guaranteed, not something that
        # can fail at runtime.
        stop_loss = float(row[SMA_COL])

        vol_ratio = float(row["volume"] / row[VOL_COL])

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=[],  # no fixed target - held until the trailing SMA exit fires
                sort_key=weeks_gap,
                note=(
                    f"Prior high {prior_high:.2f} ({prior_high_date.date()}) | "
                    f"Breakout after {weeks_gap}w | Vol {vol_ratio:.1f}x avg | "
                    f"Trail: exit below {config.BREAKOUT_TREND_SMA}w SMA"
                ),
                extra={"weeks_gap": weeks_gap, "vol_ratio": round(vol_ratio, 2)},
                candle_date=current_date.date(),
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
