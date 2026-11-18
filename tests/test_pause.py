import json

import numpy as np
import pandas as pd

from signals import price_action as pa
from signals import price_action_board as pb
from signals import price_action_model as pm

P = pa.Params()


def _bars(rows):
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def _flat(n=40, base=100.0):
    """Quiet candles around `base` with a range of about 1.2 (the ATR)."""
    rng = np.random.default_rng(3)
    mid = base + rng.normal(0, 0.15, n)
    o = mid + rng.normal(0, 0.1, n)
    c = mid + rng.normal(0, 0.1, n)
    h = np.maximum(o, c) + 0.5
    l = np.minimum(o, c) - 0.5
    return [[a, b, d, e] for a, b, d, e in zip(o, h, l, c)]


REDS = [[100.0, 100.1, 98.5, 98.6], [98.6, 98.7, 97.1, 97.2], [97.2, 97.3, 95.7, 95.8], [95.8, 95.9, 94.3, 94.4]]       # strong red candles, each closing at its low
HALT = [[94.4, 94.9, 94.0, 94.45], [94.45, 94.8, 94.1, 94.3], [94.3, 94.9, 94.05, 94.5]]                                  # three small candles: the halt zone 94.0 - 94.9
BREAK = [94.4, 94.6, 93.2, 93.4]                                                                                           # closes below the zone low, high inside the zone


def _arrays(rows):
    o, h, l, c = _bars(rows)
    return o, h, l, c, pa._atr(h, l, c)


def _run(rows):
    a = _arrays(rows)
    return pa.pause_run(*a[:4], a[4], len(rows) - 1, P)


def test_a_run_of_clear_selling_is_found_with_its_size():
    run = _run(_flat() + REDS)
    assert run and run["run"] == 4 and run["drop_atr"] >= 2 and abs(run["drop_pct"] - 5.6) < 0.1
    assert run["first"] == 40 and run["r"] == 43 and run["hi"] == 95.9
    assert _run(_flat() + REDS[1:])["run"] == 3, "three red candles are enough"


def test_the_run_needs_every_part_of_the_selling_pressure():
    base = _flat()
    assert _run(base + REDS[2:]) is None                                                       # two red candles are not a run
    weak = [list(r) for r in REDS]
    weak[2] = [97.2, 98.2, 95.7, 96.9]                                                          # one closed mid-range: no clear selling
    assert _run(base + weak) is None
    shallow = [[100.0, 100.1, 99.5, 99.6], [99.6, 99.7, 99.1, 99.2], [99.2, 99.3, 98.7, 98.8]]  # three red candles that fell less than 2 ATR
    assert _run(base + shallow) is None
    notlower = [list(r) for r in REDS]
    notlower[1] = [98.6, 98.7, 97.1, 98.9]                                                      # red body but its close is not below the one before... it is a green candle: breaks the run
    assert _run(base + notlower) is None


def _walk(run, rows):
    """Feed bars to halt_step the way the scan does; returns the last (zone, state)."""
    zone, state = None, "dead"
    sma = 100.0
    for j, (o, h, l, c) in enumerate(rows):
        zone, state = pa.halt_step(run, zone, o, h, l, c, j, sma, P, P.pause_max_zone)
        if state != "zone":
            break
    return zone, state


def test_the_halt_zone_grows_with_small_candles_and_breaks_below_its_low():
    run = _run(_flat() + REDS)
    zone, state = _walk(run, HALT)
    assert state == "zone" and zone["n"] == 3 and zone["lo"] == 94.0 and zone["hi"] == 94.9
    zone, state = _walk(run, HALT + [BREAK])
    assert state == "break" and zone["n"] == 3
    assert _walk(run, HALT[:2] + [[94.4, 95.0, 93.2, 93.4]])[1] == "dead", "the break candle's high went above the zone: the stop was hit first"
    assert _walk(run, HALT[:1] + [[94.4, 96.8, 94.2, 96.5]])[1] == "dead", "a rally candle ends the halt"
    assert _walk(run, HALT[:1] + [[94.4, 96.0, 94.2, 94.5]])[1] == "dead", "the zone may not rise above the last red candle's high"
    assert _walk(run, [[94.4, 95.9, 92.5, 94.4]])[1] == "dead", "a candle taller than 2 ATR is not a halt"
    assert _walk(run, [[94.4, 94.45, 94.4, 94.42]])[1] == "dead", "a flat bar is not a candle"
    z, st = pa.halt_step(run, None, 94.4, 94.9, 94.0, 94.45, 0, 90.0, P, 8)
    assert st == "dead", "above its moving average: not a downtrend"
    assert _walk(run, HALT[:1] + [[94.4, 94.9, 91.5, 94.3]])[1] == "dead", "a zone taller than 2 ATR is not a halt"


def _stock(tail, n_base=300):
    rows = _flat(n_base) + tail
    o, h, l, c = _bars(rows)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.full(len(rows), 5e5)}, index=pd.bdate_range("2024-01-01", periods=len(rows)))


def _pause_rows(df):
    t, p = pa.scan_stock(df)
    return [x for x in t if x["kind"] == "pause"], [x for x in p if x["kind"] == "pause"]


def test_a_daily_close_below_the_halt_zone_triggers_a_short_with_the_zone_high_as_the_stop():
    df = _stock(REDS + HALT + [BREAK])
    trig, pend = _pause_rows(df)
    assert len(trig) == 1 and not pend
    t = trig[0]
    assert t["side"] == "short" and t["i"] == len(df) - 1 and t["geo"]["tf"] == "daily" and t["geo"]["run"] == 4 and t["geo"]["zone"] == 3
    assert t["stop"] == 94.9 and t["level"] == 94.0 and t["target"] < 94.0 and t["rr"] > 0 and "halt zone of 3 days" in t["reason"]
    assert pb.setup_name("pause", "short", t["geo"]) == "Bearish continuation (daily): 4 red days, then a 3-day halt zone"
    json.dumps(t["geo"])
    assert t["geo"]["from"] < t["geo"]["run_to"] < t["geo"]["zone_from"] <= t["geo"]["to"]


def test_one_candle_is_not_a_zone_and_a_high_above_the_zone_kills_it():
    assert not _pause_rows(_stock(REDS + HALT[:1] + [BREAK]))[0], "a break after a single candle is not a halt zone"
    wider = _stock(REDS + HALT + [[94.4, 95.2, 93.9, 94.1], [94.1, 94.2, 93.0, 93.1]])          # a high of 95.2 that closes back inside only widens the zone: the stop is now 95.2
    t = _pause_rows(wider)[0]
    assert len(t) == 1 and t[0]["stop"] == 95.2 and t[0]["geo"]["zone"] == 4
    above = _stock(REDS + HALT + [[94.4, 96.2, 93.9, 94.1], [94.1, 94.2, 93.0, 93.1]])          # 96.2 is above the last red candle's high (95.9): the halt failed
    assert not _pause_rows(above)[0]


def test_a_zone_still_halting_on_the_latest_bar_is_pending_with_its_entry_below_the_low():
    trig, pend = _pause_rows(_stock(REDS + HALT))
    assert not trig and len(pend) == 1
    q = pend[0]
    assert q["side"] == "short" and q["entry"] < 94.0 and q["stop"] == 94.9 and q["geo"]["level"] == 94.0
    assert "short on a daily close below 94.0" in q["reason"]
    assert not _pause_rows(_stock(REDS + HALT[:1]))[1], "one candle is not a zone yet"


def _weekly_days(O, H, L, C):
    """Five daily bars whose weekly candle is exactly (O, H, L, C)."""
    return [[O, O, O, O], [O, H, O, H], [H, H, L, L], [L, L, L, L], [L, max(L, C), min(L, C), C]]


def test_a_weekly_halt_zone_triggers_on_a_daily_close_in_the_following_week():
    base = _flat(280, 100.0)
    rows = base[:280 - 280 % 5]
    for w in (*REDS, *HALT[:2]):                                                                  # 4 red weeks, then a 2-week halt zone 94.0 - 94.9
        rows += _weekly_days(*w)
    rows += [[94.4, 94.6, 93.4, 93.6]] + [[93.6, 93.8, 93.0, 93.2]] * 4                          # the following week: closes under 94.0 on its first day
    o, h, l, c = _bars(rows)
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.full(len(rows), 5e5)}, index=pd.bdate_range("2024-01-01", periods=len(rows)))
    weekly = [t for t in _pause_rows(df)[0] if t["geo"]["tf"] == "weekly"]
    assert len(weekly) == 1
    t = weekly[0]
    assert t["geo"]["run"] == 4 and t["geo"]["zone"] == 2 and t["stop"] == 94.9 and t["level"] == 94.0 and df.index[t["i"]] == df.index[len(rows) - 5]
    assert pb.setup_name("pause", "short", t["geo"]) == "Bearish continuation (weekly): 4 red weeks, then a 2-week halt zone"


def test_the_trade_is_a_short_outcome_and_the_board_lists_it_for_fo_stocks_only():
    df = _stock(REDS + HALT + [BREAK, [93.3, 93.4, 90.0, 90.5], [90.5, 91.0, 89.0, 89.5]])
    ev, _ = pm.collect({"X": df})
    r = ev[ev["kind"] == "pause"]
    assert len(r) == 1 and r.iloc[0]["side"] == "short" and r.iloc[0]["R"] > 0 and r.iloc[0]["tf"] == "daily"
    from tests.test_price_action import _daily
    frames = {f"S{k:02d}": _daily(k) for k in range(14)}
    payload, _ = pb.build(frames, fo={"S00"})
    assert all(x["fo"] for x in payload["setups"] if x["side"] == "short")
