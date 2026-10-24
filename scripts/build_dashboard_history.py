#!/usr/bin/env python3
"""Builds the dashboard's Replay data: the last DASHBOARD_HISTORY_DAYS trading
sessions of 5-minute candles for the Nifty 500, one compact file per day, plus
index.json listing the dates, published to the `dashboard-history` branch.

Run nightly by .github/workflows/dashboard_history.yml after the close. Today's
session is included when it has finished (taken from Upstox's intraday
endpoint, since the historical one lags by a day).
"""
import json
import logging
import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import dashboard_history, data, runtime, universe  # noqa: E402
from signals.publish import publish_directory  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

logger = logging.getLogger("dashboard_history")
IST = ZoneInfo("Asia/Kolkata")
OUT_DIR = Path("dashboard_history_site")
BRANCH = "dashboard-history"
KEEP_DAYS = int(os.environ.get("DASHBOARD_HISTORY_DAYS", "20"))
TITLE, EMOJI = "Dashboard history", "📊"


def _add_today(client: UpstoxClient, instrument_map: dict, intraday: dict) -> None:
    """Merge today's finished session (intraday endpoint) into the history frames."""
    now = datetime.now(IST)
    if now.weekday() >= 5 or now.time() < dtime(15, 35):
        return
    added = 0
    for symbol, key in instrument_map.items():
        try:
            today_df = client.get_intraday_candles(key, 5)
        except Exception as exc:  # noqa: BLE001 - fail soft: yesterday-and-earlier is still useful
            logger.warning("Intraday candles failed for %s: %s", symbol, exc)
            continue
        client.throttle()
        if today_df.empty:
            continue
        base = intraday.get(symbol)
        merged = today_df if base is None else pd.concat([base, today_df])
        intraday[symbol] = merged[~merged.index.duplicated(keep="last")].sort_index()
        added += 1
    logger.info("Added today's session for %d symbols", added)


def main() -> None:
    runtime.setup_logging()
    token, repository = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        industries = universe.fetch_nifty500_constituents()
        instrument_map = data.build_instrument_map(client, sorted(industries))
        try:
            fo_symbols = client.fetch_fo_symbol_set()
        except Exception as exc:  # noqa: BLE001
            logger.warning("F&O list unavailable: %s", exc)
            fo_symbols = None
        daily = data._fetch_history(
            instrument_map, fetch_one=lambda key: client.get_daily_history(key, years=1),
            throttle=client.throttle, label="daily",
        )
        intraday = data.fetch_intraday(client, instrument_map, months=1)
        _add_today(client, instrument_map, intraday)
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise

    (OUT_DIR / "days").mkdir(parents=True, exist_ok=True)
    dates = []
    for day in dashboard_history.trading_days(intraday, KEEP_DAYS):
        payload = dashboard_history.build_day(day, intraday, daily, industries, fo_symbols)
        if payload is None:
            logger.warning("Skipping %s: too few stocks traded", day.date())
            continue
        (OUT_DIR / "days" / f"{payload['date']}.json").write_text(json.dumps(payload, separators=(",", ":")))
        dates.append(payload["date"])
    if not dates:
        raise RuntimeError("No usable trading days built")
    (OUT_DIR / "index.json").write_text(json.dumps(
        {"dates": dates, "generated_at": datetime.now(IST).isoformat(timespec="seconds")}, separators=(",", ":")))
    size_mb = sum(f.stat().st_size for f in OUT_DIR.rglob("*.json")) / 1e6
    logger.info("Built %d days (%.1f MB): %s .. %s", len(dates), size_mb, dates[0], dates[-1])
    if token and repository:
        publish_directory(str(OUT_DIR), repository, token, BRANCH, f"history to {dates[-1]}")


if __name__ == "__main__":
    main()
