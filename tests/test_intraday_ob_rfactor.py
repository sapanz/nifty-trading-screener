import numpy as np
import pandas as pd

from signals import intraday_ob_backtest as ib, intraday_ob_rfactor as ibr
from tests.test_intraday_ob_backtest import _walk_frame


def _universe(n=32, days=40):
    out = {}
    for k in range(n):
        df = _walk_frame(days=days, seed=100 + k, sigma=0.0014)
        df["volume"] = np.random.default_rng(k).integers(1000, 5000, len(df))
        out[f"S{k}"] = df
    return out


def test_day_tables_and_lite_r_are_causal_and_ranked():
    data = _universe()
    tables = ibr.build_day_tables(data)
    day = sorted(tables)[30]
    r_early = ibr.lite_r(tables[day], 6)
    assert len(r_early) >= 30 and all(0 <= v[0] <= 100 and v[1] in (-1, 1) for v in r_early.values())
    # changing candles AFTER the slot must not change R at that slot
    sym = next(iter(r_early))
    tables[day][sym]["c"][20] *= 2
    tables[day][sym]["v"][20] *= 50
    assert ibr.lite_r(tables[day], 6)[sym] == r_early[sym]


def test_first_days_have_no_average_volume():
    tables = ibr.build_day_tables(_universe(n=3, days=10))
    first = sorted(tables)[0]
    assert tables[first]["S0"]["av"] is None


def test_annotate_and_summary_run_with_and_without_oi():
    data = _universe()
    trades, arrays = ib.run_intraday_backtest(data)
    tables = ibr.build_day_tables(data)
    recs = ibr.annotate(trades, tables, None)
    assert recs and all(r["trade"].minutes_in < ibr.CUTOFF_MINUTES for r in recs)
    text = ibr.summarize_r(recs, arrays, 0)
    assert "R-lite" in text and "<" not in text.replace("<b>", "").replace("</b>", "")
    # an OI day file for one covered day
    day = recs[len(recs) // 2]["trade"].day
    stocks = {}
    for sym, s in tables[day].items():
        stocks[sym] = {"fo": 1, "av": s["av"] or 1e6, "c": s["c"], "v": s["v"],
                       "o": [1000 + 5 * i for i in range(75)], "p": s["c"]}
    recs2 = ibr.annotate(trades, tables, {day.strftime("%Y-%m-%d"): {"stocks": stocks}})
    assert any(r["r_full"] is not None for r in recs2)
    assert "Full R-factor" in ibr.summarize_r(recs2, arrays, 1)
