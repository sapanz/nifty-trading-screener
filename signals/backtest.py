"""Historical backtest of all active strategies over a lookback window.

For each historical date in the window, reconstructs what a strategy
would have signalled using *only* data available up to that date (the
existing scan() functions are reused unmodified - each is just handed a
dict of DataFrames sliced up to that date, exactly like a live run would
see, so there is no separate "backtest mode" to drift out of sync with
production behaviour and no lookahead bias).

Every generated signal is then walked forward on the real subsequent
daily price action to see whether it would have hit a target or its
stop-loss first - regardless of which timeframe produced the signal,
daily bars give the finest resolution available for that walk. If a
single day's range could have hit both the stop-loss and a target, the
stop-loss is assumed to trigger first (conservative, standard practice
without intraday data).

Some strategies (Daily Swing) set entry above the signal candle's own
close - a resting buy-stop order, not an immediate fill. simulate_forward
only starts tracking stop/target outcomes once a later day's high
actually reaches that entry price; a signal whose entry is never
subsequently reached is reported as "unfilled" rather than a real
win/loss/open trade.

Every entered trade's return is net of config.ROUND_TRIP_COST_PCT (STT +
stamp duty + exchange charges for a real Indian delivery trade) - a
mechanical screener's reported edge is meaningless if it can't survive
the costs a real trade actually pays.

run_backtest()'s Weekly Range Breakout and Monthly ATH Breakout scans
should each be fed their real native fetch (data.fetch_weekly_history /
data.fetch_monthly_ath_history) via the `weekly_data` / `monthly_data`
arguments, same as a live run sees - the daily-resample fallback used
when either is omitted won't exactly match live weekly candle
boundaries, and for monthly is capped at DAILY_HISTORY_YEARS rather than
genuinely deep history. See scripts/run_backtest.py for the live wiring.

Price Action Breakout runs twice - "price_action_breakout_daily" off
`daily_data` via price_action_breakout.scan() (immediate entry) and
"_weekly" off `weekly_data` via price_action_breakout.scan_retest()
(waits for a retest/reclaim) - two different functions, not the same one
called twice, since the two timeframes now run genuinely different entry
logic (see that module's docstring for why). Kept as two separate result
buckets rather than pooled together either way, since a daily-timeframe
base/breakout and a weekly one are different trades with different
holding periods, not the same signal at two resolutions. No monthly leg -
a 5-year backtest of an earlier version of this strategy showed it never
fired at all (too little monthly history per stock to form a base this
strict), and Monthly ATH Breakout already covers that timeframe.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from signals import config, data
from signals.models import Signal
from signals.strategies import daily_swing, monthly_breakout, price_action_breakout, weekly_breakout


def _net_return_pct(gross_return_pct: float) -> float:
    """Deduct the round-trip transaction cost from a gross price return."""
    return gross_return_pct - config.ROUND_TRIP_COST_PCT


def _gross_return_pct(direction: str, entry: float, exit_price: float) -> float:
    """% return on `entry`, direction-aware: a long profits as exit_price
    rises above entry, a short profits as it falls below - both expressed
    on the same entry-relative basis so long and short trades stay
    comparable in the same CSV/summary."""
    if direction == "short":
        return (1 - exit_price / entry) * 100
    return (exit_price / entry - 1) * 100

# Diagnostic-only fields a strategy can stamp onto a signal's `extra` dict
# (aside from months_gap, which has its own typed column) purely so a
# backtest's trade CSV carries enough to mine for what actually
# differentiates good and bad signals - vs guessing blind through repeated
# backtest round-trips. Not every strategy sets every key; write_csv leaves
# a column blank wherever a given trade's signal didn't set it.
DIAGNOSTIC_KEYS = [
    "vol_ratio", "confluence_gap_pct", "rsi14", "dist_from_sma200_pct",
    "extension_pct", "tightness_pct",
    "breakout_type", "base_candles", "base_start", "base_end",
]

CSV_FIELDS = [
    "strategy", "symbol", "direction", "signal_date", "entry", "stop_loss", "targets",
    "outcome", "exit_date", "exit_price", "return_pct", "holding_days", "months_gap",
    *DIAGNOSTIC_KEYS,
]


@dataclass
class TradeResult:
    strategy: str
    symbol: str
    signal_date: pd.Timestamp
    entry: float
    stop_loss: float
    targets: list[float]
    outcome: str  # "target1", "target2", ..., "stop_loss", or "open"
    exit_date: pd.Timestamp
    exit_price: float
    return_pct: float
    holding_days: int
    # "long" or "short" - see Signal.direction.
    direction: str = "long"
    # Monthly ATH Breakout only: months spent below the prior all-time high
    # before this breakout (signal.extra["months_gap"]); None for every
    # other strategy, which doesn't set it.
    months_gap: int | None = None
    # Everything else a strategy stamped onto signal.extra (see
    # DIAGNOSTIC_KEYS) - diagnostic-only, never used to gate a signal or
    # alter simulate_forward's own logic.
    diagnostics: dict = field(default_factory=dict)


def simulate_forward(
    strategy: str, signal: Signal, signal_date: pd.Timestamp, daily_df: pd.DataFrame,
    max_holding_days: int | None = None,
) -> TradeResult:
    """Walk the real daily price path after `signal_date` to see what happened.

    Direction-aware throughout: a long's resting entry sits above the
    signal close (a buy-stop) and its stop-loss sits below entry, its
    targets above; a short's are all mirrored (sell-stop below close,
    stop-loss above entry, targets below). Price Action Breakout's F&O-gated
    short leg is the one producer of `direction="short"` signals so far;
    every other strategy stays long-only, so this is exactly the original
    long-only logic in practice for them.

    `max_holding_days`, when set, force-exits at that day's close once
    this many TRADING days have elapsed since the entry actually filled
    (not calendar days, and not from signal_date - from the fill, since
    that's when the position actually opens) without either the stop or a
    target firing first. Every current strategy leaves this None
    (unlimited hold) - a forcing time-stop was tried for Weekly Range
    Breakout early on and reverted after a real, measured regression
    (PF 1.03 -> 0.60, see git history): it cut off winners that would have
    kept running, inferred from observing holding-period outcomes after
    the fact rather than a genuine upfront design requirement. This
    parameter exists for a different kind of case - an explicit "quick
    trade" design constraint for a fast-moving instrument, not a pattern
    mined from a backtest.
    """
    future = daily_df[daily_df.index > signal_date]
    months_gap = signal.extra.get("months_gap")
    diagnostics = {k: v for k, v in signal.extra.items() if k != "months_gap"}
    is_short = signal.direction == "short"

    as_of = daily_df[daily_df.index <= signal_date]
    signal_close = float(as_of["close"].iloc[-1]) if not as_of.empty else signal.entry
    resting_unfilled = (signal.entry < signal_close) if is_short else (signal.entry > signal_close)
    if resting_unfilled:
        # A resting stop order beyond the signal candle's own close isn't
        # filled yet - find the first later day that actually trades
        # through it, and don't track outcomes before that.
        filled = future[future["low"] <= signal.entry] if is_short else future[future["high"] >= signal.entry]
        if filled.empty:
            return TradeResult(
                strategy, signal.symbol, signal_date, signal.entry, signal.stop_loss, signal.targets,
                outcome="unfilled", exit_date=signal_date, exit_price=signal.entry,
                return_pct=0.0, holding_days=0, direction=signal.direction,
                months_gap=months_gap, diagnostics=diagnostics,
            )
        future = future[future.index >= filled.index[0]]

    for day_num, (dt, row) in enumerate(future.iterrows(), start=1):
        stopped = (row["high"] >= signal.stop_loss) if is_short else (row["low"] <= signal.stop_loss)
        if stopped:
            return TradeResult(
                strategy, signal.symbol, signal_date, signal.entry, signal.stop_loss, signal.targets,
                outcome="stop_loss", exit_date=dt, exit_price=signal.stop_loss,
                return_pct=_net_return_pct(_gross_return_pct(signal.direction, signal.entry, signal.stop_loss)),
                holding_days=(dt - signal_date).days, direction=signal.direction,
                months_gap=months_gap, diagnostics=diagnostics,
            )
        if is_short:
            hit = [i for i, target in enumerate(signal.targets) if row["low"] <= target]
        else:
            hit = [i for i, target in enumerate(signal.targets) if row["high"] >= target]
        if hit:
            idx = max(hit)  # the target farthest from entry actually reached that day
            exit_price = signal.targets[idx]
            return TradeResult(
                strategy, signal.symbol, signal_date, signal.entry, signal.stop_loss, signal.targets,
                outcome=f"target{idx + 1}", exit_date=dt, exit_price=exit_price,
                return_pct=_net_return_pct(_gross_return_pct(signal.direction, signal.entry, exit_price)),
                holding_days=(dt - signal_date).days, direction=signal.direction,
                months_gap=months_gap, diagnostics=diagnostics,
            )
        if max_holding_days is not None and day_num >= max_holding_days:
            exit_price = float(row["close"])
            return TradeResult(
                strategy, signal.symbol, signal_date, signal.entry, signal.stop_loss, signal.targets,
                outcome="time_exit", exit_date=dt, exit_price=exit_price,
                return_pct=_net_return_pct(_gross_return_pct(signal.direction, signal.entry, exit_price)),
                holding_days=(dt - signal_date).days, direction=signal.direction,
                months_gap=months_gap, diagnostics=diagnostics,
            )

    # Neither hit yet - still open as of the last available price.
    if not future.empty:
        last_date = future.index[-1]
        last_close = float(future["close"].iloc[-1])
    else:
        last_date = signal_date
        last_close = signal.entry
    return TradeResult(
        strategy, signal.symbol, signal_date, signal.entry, signal.stop_loss, signal.targets,
        outcome="open", exit_date=last_date, exit_price=last_close,
        return_pct=_net_return_pct(_gross_return_pct(signal.direction, signal.entry, last_close)),
        holding_days=(last_date - signal_date).days, direction=signal.direction,
        months_gap=months_gap, diagnostics=diagnostics,
    )


def _dates_in_window(datasets: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    all_dates: set[pd.Timestamp] = set()
    for df in datasets.values():
        all_dates.update(df.index)
    return sorted(d for d in all_dates if start <= d <= end)


def _scan_as_of(datasets: dict[str, pd.DataFrame], asof: pd.Timestamp) -> dict[str, pd.DataFrame]:
    sliced = {}
    for symbol, df in datasets.items():
        cut = df.loc[:asof]
        if not cut.empty:
            sliced[symbol] = cut
    return sliced


ALL_STRATEGIES = frozenset({
    "daily_swing", "weekly_breakout", "monthly_breakout",
    "price_action_breakout_daily", "price_action_breakout_weekly",
})


def run_backtest(
    daily_data: dict[str, pd.DataFrame],
    months: int,
    weekly_data: dict[str, pd.DataFrame] | None = None,
    monthly_data: dict[str, pd.DataFrame] | None = None,
    strategies: set[str] | None = None,
    short_eligible: set[str] | None = None,
) -> dict[str, list[TradeResult]]:
    """Backtest active strategies over the trailing `months` months.

    `weekly_data` and `monthly_data` drive the Weekly Range Breakout and
    Monthly ATH Breakout scans specifically. Pass each strategy's real
    native fetch (data.fetch_weekly_history / data.fetch_monthly_ath_history)
    so they see the same data a live run would; omitting either falls back
    to resampling `daily_data` (data.to_weekly / data.to_monthly), which is
    fine for a quick local backtest without the extra Upstox fetches but
    won't exactly match live candle boundaries (weekly) or reach past
    DAILY_HISTORY_YEARS (monthly).

    `strategies`, when given, limits which of ALL_STRATEGIES' keys actually
    get scanned - the skipped ones' scan() calls (and every historical
    date's worth of forward-walk simulation for them) never run at all,
    not just their output being discarded, so a run scoped to e.g. only
    the two Price Action Breakout legs is genuinely cheaper, not just
    quieter. None (the default) runs everything, same as before this
    parameter existed.

    `short_eligible` is passed straight through to both Price Action
    Breakout legs' `short_eligible` argument (see
    data.fetch_fo_eligible_symbols) - gating which symbols their short leg
    can fire on. Note this is necessarily *today's* F&O universe applied
    across the whole historical window, since Upstox's instrument master
    has no historical snapshot of which symbols had a futures contract on
    a given past date - a real but unavoidable approximation, same kind of
    limitation the old Futures OI Buildup strategy had with futures price
    history itself (see git history).
    """
    end = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(months=months)
    wanted = ALL_STRATEGIES if strategies is None else (strategies & ALL_STRATEGIES)

    if weekly_data is None:
        weekly_data = data.to_weekly(daily_data)
    if monthly_data is None:
        monthly_data = data.to_monthly(daily_data)

    results: dict[str, list[TradeResult]] = {key: [] for key in wanted}

    if "daily_swing" in wanted or "price_action_breakout_daily" in wanted:
        for asof in _dates_in_window(daily_data, start, end):
            sliced_daily = _scan_as_of(daily_data, asof)

            if "daily_swing" in wanted:
                daily_signals = daily_swing.scan(sliced_daily, _scan_as_of(weekly_data, asof))
                for signal in daily_signals:
                    results["daily_swing"].append(simulate_forward("daily_swing", signal, asof, daily_data[signal.symbol]))

            if "price_action_breakout_daily" in wanted:
                pa_daily_signals = price_action_breakout.scan(
                    sliced_daily,
                    pattern_min_lookback=config.PRICE_ACTION_PATTERN_MIN_LOOKBACK_DAILY,
                    pattern_max_lookback=config.PRICE_ACTION_PATTERN_MAX_LOOKBACK_DAILY,
                    volume_lookback=config.PRICE_ACTION_VOLUME_LOOKBACK_DAILY,
                    max_risk_pct=config.PRICE_ACTION_MAX_RISK_PCT_DAILY,
                    short_eligible=short_eligible,
                )
                for signal in pa_daily_signals:
                    results["price_action_breakout_daily"].append(
                        simulate_forward("price_action_breakout_daily", signal, asof, daily_data[signal.symbol])
                    )

    if "weekly_breakout" in wanted or "price_action_breakout_weekly" in wanted:
        for asof in _dates_in_window(weekly_data, start, end):
            sliced_weekly = _scan_as_of(weekly_data, asof)

            if "weekly_breakout" in wanted:
                for signal in weekly_breakout.scan(sliced_weekly):
                    # weekly_data may come from a separate fetch than daily_data (a
                    # different set of symbols can fail between two independent
                    # Upstox calls) - the exit walk-forward still needs daily bars,
                    # so skip a signal whose symbol didn't come back in daily_data
                    # rather than raising.
                    if signal.symbol not in daily_data:
                        continue
                    results["weekly_breakout"].append(simulate_forward("weekly_breakout", signal, asof, daily_data[signal.symbol]))

            if "price_action_breakout_weekly" in wanted:
                pa_weekly_signals = price_action_breakout.scan_retest(
                    sliced_weekly,
                    pattern_min_lookback=config.PRICE_ACTION_PATTERN_MIN_LOOKBACK_WEEKLY,
                    pattern_max_lookback=config.PRICE_ACTION_PATTERN_MAX_LOOKBACK_WEEKLY,
                    breakout_window=config.PRICE_ACTION_BREAKOUT_WINDOW_WEEKLY,
                    volume_lookback=config.PRICE_ACTION_VOLUME_LOOKBACK_WEEKLY,
                    max_risk_pct=config.PRICE_ACTION_MAX_RISK_PCT_WEEKLY,
                    short_eligible=short_eligible,
                )
                for signal in pa_weekly_signals:
                    # Same reasoning as weekly_breakout above.
                    if signal.symbol not in daily_data:
                        continue
                    results["price_action_breakout_weekly"].append(
                        simulate_forward("price_action_breakout_weekly", signal, asof, daily_data[signal.symbol])
                    )

    if "monthly_breakout" in wanted:
        for asof in _dates_in_window(monthly_data, start, end):
            for signal in monthly_breakout.scan(_scan_as_of(monthly_data, asof)):
                # Same reasoning as weekly_data above.
                if signal.symbol not in daily_data:
                    continue
                results["monthly_breakout"].append(simulate_forward("monthly_breakout", signal, asof, daily_data[signal.symbol]))

    return results


def summarize(trades: list[TradeResult]) -> str:
    if not trades:
        return "No signals in this window."

    total = len(trades)
    opens = [t for t in trades if t.outcome == "open"]
    unfilled = [t for t in trades if t.outcome == "unfilled"]
    decided = [t for t in trades if t.outcome not in ("open", "unfilled")]
    # Win/loss is judged by the trade's actual return, not by which exit
    # mechanism fired - a trailing stop that ratchets above entry before
    # giving the trade back is still a win, even though its outcome label
    # says "trailing_stop" rather than "target1".
    wins = [t for t in decided if t.return_pct > 0]
    losses = [t for t in decided if t.return_pct <= 0]
    # Win rate is only meaningful over decided (closed) trades - diluting it
    # with still-open positions understates performance whenever a strategy
    # has a lot of recent, unresolved signals.
    win_rate = (len(wins) / len(decided) * 100) if decided else 0.0
    # Unfilled signals never actually entered a trade - excluded from
    # return/holding-period stats and from the best/worst ranking too.
    entered = [t for t in trades if t.outcome != "unfilled"]
    avg_return = sum(t.return_pct for t in entered) / len(entered) if entered else 0.0
    avg_days = sum(t.holding_days for t in entered) / len(entered) if entered else 0.0

    # Expectancy view: a strategy can be profitable on a low win rate if
    # winners are enough bigger than losers - profit factor (gross wins /
    # gross losses) and the average win/loss size make that visible
    # directly, rather than judging the strategy by hit rate alone.
    gross_win = sum(t.return_pct for t in wins)
    gross_loss = sum(-t.return_pct for t in losses)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    if gross_loss > 0:
        profit_factor_str = f"{gross_win / gross_loss:.2f}"
    else:
        profit_factor_str = "inf" if gross_win > 0 else "n/a"

    ranked = sorted(entered, key=lambda t: t.return_pct, reverse=True)
    top = ranked[:3]
    bottom = ranked[-3:][::-1] if len(entered) > 3 else []

    win_rate_str = f"{win_rate:.0f}% win rate of {len(decided)} decided" if decided else "no decided trades yet"
    unfilled_str = f"-{len(unfilled)}Unfilled" if unfilled else ""
    lines = [
        f"{total} signals | {len(wins)}W-{len(losses)}L-{len(opens)}Open{unfilled_str} ({win_rate_str})",
        f"Avg return: {avg_return:+.1f}% | Avg win: {avg_win:+.1f}% | Avg loss: {avg_loss:+.1f}% | Profit factor: {profit_factor_str}",
        f"Avg holding: {avg_days:.0f}d",
    ]
    if top:
        lines.append("Best: " + ", ".join(f"{t.symbol} {t.return_pct:+.1f}%" for t in top))
    if bottom:
        lines.append("Worst: " + ", ".join(f"{t.symbol} {t.return_pct:+.1f}%" for t in bottom))

    # Price Action Breakout's own diagnostics - only present on that
    # strategy's trades, so these lines only appear there, not on every
    # other strategy's summary.
    by_shape: dict[str, list[TradeResult]] = defaultdict(list)
    for t in decided:
        shape = t.diagnostics.get("breakout_type")
        if shape:
            by_shape[shape].append(t)
    if by_shape:
        shape_parts = []
        for shape, shape_trades in sorted(by_shape.items(), key=lambda kv: len(kv[1]), reverse=True):
            shape_wr = sum(1 for t in shape_trades if t.return_pct > 0) / len(shape_trades) * 100
            shape_parts.append(f"{shape} {len(shape_trades)} ({shape_wr:.0f}%)")
        lines.append("By shape: " + " | ".join(shape_parts))

    # base_candles bucketed into fixed, timeframe-agnostic bands (a 40-week
    # weekly base and a 40-day daily base aren't comparable in real time,
    # but "how long relative to this strategy's own min/max lookback" isn't
    # available here - this is a coarse, absolute-candle-count cut, useful
    # for spotting whether short vs. long bases perform differently at all).
    by_base_len: dict[str, list[TradeResult]] = defaultdict(list)
    # Band labels avoid literal "<"/">" - these summaries get sent to Telegram
    # with parse_mode=HTML, and a literal "<15" gets misread as an HTML tag,
    # which fails the whole send (see the 2026-09-20 backtest run failure).
    base_len_bands = [(15, "Under 15"), (25, "15-25"), (35, "25-35"), (float("inf"), "35+")]
    for t in decided:
        base_candles = t.diagnostics.get("base_candles")
        if base_candles is None:
            continue
        band = next(label for threshold, label in base_len_bands if base_candles < threshold)
        by_base_len[band].append(t)
    if by_base_len:
        band_order = [label for _, label in base_len_bands]
        base_len_parts = []
        for band in band_order:
            band_trades = by_base_len.get(band)
            if not band_trades:
                continue
            band_wr = sum(1 for t in band_trades if t.return_pct > 0) / len(band_trades) * 100
            base_len_parts.append(f"{band} {len(band_trades)} ({band_wr:.0f}%)")
        lines.append("By base length: " + " | ".join(base_len_parts))

    return "\n".join(lines)


def write_csv(all_trades: list[TradeResult], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        for t in all_trades:
            writer.writerow([
                t.strategy, t.symbol, t.direction, t.signal_date.date(), t.entry, t.stop_loss,
                ";".join(str(x) for x in t.targets), t.outcome, t.exit_date.date(),
                t.exit_price, round(t.return_pct, 2), t.holding_days,
                t.months_gap if t.months_gap is not None else "",
                *(t.diagnostics.get(key, "") for key in DIAGNOSTIC_KEYS),
            ])
