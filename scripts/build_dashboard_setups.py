#!/usr/bin/env python3
"""Builds the dashboard's Price action list after the close: weekly support zones and weekly breakouts (range, triangle, cup and handle)
with a daily trigger, each with entry / stop / target, reward:risk, a probability measured on the history, and the reason
(signals/price_action*.py). Universe = Nifty 500 plus every F&O stock. Published as setups.json + setups_candles.json (and sma_support.json, the Swing tab's SMA-support list) on the
`dashboard-setups` branch.

  python scripts/build_dashboard_setups.py                      # needs UPSTOX_ACCESS_TOKEN
  PA_SYMBOL_LIMIT=60 python scripts/build_dashboard_setups.py   # smoke test

Run nightly by .github/workflows/dashboard_setups.yml.
"""
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time  # noqa: E402

import requests  # noqa: E402

from signals import config, data, leader_trades, macro, news, nse_events, peers, price_action_board, runtime, secure, sma_support, universe, value_universe  # noqa: E402
from signals.publish import publish_directory  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

logger = logging.getLogger("dashboard_setups")
IST = ZoneInfo("Asia/Kolkata")
OUT_DIR = Path("dashboard_setups_site")
BRANCH = "dashboard-setups"
TITLE, EMOJI = "Dashboard price action", "🧱"
YEARS = int(os.environ.get("PA_YEARS", "6"))


def _oi_store(repository: str | None) -> dict | None:
    """The swing build's saved open-interest totals (for the OI tag); None when they cannot be read."""
    if not repository:
        return None
    try:
        return secure.loads(urllib.request.urlopen(f"https://raw.githubusercontent.com/{repository}/dashboard-swing/swing_oi.json", timeout=30).read())
    except Exception as exc:  # noqa: BLE001
        logger.info("No swing_oi.json (%s) - the OI tag is left out", exc)
        return None


def main() -> None:
    runtime.setup_logging()
    token, repository = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    limit = int(os.environ.get("PA_SYMBOL_LIMIT", "0"))
    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        fo = set(data.fetch_fo_eligible_symbols(client))
        try:
            sectors = universe.fetch_nifty500_constituents()
        except Exception as exc:  # noqa: BLE001 - NSE sometimes refuses; the F&O stocks alone still make a list
            logger.warning("Nifty 500 list failed (%s) - building from the F&O stocks only", exc)
            sectors = {}
        symbols = sorted(set(sectors) | fo)
        if limit:
            symbols = symbols[:limit]
        equity_map = data.build_instrument_map(client, symbols)
        frames = data._fetch_history(equity_map, fetch_one=lambda key: client.get_daily_history(key, years=YEARS), throttle=client.throttle, label="daily")
        frames = {sym: data._with_todays_candle(client, equity_map[sym], df) for sym, df in frames.items()}
        frames = {sym: df for sym, df in frames.items() if len(df) >= 260 and "volume" in df}
        logger.info("%d stocks with at least a year of candles (%d F&O)", len(frames), sum(1 for s in frames if s in fo))
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise
    # The universe: only stocks above MCAP_MIN_CR (a screener.in custom screen). The leader trades keep every F&O stock (`frames_all`); a failed or short fetch leaves the universe as it was.
    frames_all, mcap_min = frames, None
    email, password = os.environ.get("SCREENER_EMAIL"), os.environ.get("SCREENER_PASSWORD")
    if email and password:
        try:
            big = value_universe.fetch_screen_symbols(email, password, config.MCAP_SCREEN_QUERY)
            if len(big) < config.MCAP_MIN_SYMBOLS:
                raise RuntimeError(f"only {len(big)} symbols came back for the market-cap screen (fewer than {config.MCAP_MIN_SYMBOLS}) - the list looks cut short")
            kept = {sym: df for sym, df in frames.items() if sym in big}
            if len(kept) < 0.25 * len(frames):
                raise RuntimeError(f"the market-cap screen matches only {len(kept)} of the {len(frames)} stocks - symbols probably do not line up")
            logger.info("Market-cap filter (> %d cr): %d of %d stocks kept (%d F&O)", config.MCAP_MIN_CR, len(kept), len(frames), sum(1 for sym in kept if sym in fo))
            frames, mcap_min = kept, config.MCAP_MIN_CR
        except Exception as exc:  # noqa: BLE001 - the filter is skipped, not guessed
            logger.warning("Market-cap filter skipped (%s) - the universe is unchanged", exc)
    else:
        logger.info("No SCREENER_EMAIL / SCREENER_PASSWORD - the universe is not filtered by market cap")
    # Optional context for the selected stock - each source fails soft and the page says what is missing.
    series, macro_status = macro.fetch_global()
    for key, index_key in (("nifty", "NSE_INDEX|Nifty 50"), ("vix", "NSE_INDEX|India VIX")):
        try:
            series[key] = client.get_daily_history(index_key, years=1)["close"]
            macro_status[key] = "upstox"
        except Exception as exc:  # noqa: BLE001
            macro_status[key] = f"upstox: {type(exc).__name__}"
    meetings, actions, event_status = nse_events.fetch(datetime.now(IST).date())
    logger.info("Macro sources: %s", macro_status)
    logger.info("NSE events: %s", event_status)
    # Stock-specific context: company names for the news search, and fetchers that run only for the stocks that end up on the list.
    try:
        names = universe.fetch_nifty500_names()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Company names failed (%s) - the news search will use symbols", exc)
        names = {}
    session, ticker_cache, today = requests.Session(), {"^GSPC": series.get("spx")}, datetime.now(IST).date()
    stats = {"tickers": 0, "tickers_ok": 0, "news": 0, "news_ok": 0}

    def global_fn(symbol: str, sector: str | None) -> dict:
        out = {}
        for t in peers.tickers_for(symbol, sector) + ["^GSPC"]:
            if t not in ticker_cache:
                stats["tickers"] += 1
                ticker_cache[t] = macro.fetch_ticker(t, session)
                stats["tickers_ok"] += ticker_cache[t] is not None
                time.sleep(0.2)
            if ticker_cache[t] is not None:
                out["SPX" if t == "^GSPC" else t] = ticker_cache[t]
        return out

    def news_fn(symbol: str, name: str):
        stats["news"] += 1
        time.sleep(0.4)
        items, source = news.fetch(name, symbol, today, session)
        stats["news_ok"] += bool(items)
        return items, source

    collected = price_action_board.collect(frames)
    payload, candles = price_action_board.build(frames, fo=fo, sectors=sectors, oi_store=_oi_store(repository), now=datetime.now(IST),
                                                macro_factors=macro.readings(series), events=(meetings, actions, bool(meetings) or bool(actions)), collected=collected,
                                                news_fn=news_fn, names=names, global_fn=global_fn)
    payload["mcap_min_cr"] = mcap_min
    logger.info("Stock-specific context: %s; %d company names", stats, len(names))
    past = price_action_board.history(collected[1], fo=fo, sectors=sectors)
    m = payload["model"]
    logger.info("As of %s: %d setups from %d stocks. History: %s", payload["asof"], len(payload["setups"]), payload["universe"], json.dumps({k: m.get(k) for k in ("n", "all", "calibration", "base_rate")}))
    for row in m.get("by_kind", []):
        logger.info("  %-24s %s", row["name"], {k: row[k] for k in ("n", "hit", "avg_r", "pf", "rr")})
    for row in m.get("by_year", []):
        logger.info("  %s %s", row["year"], {k: row[k] for k in ("n", "hit", "avg_r")})
    if not payload["universe"]:
        raise RuntimeError("No candles - nothing to publish")
    OUT_DIR.mkdir(exist_ok=True)
    secure.write_json(OUT_DIR / "setups.json", payload)
    secure.write_json(OUT_DIR / "setups_candles.json", candles)
    try:                                                                  # the Swing tab's "Bullish SMA support" list (signals/sma_support.py); a failure here must not stop the rest
        trend = sma_support.collect_trend_tables(frames, sectors)           # ranks a daily long signal's trend against every signal the rules ever gave
        sma = sma_support.build(frames, sectors, fo, datetime.now(IST), trend)
        sma["mcap_min_cr"] = mcap_min
        secure.write_json(OUT_DIR / "sma_support.json", sma)
        logger.info("SMA support: %d setups (%d daily, %d weekly), %d sectors measured", len(sma["rows"]), sum(r["tf"] == "D" for r in sma["rows"]), sum(r["tf"] == "W" for r in sma["rows"]), sma["sectors"])
        past_sma = sma_support.history(frames, sectors, fo, trend)                 # the same rules traded over the whole history: entries and exits for the page's backtest view
        secure.write_json(OUT_DIR / "sma_hist.json", past_sma)
        (OUT_DIR / "sma_hist" / "c").mkdir(parents=True, exist_ok=True)
        for symbol in past_sma["symbols"]:
            secure.write_json(OUT_DIR / "sma_hist" / "c" / f"{symbol}.json", price_action_board.history_candles(frames[symbol]))
        logger.info("SMA support backtest: %d trades in %d stocks", len(past_sma["rows"]), len(past_sma["symbols"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("SMA support list failed: %s", exc)
    try:                                                                  # the Swing tab's "High-range leader trades": every day's leaders traded by the Momentum rules, with entries, exits and what is held now
        lead = leader_trades.history(frames_all, fo)
        secure.write_json(OUT_DIR / "leader_trades.json", lead)
        (OUT_DIR / "leader_hist" / "c").mkdir(parents=True, exist_ok=True)
        for symbol in lead["symbols"]:
            secure.write_json(OUT_DIR / "leader_hist" / "c" / f"{symbol}.json", price_action_board.history_candles(frames_all[symbol]))
        logger.info("Leader trades: %d trades in %d stocks, %d still held", len(lead["rows"]), len(lead["symbols"]), sum(1 for r in lead["rows"] if r[7] == 2))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Leader trades failed: %s", exc)
    # The backtest view: past setups by year, and each stock's daily candles in its own file (the page loads one when a row is opened).
    hist_dir = OUT_DIR / "setups_hist"
    (hist_dir / "c").mkdir(parents=True, exist_ok=True)
    for year, rows in past.items():
        secure.write_json(hist_dir / f"{year}.json", {"year": year, "rows": rows})
    symbols = sorted({r["symbol"] for rows in past.values() for r in rows})
    for symbol in symbols:
        secure.write_json(hist_dir / "c" / f"{symbol}.json", price_action_board.history_candles(frames[symbol]))
    secure.write_json(hist_dir / "index.json", {"asof": payload["asof"], "years": sorted(past), "counts": {str(y): len(r) for y, r in past.items()}, "min_rr": payload["min_rr"]})
    logger.info("Backtest view: %d past setups over %d years, candles for %d stocks", sum(len(r) for r in past.values()), len(past), len(symbols))
    if token and repository:
        publish_directory(str(OUT_DIR), repository, token, BRANCH, f"setups {payload['asof']}")


if __name__ == "__main__":
    main()
