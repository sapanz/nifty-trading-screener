"""Weekly Value Stocks Breakout: a fundamental value screen
(signals/value_universe.py, scraping screener.in - VALUE_SCREEN_QUERY in
config.py: profit growth > 25%, debt/equity < 0.5, market cap > 5000 Cr)
gates which symbols are even considered, then a stock qualifies when, on
the weekly timeframe, this week's close is above the highest weekly close
in its entire available history - no minimum age on that prior high at
all. No bullish-candle check, no "proper close" (small upper wick) check,
no volume-confirmation check either.

There used to be a VALUE_BREAKOUT_MIN_GAP_WEEKS floor (the high being
broken had to be at least ~52 weeks old, i.e. a genuinely dormant-value
breakout, not a stock already mid-trend) - removed per explicit
direction, after it turned out to be excluding real, fundamentally-
qualifying stocks for the wrong reason. Engineers India (ENGINERSIN) is
the case that surfaced this: it clears the fundamental screen
comfortably (>25% profit growth, near-zero debt, market cap well past
5000 Cr) but had been up ~57% over the trailing year - a stock making
frequent new highs sets each new one only weeks after the last, so it can
never clear a 52-week-old-high requirement no matter how well it
otherwise fits the strategy's thesis. Removing the floor means a stock
already in a strong uptrend can now signal on every fresh closing high it
makes, not just a first breakout after a long dormant stretch - a real
behavior change (more frequent, possibly repeat, signals for trending
names in the value universe), not just a filter tweak.

Per explicit direction generally: this fundamentally-screened universe is
already small (a handful of signals over 5 years in the first real
backtest), so stacking technical filters on top of the fundamental one
just starves the strategy of trades further for a benefit that's unproven
at this sample size - unlike Monthly ATH Breakout and Price Action
Breakout, which lean on those filters precisely because their much larger
unfiltered universes can afford to trade off quantity for quality.

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
decision is made. The value-universe scraper (see signals/value_universe.py)
is confirmed working live against screener.in, but a backtest can only
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
from signals.indicators import add_avg_volume, add_sma
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

        current_date = df.index[-1]
        # No minimum-age gate on this anymore (see module docstring) -
        # weeks_gap is still computed and shown/sorted on, since "how old
        # was the high being broken" stays useful context even though it's
        # no longer a pass/fail condition.
        weeks_gap = int(round((current_date - prior_high_date).days / 7))

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
