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
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field

import pandas as pd

from signals import config, data
from signals.models import Signal
from signals.strategies import daily_swing, monthly_breakout, weekly_breakout


def _net_return_pct(gross_return_pct: float) -> float:
    """Deduct the round-trip transaction cost from a gross price return."""
    return gross_return_pct - config.ROUND_TRIP_COST_PCT

# Diagnostic-only fields a strategy can stamp onto a signal's `extra` dict
# (aside from months_gap, which has its own typed column) purely so a
# backtest's trade CSV carries enough to mine for what actually
# differentiates good and bad signals - vs guessing blind through repeated
# backtest round-trips. Not every strategy sets every key; write_csv leaves
# a column blank wherever a given trade's signal didn't set it.
DIAGNOSTIC_KEYS = [
    "vol_ratio", "confluence_gap_pct", "rsi14", "dist_from_sma200_pct",
    "extension_pct", "tightness_pct",
]

CSV_FIELDS = [
    "strategy", "symbol", "signal_date", "entry", "stop_loss", "targets",
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
    # Monthly ATH Breakout only: months spent below the prior all-time high
    # before this breakout (signal.extra["months_gap"]); None for every
    # other strategy, which doesn't set it.
    months_gap: int | None = None
    # Everything else a strategy stamped onto signal.extra (see
    # DIAGNOSTIC_KEYS) - diagnostic-only, never used to gate a signal or
    # alter simulate_forward's own logic.
    diagnostics: dict = field(default_factory=dict)


def simulate_forward(strategy: str, signal: Signal, signal_date: pd.Timestamp, daily_df: pd.DataFrame) -> TradeResult:
    """Walk the real daily price path after `signal_date` to see what happened."""
    future = daily_df[daily_df.index > signal_date]
    months_gap = signal.extra.get("months_gap")
    diagnostics = {k: v for k, v in signal.extra.items() if k != "months_gap"}

    as_of = daily_df[daily_df.index <= signal_date]
    signal_close = float(as_of["close"].iloc[-1]) if not as_of.empty else signal.entry
    if signal.entry > signal_close:
        # A resting buy-stop above the signal candle's own close (e.g. CIP's
        # entry-above-high) isn't filled yet - find the first later day that
        # actually trades up to it, and don't track outcomes before that.
        filled = future[future["high"] >= signal.entry]
        if filled.empty:
            return TradeResult(
                strategy, signal.symbol, signal_date, signal.entry, signal.stop_loss, signal.targets,
                outcome="unfilled", exit_date=signal_date, exit_price=signal.entry,
                return_pct=0.0, holding_days=0, months_gap=months_gap, diagnostics=diagnostics,
            )
        future = future[future.index >= filled.index[0]]

    for dt, row in future.iterrows():
        if row["low"] <= signal.stop_loss:
            return TradeResult(
                strategy, signal.symbol, signal_date, signal.entry, signal.stop_loss, signal.targets,
                outcome="stop_loss", exit_date=dt, exit_price=signal.stop_loss,
                return_pct=_net_return_pct((signal.stop_loss / signal.entry - 1) * 100),
                holding_days=(dt - signal_date).days, months_gap=months_gap, diagnostics=diagnostics,
            )
        hit = [i for i, target in enumerate(signal.targets) if row["high"] >= target]
        if hit:
            idx = max(hit)  # the highest target actually reached that day
            exit_price = signal.targets[idx]
            return TradeResult(
                strategy, signal.symbol, signal_date, signal.entry, signal.stop_loss, signal.targets,
                outcome=f"target{idx + 1}", exit_date=dt, exit_price=exit_price,
                return_pct=_net_return_pct((exit_price / signal.entry - 1) * 100),
                holding_days=(dt - signal_date).days, months_gap=months_gap, diagnostics=diagnostics,
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
        return_pct=_net_return_pct((last_close / signal.entry - 1) * 100),
        holding_days=(last_date - signal_date).days, months_gap=months_gap, diagnostics=diagnostics,
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


def run_backtest(
    daily_data: dict[str, pd.DataFrame],
    months: int,
    weekly_data: dict[str, pd.DataFrame] | None = None,
    monthly_data: dict[str, pd.DataFrame] | None = None,
) -> dict[str, list[TradeResult]]:
    """Backtest all active strategies over the trailing `months` months.

    `weekly_data` and `monthly_data` drive the Weekly Range Breakout and
    Monthly ATH Breakout scans specifically. Pass each strategy's real
    native fetch (data.fetch_weekly_history / data.fetch_monthly_ath_history)
    so they see the same data a live run would; omitting either falls back
    to resampling `daily_data` (data.to_weekly / data.to_monthly), which is
    fine for a quick local backtest without the extra Upstox fetches but
    won't exactly match live candle boundaries (weekly) or reach past
    DAILY_HISTORY_YEARS (monthly).
    """
    end = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(months=months)

    if weekly_data is None:
        weekly_data = data.to_weekly(daily_data)
    if monthly_data is None:
        monthly_data = data.to_monthly(daily_data)

    results: dict[str, list[TradeResult]] = {
        "daily_swing": [],
        "weekly_breakout": [],
        "monthly_breakout": [],
    }

    for asof in _dates_in_window(daily_data, start, end):
        daily_signals = daily_swing.scan(_scan_as_of(daily_data, asof), _scan_as_of(weekly_data, asof))
        for signal in daily_signals:
            results["daily_swing"].append(simulate_forward("daily_swing", signal, asof, daily_data[signal.symbol]))

    for asof in _dates_in_window(weekly_data, start, end):
        for signal in weekly_breakout.scan(_scan_as_of(weekly_data, asof)):
            # weekly_data may come from a separate fetch than daily_data (a
            # different set of symbols can fail between two independent
            # Upstox calls) - the exit walk-forward still needs daily bars,
            # so skip a signal whose symbol didn't come back in daily_data
            # rather than raising.
            if signal.symbol not in daily_data:
                continue
            results["weekly_breakout"].append(simulate_forward("weekly_breakout", signal, asof, daily_data[signal.symbol]))

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
    return "\n".join(lines)


def write_csv(all_trades: list[TradeResult], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        for t in all_trades:
            writer.writerow([
                t.strategy, t.symbol, t.signal_date.date(), t.entry, t.stop_loss,
                ";".join(str(x) for x in t.targets), t.outcome, t.exit_date.date(),
                t.exit_price, round(t.return_pct, 2), t.holding_days,
                t.months_gap if t.months_gap is not None else "",
                *(t.diagnostics.get(key, "") for key in DIAGNOSTIC_KEYS),
            ])
