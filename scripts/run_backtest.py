#!/usr/bin/env python3
"""One-off historical backtest of all active strategies over a lookback window.

For each historical date, reconstructs what each strategy would have
signalled using only data available up to that date (the live scan()
functions, unmodified - no separate backtest logic to drift out of sync),
then walks the real subsequent daily price action forward to see whether
each trade would have hit a target or its stop-loss first.

Usage:
  BACKTEST_MONTHS=3 python scripts/run_backtest.py

  # Scope to specific strategies only - skips their scan() calls entirely
  # (genuinely cheaper, not just filtered after the fact). "price_action_breakout"
  # is a shorthand for both of its timeframe legs (daily + weekly).
  BACKTEST_MONTHS=60 BACKTEST_STRATEGIES=price_action_breakout python scripts/run_backtest.py

Requires the same env vars as scripts/run_signals.py (UPSTOX_ACCESS_TOKEN,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Intended to be triggered manually via
.github/workflows/backtest.yml, which handles the Upstox TOTP login and
uploads the full trade-by-trade CSV as a workflow artifact.

This fetches the same ~500-symbol universe as a live run and then re-scans
it once per historical date, so it takes noticeably longer than a normal
run (order of 10-20 minutes, not seconds). Three separate Upstox history
fetches, same split as a live run: daily (DAILY_HISTORY_YEARS) for Daily
Swing, native-weekly (WEEKLY_HISTORY_YEARS) for Weekly Range Breakout, and
native-monthly (MONTHLY_ATH_HISTORY_YEARS) for Monthly ATH Breakout's
all-time-high check. Plus one instrument-master fetch (no price history)
for the F&O-eligible symbol set that gates Price Action Breakout's short
leg - see data.fetch_fo_eligible_symbols. Plus, when Weekly Value Stocks
Breakout is in scope: one screener.in scrape (no Upstox call) for the
fundamentally-screened symbol set that gates it (signals/value_universe.py),
then its own separate instrument map and weekly+daily Upstox fetches
scoped to exactly those symbols - unlike every other strategy here, this
one screens the full NSE, not the fixed ~500-symbol universe the rest of
this run uses, so its price history has to reach whatever screener.in
actually returns.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import backtest, data, runtime, universe, value_universe  # noqa: E402
from signals.telegram import send_message  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

CSV_PATH = "backtest_trades.csv"
FETCH_TITLE = "Backtest (data fetch)"
FETCH_EMOJI = "🧪"

STRATEGY_LABELS = {
    "daily_swing": ("Daily Swing (SMA44/BB Confluence)", "📈"),
    "weekly_breakout": ("Weekly Range Breakout", "🚀"),
    "monthly_breakout": ("Monthly ATH Breakout", "🏔️"),
    "price_action_breakout_daily": ("Price Action Breakout (Daily)", "🎯"),
    "price_action_breakout_weekly": ("Price Action Breakout (Weekly)", "🎯"),
    "value_breakout": ("Weekly Value Stocks Breakout", "💎"),
}

# "price_action_breakout" alone means both of its timeframe legs -
# a convenient shorthand since they're always the same underlying strategy.
STRATEGY_SHORTHANDS = {
    "price_action_breakout": {"price_action_breakout_daily", "price_action_breakout_weekly"},
}


def _parse_strategies(raw: str | None) -> set[str] | None:
    """None (env var unset) means "run everything", same as before this
    existed. A set (even one strategy) scopes both which scan() calls run
    and which Upstox fetches happen at all - see run_backtest()'s
    `strategies` docstring for why that's a genuine cost saving, not just
    filtered output."""
    if not raw:
        return None
    wanted: set[str] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        wanted |= STRATEGY_SHORTHANDS.get(token, {token})
    unknown = wanted - set(STRATEGY_LABELS)
    if unknown:
        raise ValueError(f"Unknown BACKTEST_STRATEGIES entries: {sorted(unknown)}")
    return wanted


def main() -> None:
    runtime.setup_logging()
    months = int(os.environ.get("BACKTEST_MONTHS", "3"))
    strategies = _parse_strategies(os.environ.get("BACKTEST_STRATEGIES"))

    token = runtime.get_env("TELEGRAM_BOT_TOKEN")
    chat_id = runtime.get_env("TELEGRAM_CHAT_ID")

    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        symbols = universe.fetch_nifty500_symbols()
        instrument_map = data.build_instrument_map(client, symbols)
        daily = data.fetch_daily(client, instrument_map)
        # Weekly Range Breakout and Monthly ATH Breakout each get their own
        # native fetch rather than a resample of `daily` above - weekly so
        # its candles match live Upstox week boundaries, monthly because
        # its all-time-high check needs much deeper history than
        # DAILY_HISTORY_YEARS (see WEEKLY_HISTORY_YEARS /
        # MONTHLY_ATH_HISTORY_YEARS in config.py).
        weekly = data.fetch_weekly_history(client, instrument_map)
        monthly = data.fetch_monthly_ath_history(client, instrument_map)
        # Gates Price Action Breakout's short leg - see
        # backtest.run_backtest's `short_eligible` docstring for the
        # "today's F&O universe applied across the whole window" caveat.
        fo_symbols = data.fetch_fo_eligible_symbols(client)
        # Gates Weekly Value Stocks Breakout entirely - only attempted when
        # that strategy is actually in scope, so an unrelated scoped
        # backtest run doesn't pay for a screener.in login/scrape it
        # doesn't need. Unlike every other strategy, this one screens the
        # full NSE (not the fixed `instrument_map`/`weekly`/`daily` above),
        # so it needs its own instrument map and weekly/daily fetches
        # scoped to exactly the symbols screener.in's query returned - see
        # backtest.run_backtest's value_weekly_data/value_daily_data
        # docstring.
        value_symbols = None
        value_weekly = None
        value_daily = None
        if strategies is None or "value_breakout" in strategies:
            value_symbols = value_universe.fetch_value_stock_symbols(
                runtime.get_env("SCREENER_EMAIL"), runtime.get_env("SCREENER_PASSWORD")
            )
            value_instrument_map = data.build_instrument_map(client, sorted(value_symbols))
            value_weekly = data.fetch_weekly_history(client, value_instrument_map)
            value_daily = data.fetch_daily(client, value_instrument_map)
        results = backtest.run_backtest(
            daily, months=months, weekly_data=weekly, monthly_data=monthly,
            strategies=strategies, short_eligible=fo_symbols, value_universe=value_symbols,
            value_weekly_data=value_weekly, value_daily_data=value_daily,
        )
    except Exception as exc:
        runtime.notify_error(FETCH_TITLE, FETCH_EMOJI, str(exc))
        raise

    send_message(token, chat_id, f"🧪 <b>Backtest results — last {months} month(s)</b>")

    all_trades = []
    for strategy, trades in results.items():
        title, emoji = STRATEGY_LABELS[strategy]
        summary = backtest.summarize(trades)
        send_message(token, chat_id, f"{emoji} <b>{title}</b>\n{summary}")
        print(f"\n=== {title} ===\n{summary}")  # also visible in the Actions run log, not just Telegram
        all_trades.extend(trades)

    backtest.write_csv(all_trades, CSV_PATH)
    print(f"Wrote {len(all_trades)} trades to {CSV_PATH}")


if __name__ == "__main__":
    main()
