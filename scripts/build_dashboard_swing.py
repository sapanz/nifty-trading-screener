#!/usr/bin/env python3
"""Builds the dashboard's Swing view after the close: R-factor on daily charts
for the F&O stocks (signals/swing.py), published as swing.json (plus the saved
OI totals, swing_oi.json) on the `dashboard-swing` branch. Each stock also carries its live daily
order-block zones (signals/swing_blocks.py); swing_candles.json holds the daily candles and levels for
the stocks that have one (the chart view).

Run nightly by .github/workflows/dashboard_swing.yml. Each run reads the
previous swing_oi.json from the branch and adds the new totals to it - see
signals/swing.py for why the open-interest series has to be saved night by
night rather than recomputed.
"""
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import data, runtime, swing, swing_blocks, universe  # noqa: E402
from signals.publish import publish_directory  # noqa: E402
from signals.upstox_client import UpstoxClient  # noqa: E402

logger = logging.getLogger("dashboard_swing")
IST = ZoneInfo("Asia/Kolkata")
OUT_DIR = Path("dashboard_swing_site")
BRANCH = "dashboard-swing"
TITLE, EMOJI = "Dashboard swing", "📊"


def _merge_latest(client: UpstoxClient, key: str, history: pd.DataFrame) -> pd.DataFrame:
    """Append the newest daily candle from the intraday endpoint when
    historical-candle hasn't reached it yet (it lags the latest session). Fail soft."""
    try:
        latest = client.get_intraday_daily_candle(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Latest daily candle failed for %s: %s", key, exc)
        return history
    client.throttle()
    if latest.empty or (not history.empty and latest.index[-1] <= history.index[-1]):
        return history
    return pd.concat([history, latest]).sort_index()


def _previous_store(repository: str | None) -> dict | None:
    if not repository:
        return None
    url = f"https://raw.githubusercontent.com/{repository}/{BRANCH}/swing_oi.json"
    try:
        return json.load(urllib.request.urlopen(url, timeout=30))
    except Exception as exc:  # noqa: BLE001 - first run: nothing saved yet
        logger.info("No previous swing_oi.json (%s) - starting a new store", exc)
        return None


def main() -> None:
    runtime.setup_logging()
    token, repository = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    try:
        client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
        industries = universe.fetch_nifty500_constituents()
        contracts = client.fetch_futures_contracts(per_symbol=2)
        equity_map = data.build_instrument_map(client, sorted(contracts))
        logger.info("%d F&O underlyings, %d with an equity key", len(contracts), len(equity_map))
        equity = data._fetch_history(
            equity_map, fetch_one=lambda key: client.get_daily_history(key, years=1),
            throttle=client.throttle, label="daily (equity)",
        )
        equity = {sym: _merge_latest(client, equity_map[sym], df) for sym, df in equity.items()}
        combined, near_expiries = {}, set()
        for symbol in equity:
            frames = []
            for expiry, key in contracts[symbol]:
                try:
                    frames.append(_merge_latest(client, key, client.get_daily_history(key, years=1)))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Futures history failed for %s %s: %s", symbol, expiry, exc)
                client.throttle()
            series = swing.combined_oi(frames)
            if not series.empty:
                combined[symbol] = series
            near_expiries.add(contracts[symbol][0][0])
    except Exception as exc:
        runtime.notify_error(TITLE, EMOJI, str(exc))
        raise

    floor = swing.previous_monthly_expiry(min(near_expiries))
    store = swing.update_store(_previous_store(repository), combined, near_expiries, equity, seed_floor=floor)
    snapshot = swing.build_swing(store, equity, industries, datetime.now(IST))
    if not snapshot["stocks"]:
        raise RuntimeError("Swing snapshot has no stocks - OI or equity data missing")
    candles = {}
    for row in snapshot["stocks"]:                                    # daily order blocks (signals/swing_blocks.py)
        df = equity.get(row["symbol"])
        zones = swing_blocks.scan_daily(df) if df is not None else []
        row["zones"] = zones
        if zones:
            payload = swing_blocks.chart_payload_daily(df)
            if payload:
                candles[row["symbol"]] = payload
    logger.info("Daily order blocks: %d of %d stocks have a live zone", len(candles), len(snapshot["stocks"]))
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "swing_candles.json").write_text(json.dumps({"asof": snapshot["asof"], "stocks": candles}, separators=(",", ":")))
    (OUT_DIR / "swing.json").write_text(json.dumps(snapshot, separators=(",", ":")))
    (OUT_DIR / "swing_oi.json").write_text(json.dumps(store, separators=(",", ":")))
    top = snapshot["stocks"][:3]
    logger.info("Swing snapshot as of %s: %d stocks, %d sectors; top R: %s", snapshot["asof"], snapshot["stock_count"],
                len(snapshot["sectors"]), [(r["symbol"], r["r"], r["bias"]) for r in top])
    if token and repository:
        publish_directory(str(OUT_DIR), repository, token, BRANCH, f"swing {snapshot['asof']}")


if __name__ == "__main__":
    main()
