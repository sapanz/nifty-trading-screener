#!/usr/bin/env python3
"""Intraday research loop: fetch (once) -> event table -> walk-forward models -> configuration search -> report.

  python scripts/run_research.py --fetch      # only the data step (needs UPSTOX_ACCESS_TOKEN); skipped if the file exists
  python scripts/run_research.py              # analysis on the saved data

The 5-minute candles of the F&O stocks are saved to research_data/intraday_5m.pkl.gz by the fetch step and restored from the
Actions cache on later runs, so analysis runs don't need an Upstox login. Knobs (env): RESEARCH_MONTHS (default 36),
RESEARCH_GEOMS ("0,2,5"), RESEARCH_TRAIN_FRAC (0.4), RESEARCH_FEATURES_OFF (comma list of features to drop).
"""
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = "research_data/intraday_5m.pkl.gz"
REPORT = "research_report.txt"


def fetch(months: int) -> None:
    from signals import data, runtime, universe
    from signals.upstox_client import UpstoxClient, UpstoxError
    from datetime import timedelta
    from signals.upstox_client import ist_today

    runtime.setup_logging()
    client = UpstoxClient(runtime.get_env("UPSTOX_ACCESS_TOKEN"))
    symbols = sorted(data.fetch_fo_eligible_symbols(client))
    instrument_map = data.build_instrument_map(client, symbols)
    end = ist_today()
    start = end - timedelta(days=round(months * 30.5))
    out: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    for n, (symbol, key) in enumerate(sorted(instrument_map.items()), 1):
        frames, chunk_end, misses = [], end, 0
        while chunk_end > start:
            chunk_start = max(start, chunk_end - timedelta(days=data.INTRADAY_CHUNK_DAYS - 1))
            try:
                df = client.get_candles_between(key, "minutes", 5, chunk_start, chunk_end)
                misses = 0 if len(df) else misses + 1
                if len(df):
                    frames.append(df)
            except UpstoxError as exc:            # e.g. older than Upstox serves: stop going further back for this stock
                misses += 2
                print(f"{symbol}: {chunk_start}..{chunk_end}: {exc}")
            client.throttle()
            if misses >= 3:
                break
            chunk_end = chunk_start - timedelta(days=1)
        if frames:
            out[symbol] = pd.concat(frames).sort_index().loc[lambda d: ~d.index.duplicated(keep="last")]
        if n % 20 == 0:
            print(f"fetched {n}/{len(instrument_map)} symbols, {time.time() - t0:.0f}s")
    if len(out) < 100:
        raise RuntimeError(f"only {len(out)} symbols fetched")
    os.makedirs("research_data", exist_ok=True)
    pd.to_pickle(out, DATA, compression="gzip")
    first = min(df.index[0] for df in out.values()); last = max(df.index[-1] for df in out.values())
    print(f"saved {len(out)} symbols, {first} -> {last}")
    try:
        sectors = universe.fetch_nifty500_constituents()
        pd.to_pickle(sectors, "research_data/sectors.pkl")
    except Exception as exc:  # noqa: BLE001
        print(f"sector map unavailable: {exc}")


def analyse() -> str:
    from research import dataset, walkforward

    intraday = pd.read_pickle(DATA, compression="gzip")
    sectors = pd.read_pickle("research_data/sectors.pkl") if os.path.exists("research_data/sectors.pkl") else None
    first = min(df.index[0] for df in intraday.values()); last = max(df.index[-1] for df in intraday.values())
    lines = [f"Data: {len(intraday)} F&O stocks, 5-minute candles {first.date()} to {last.date()}; sector map {'yes' if sectors else 'no'}."]
    t0 = time.time()
    ev = dataset.build_events(intraday, sectors, processes=int(os.environ.get("RESEARCH_PROCESSES", "4")))
    lines.append(f"{len(ev)} events (stock x day x entry time 10:00/10:15/10:30) built in {time.time() - t0:.0f}s; days {ev['date'].min().date()} to {ev['date'].max().date()}.")
    geoms = [int(x) for x in os.environ.get("RESEARCH_GEOMS", ",".join(str(i) for i in range(len(dataset.GEOMS)))).split(",")]
    off = {x for x in os.environ.get("RESEARCH_FEATURES_OFF", "").split(",") if x}
    features = [f for f in dataset.FEATURES if f not in off]
    t0 = time.time()
    oos = walkforward.walk_forward(ev, features=features, geoms=geoms, train_frac=float(os.environ.get("RESEARCH_TRAIN_FRAC", "0.4")))
    lines.append(f"Walk-forward models fitted in {time.time() - t0:.0f}s ({len(geoms)} geometries x 2 sides), out-of-sample rows {len(oos)}.")
    lines.append(walkforward.search(oos))
    return "\n".join(lines)


def main() -> None:
    months = int(os.environ.get("RESEARCH_MONTHS", "36"))
    if "--fetch" in sys.argv:
        if os.path.exists(DATA) and os.environ.get("RESEARCH_REFETCH", "").lower() != "true":
            print(f"{DATA} already present - not fetching.")
            return
        fetch(months)
        return
    report = analyse()
    print(report)
    open(REPORT, "w").write(report)
    try:
        from signals import runtime
        from signals.telegram import send_message
        send_message(runtime.get_env("TELEGRAM_BOT_TOKEN"), runtime.get_env("TELEGRAM_CHAT_ID"), "🔬 <b>Intraday research</b>\n" + report[:3500])
    except Exception as exc:  # noqa: BLE001
        print(f"telegram skipped: {exc}")


if __name__ == "__main__":
    main()
