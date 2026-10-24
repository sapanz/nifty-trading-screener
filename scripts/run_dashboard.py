#!/usr/bin/env python3
"""Keeps the market dashboard's data fresh for the trading session.

A second loop, every DASHBOARD_OI_REFRESH_SECONDS (default 300), fetches
today's 5-minute futures candles (with open interest) for the F&O stocks and
publishes them as oi.json next to data.json - the OI-buildup chart's data.

Every DASHBOARD_REFRESH_SECONDS: fetch live quotes for the Nifty 500, build
the snapshot (signals/dashboard.py) and publish it as data.json on the
`dashboard-data` branch, which the page in docs/index.html reads. Reference
data (sector map, F&O list, 20-day average volumes) is fetched once at start.

  DASHBOARD_ONCE=true python scripts/run_dashboard.py   # one snapshot, then exit

Without GITHUB_TOKEN/GITHUB_REPOSITORY (local runs) it only writes
dashboard_site/data.json. Intended to be run by .github/workflows/dashboard.yml,
which does the Upstox login.
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import dashboard, data, oi, runtime, universe  # noqa: E402
from signals.publish import publish_directory  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

logger = logging.getLogger("dashboard")
IST = ZoneInfo("Asia/Kolkata")
SITE_DIR = Path("dashboard_site")
DATA_BRANCH = "dashboard-data"
OPEN, CLOSE, STOP_AFTER = dtime(9, 15), dtime(15, 30), dtime(15, 35)
MAX_CONSECUTIVE_FAILURES = 10
TITLE, EMOJI = "Dashboard", "📊"
PUBLISH_LOCK = threading.Lock()    # data.json and oi.json share one directory and one branch


def market_status(now: datetime) -> str:
    return "open" if now.weekday() < 5 and OPEN <= now.time() <= CLOSE else "closed"


def _trade_date(value) -> str | None:
    """IST date of a quote's last_trade_time, which may be epoch ms or an ISO string."""
    try:
        if value is None:
            return None
        if str(value).isdigit():
            return datetime.fromtimestamp(int(value) / 1000, IST).date().isoformat()
        return datetime.fromisoformat(str(value)).date().isoformat()
    except (ValueError, OSError):
        return None


def _publish(token, repository, message: str) -> None:
    if token and repository:
        with PUBLISH_LOCK:
            publish_directory(str(SITE_DIR), repository, token, DATA_BRANCH, message)


def refresh_oi(access_token: str, futures_map: dict, token, repository) -> None:
    """One pass: today's futures candles per F&O stock -> oi.json (published)."""
    client = UpstoxClient(access_token)            # its own session: this runs beside the quote loop
    today = pd.Timestamp(datetime.now(IST).date())
    stocks = {}
    for symbol, key in futures_map.items():
        try:
            arrays = oi.oi_arrays(client.get_intraday_candles(key, 5), today)
        except Exception as exc:  # noqa: BLE001 - one stock's failure shouldn't drop the rest
            logger.warning("OI candles failed for %s: %s", symbol, exc)
            arrays = None
        client.throttle()
        if arrays:
            stocks[symbol] = arrays
    if not stocks:
        logger.info("No OI candles yet today (before the open or a holiday)")
        return
    payload = {"updated_at": datetime.now(IST).isoformat(timespec="seconds"), "date": today.date().isoformat(),
               "start": "09:15", "step": 5, "slots": 75, "stocks": stocks}
    with PUBLISH_LOCK:
        (SITE_DIR / "oi.json").write_text(json.dumps(payload, separators=(",", ":")))
    _publish(token, repository, f"oi {payload['updated_at']}")
    logger.info("OI: %d stocks published", len(stocks))


def _oi_loop(access_token: str, futures_map: dict, token, repository, interval: int) -> None:
    while datetime.now(IST).time() <= STOP_AFTER:
        try:
            refresh_oi(access_token, futures_map, token, repository)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OI refresh failed: %s", exc)
        time.sleep(interval)


def main() -> None:
    runtime.setup_logging()
    refresh = int(os.environ.get("DASHBOARD_REFRESH_SECONDS", "120"))
    once = os.environ.get("DASHBOARD_ONCE", "").lower() == "true"
    token, repository = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")

    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        industries = universe.fetch_nifty500_constituents()
        instrument_map = data.build_instrument_map(client, sorted(industries))
        key_to_symbol = {key: symbol for symbol, key in instrument_map.items()}
        try:
            fo_symbols = client.fetch_fo_symbol_set()
        except Exception as exc:  # noqa: BLE001 - the F&O badge is optional
            logger.warning("F&O list unavailable, dashboard shows no F&O flags: %s", exc)
            fo_symbols = None
        daily = data._fetch_history(
            instrument_map, fetch_one=lambda key: client.get_daily_history(key, years=1),
            throttle=client.throttle, label="daily (for average volume)",
        )
        avg_volume = dashboard.average_volumes(daily, today=pd.Timestamp(datetime.now(IST).date()))
        logger.info("Reference data ready: %d symbols, %d with average volume", len(instrument_map), len(avg_volume))
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise

    SITE_DIR.mkdir(exist_ok=True)
    try:
        futures_map = client.fetch_futures_instrument_map()
    except Exception as exc:  # noqa: BLE001 - the OI chart is optional
        logger.warning("Futures contracts unavailable, no OI chart: %s", exc)
        futures_map = None
    access_token = runtime.get_env("UPSTOX_ACCESS_TOKEN")
    if futures_map:
        if once:
            refresh_oi(access_token, futures_map, token, repository)
        else:
            threading.Thread(
                target=_oi_loop, daemon=True,
                args=(access_token, futures_map, token, repository, int(os.environ.get("DASHBOARD_OI_REFRESH_SECONDS", "300"))),
            ).start()
    failures = 0
    first = True
    while True:
        now = datetime.now(IST)
        if not once and now.time() > STOP_AFTER:
            logger.info("Past %s IST, stopping.", STOP_AFTER)
            return
        try:
            quotes = client.get_quotes(list(instrument_map.values()))
            if first:
                logger.info("Sample raw quotes: %s", json.dumps(quotes[:2], default=str)[:1500])
                first = False
            snapshot = dashboard.build_snapshot(quotes, key_to_symbol, industries, avg_volume, fo_symbols, now)
            dates = [d for d in (_trade_date(s["last_trade"]) for s in snapshot["stocks"]) if d]
            snapshot["data_date"] = max(dates) if dates else None
            snapshot["market_status"] = market_status(now)
            for s in snapshot["stocks"]:
                s.pop("last_trade", None)
            with PUBLISH_LOCK:
                (SITE_DIR / "data.json").write_text(json.dumps(snapshot, separators=(",", ":")))
            _publish(token, repository, f"snapshot {snapshot['updated_at']}")
            logger.info("Snapshot: %d stocks, %d sectors, data date %s", snapshot["stock_count"],
                        len(snapshot["sectors"]), snapshot["data_date"])
            failures = 0
        except Exception as exc:  # noqa: BLE001 - one bad cycle shouldn't end the session
            failures += 1
            logger.warning("Refresh failed (%d in a row): %s", failures, exc)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                runtime.notify_error(TITLE, EMOJI, f"{failures} refreshes in a row failed: {exc}")
                raise
        if once:
            return
        time.sleep(refresh)


if __name__ == "__main__":
    main()
