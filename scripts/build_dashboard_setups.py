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

from signals import config, data, forward_log, leader_trades, macro, news, nse_events, peers, price_action, price_action_board, price_action_model, runtime, secure, sma_support, universe, value_universe  # noqa: E402
from signals.publish import publish_directory  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

logger = logging.getLogger("dashboard_setups")
IST = ZoneInfo("Asia/Kolkata")
OUT_DIR = Path("dashboard_setups_site")
FWD_DIR = Path("dashboard_forward_site")
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


def _saved_context(repository: str | None) -> tuple[dict, dict]:
    """({symbol: industry}, {symbol: company name}) saved by the last good build (sector_map.json on the data branch): NSE's lists answer 403 to cloud runners now and then, and
    a build that lost every sector would publish a list with no sector support at all. ({}, {}) when nothing is saved."""
    if not repository:
        return {}, {}
    try:
        saved = secure.loads(urllib.request.urlopen(f"https://raw.githubusercontent.com/{repository}/{BRANCH}/sector_map.json", timeout=30).read())
        return dict(saved.get("sectors") or {}), dict(saved.get("names") or {})
    except Exception as exc:  # noqa: BLE001
        logger.info("No saved sector_map.json (%s)", exc)
        return {}, {}


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
        saved_sectors, saved_names = _saved_context(repository)
        if not sectors and saved_sectors:
            logger.warning("NSE's Nifty 500 list is unavailable - reusing the %d sectors saved by the last good build", len(saved_sectors))
        sectors = {**saved_sectors, **sectors}
        # The universe: every NSE stock above MCAP_MIN_CR (a screener.in custom screen - not only the Nifty 500), plus the F&O stocks for the leader trades. A failed or short screen
        # falls back to the Nifty 500 + F&O list with no market-cap line (logged, and the page says nothing about a market-cap line).
        big, mcap_min = set(), None
        email, password = os.environ.get("SCREENER_EMAIL"), os.environ.get("SCREENER_PASSWORD")
        if email and password:
            try:
                big = value_universe.fetch_screen_symbols(email, password, config.MCAP_SCREEN_QUERY)
                if len(big) < config.MCAP_MIN_SYMBOLS:
                    raise RuntimeError(f"only {len(big)} symbols came back for the market-cap screen (fewer than {config.MCAP_MIN_SYMBOLS}) - the list looks cut short")
                if len(big & set(sectors)) < 0.25 * len(big) and sectors:
                    raise RuntimeError(f"the market-cap screen shares only {len(big & set(sectors))} of its {len(big)} symbols with the Nifty 500 - symbols probably do not line up")
                mcap_min = config.MCAP_MIN_CR
            except Exception as exc:  # noqa: BLE001 - the filter is skipped, not guessed
                logger.warning("Market-cap universe skipped (%s) - using the Nifty 500 and F&O stocks", exc)
                big = set()
        else:
            logger.info("No SCREENER_EMAIL / SCREENER_PASSWORD - the universe is the Nifty 500 and F&O stocks, not filtered by market cap")
        if mcap_min:
            tradable = set(client.fetch_instrument_map())
            on_nse = big & tradable
            logger.info("Market-cap universe (> %d cr): %d screener symbols, %d tradable on NSE, %d of them outside the Nifty 500", config.MCAP_MIN_CR, len(big), len(on_nse), len(on_nse - set(sectors)))
            outside = on_nse - set(sectors)
            extra_sectors, extra_names = universe.fetch_extra_constituents()
            for sym, industry in extra_sectors.items():
                sectors.setdefault(sym, industry)
            logger.info("Industry known for %d of the %d stocks outside the Nifty 500", len(outside & set(extra_sectors)), len(outside))
            symbols = sorted(on_nse | fo)
        else:
            extra_names = {}
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
    # Every list - the leader trades included (cash segment) - is the market-cap universe; `frames_all` (that plus every F&O stock) is kept for the F&O comparison in the research log.
    frames_all = frames
    if mcap_min:
        frames = {sym: df for sym, df in frames_all.items() if sym in big}
        logger.info("Market-cap filter (> %d cr): %d stocks in the universe (%d F&O); %d more F&O stocks below the line are used only in the research log", mcap_min, len(frames), sum(1 for sym in frames if sym in fo), sum(1 for sym in frames_all if sym in fo and sym not in frames))
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
        names = {**saved_names, **extra_names, **universe.fetch_nifty500_names()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Company names failed (%s) - the news search will use symbols", exc)
        names = {**saved_names, **extra_names}
    if sectors and any(sym in sectors for sym in frames_all):                  # saved for the next build, but only for the stocks this one built (a fallback universe is then the last built one)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        secure.write_json(OUT_DIR / "sector_map.json", {"sectors": {s: sectors[s] for s in frames_all if s in sectors}, "names": {s: names[s] for s in frames_all if s in names}})
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
    lead = sma = None
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
        lead = leader_trades.history(frames, set(frames), fo)
        secure.write_json(OUT_DIR / "leader_trades.json", lead)
        (OUT_DIR / "leader_hist" / "c").mkdir(parents=True, exist_ok=True)
        for symbol in lead["symbols"]:
            secure.write_json(OUT_DIR / "leader_hist" / "c" / f"{symbol}.json", price_action_board.history_candles(frames[symbol]))
        logger.info("Leader trades: %d trades in %d stocks, %d still held", len(lead["rows"]), len(lead["symbols"]), sum(1 for r in lead["rows"] if r[7] == 2))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Leader trades failed: %s", exc)
    try:                                                                  # the forward log: today's signals frozen, earlier ones resolved by the candles since (own branch; never replaced when it cannot be read)
        if not (token and repository):
            raise RuntimeError("no GITHUB_TOKEN / GITHUB_REPOSITORY")
        ledger = forward_log.load(repository, token)
        if ledger.get("asof") and ledger["asof"] > payload["asof"]:
            raise RuntimeError(f"the saved ledger ({ledger['asof']}) is newer than this build ({payload['asof']})")
        now_iso = datetime.now(IST).isoformat()
        new = (forward_log.leader_signals(lead, frames) if lead else []) + (forward_log.sma_signals(sma, payload["asof"]) if sma else [])
        added = forward_log.add(ledger, new, now_iso)
        forward_log.update(ledger, frames_all)
        fwd = forward_log.payload(ledger, payload["asof"], now_iso, series.get("nifty"))
        FWD_DIR.mkdir(exist_ok=True)
        secure.write_json(FWD_DIR / forward_log.FILE, fwd)
        publish_directory(str(FWD_DIR), repository, token, forward_log.BRANCH, f"forward log {payload['asof']}")
        logger.info("Forward log: %d new signals, %d entries (%s)", added, len(fwd["entries"]), {k: fwd["summary"]["strategies"]["all"][k] for k in ("pending", "open", "closed", "skipped")})
    except forward_log.LedgerUnavailable as exc:
        logger.warning("Forward log skipped - the saved ledger could not be read, so it is left as it is (%s)", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Forward log failed: %s", exc)
    try:                                                                  # research only (nothing published): the weekly range breakout, the old rule against the live one, F&O against the other stocks
        new_rep = price_action_model.kind_report(collected[1], "range", fo)
        for label, kw in (("21+ weeks, volume >= 2x, no trend check (rule of 26 Nov 2026)", dict(rng_sma_weeks=0, range_max_width=0.25)),
                          ("21+ weeks, volume >= 2x, weekly SMA rising, box up to 25% tall", dict(range_max_width=0.25)),
                          ("13+ weeks, volume >= 2x, weekly SMA rising, box up to 20% tall", dict(rng_min_weeks=13)),
                          ("8+ weeks, no volume check, no trend check, box up to 25% (old rule)", dict(rng_min_weeks=8, rng_min_vol=0.0, rng_sma_weeks=0, range_max_width=0.25))):
            ev_x, _ = price_action_model.collect(frames, p=price_action.Params(**kw))
            logger.info("Range breakout research - %s: %s", label, json.dumps(price_action_model.kind_report(ev_x, "range", fo)))
        logger.info("Range breakout research - %s: %s", "live rule (21+ weeks, volume >= 2x, weekly SMA rising, box up to 20% tall)", json.dumps(new_rep))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Range breakout research failed: %s", exc)
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
