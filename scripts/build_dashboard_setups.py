#!/usr/bin/env python3
"""Builds the dashboard's Price action list after the close: weekly support zones and weekly breakouts (range, triangle, cup and handle)
with a daily trigger, each with entry / stop / target, reward:risk, a probability measured on the history, and the reason
(signals/price_action*.py). Universe = Nifty 500 plus every F&O stock. Published as setups.json + setups_candles.json on the
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

from signals import data, price_action_board, runtime, secure, universe  # noqa: E402
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
    payload, candles = price_action_board.build(frames, fo=fo, sectors=sectors, oi_store=_oi_store(repository), now=datetime.now(IST))
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
    if token and repository:
        publish_directory(str(OUT_DIR), repository, token, BRANCH, f"setups {payload['asof']}")


if __name__ == "__main__":
    main()
