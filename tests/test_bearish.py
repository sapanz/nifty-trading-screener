"""The bearish Price action setups: bear flag, weekly resistance zone (with a trendline through it) and trendline breakdown, all short."""
import numpy as np
import pandas as pd

from signals import price_action as pa
from signals import price_action_board as pb
from signals import price_action_model as pm
from tests.test_price_action import _daily, _weeks

P = pa.Params()


def _bear_flag_weeks():
    """Lead-in weeks, a ~30% fall in 6 weeks, then a 3-week bounce that keeps well under half of it."""
    base = list(np.linspace(100, 104, 40))
    fall = list(np.linspace(104, 72, 7))
    bounce = [76, 80, 78]
    closes = base + fall + bounce
    H = [c + 1.5 for c in closes]
    L = [c - 1.5 for c in closes]
    return _weeks(H, L, closes)


def test_bear_flag_is_the_bull_flag_on_inverted_prices():
    H, L, C = _bear_flag_weeks()
    found = pa.patterns_at(H, L, C, len(H) - 1, P)
    assert ("flag", "short") in found and ("flag", "long") not in found
    pat = found[("flag", "short")]
    assert pat["level"] < 80 and pat["base"] > pat["level"]            # the trigger is below the fall's low, the bounce's high is above it
    assert pat["why"].startswith("bear flag: a ") and "bounce" in pat["why"]


def test_every_bearish_trigger_is_a_valid_short_and_has_a_name():
    seen = set()
    for seed in range(1, 9):
        df = _daily(seed)
        trig, pend = pa.scan_stock(df)
        for t in trig + pend:
            if t["side"] != "short" or t["kind"] == "retest":
                continue
            seen.add(t["kind"])
            ref = t.get("entry", df["close"].iloc[t["i"]])
            assert t["stop"] > ref > t["target"] > 0, t
            assert pb.setup_name(t["kind"], "short", t["geo"]) == pa.NAMES[(t["kind"], "short")]
            if t["kind"] == "zone":
                assert isinstance(t["cf"], bool) and t["geo"]["base"] > t["geo"]["level"]      # level = the zone's low, base = its high
            if t["kind"] == "trendline":
                assert len(t["geo"]["touches"]) >= 3 and t["geo"]["line"][3] > 0
    assert {"zone", "trendline"} <= seen


def test_trendline_break_needs_a_red_close_under_a_rising_line():
    for seed in range(1, 9):
        df = _daily(seed)
        trig, _ = pa.scan_stock(df)
        for t in (x for x in trig if x["kind"] == "trendline"):
            i = t["i"]
            assert df["close"].iloc[i] < df["open"].iloc[i] and df["close"].iloc[i] < t["level"]


def test_bearish_rows_get_a_probability_model_like_the_long_ones():
    frames = {f"S{k}": _daily(k) for k in range(1, 9)}
    ev, _ = pm.collect(frames)
    shorts = ev[ev["side"] == "short"]
    assert {"zone", "trendline"} <= set(shorts["kind"]) and (shorts["is_short"] == 1.0).all()
    assert "cf" in shorts.columns and shorts["rr"].min() > 0


def test_the_page_lists_the_bearish_kinds_with_a_direction_and_a_zone_trendline_filter():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    for needle in ('value="flag">Bull / bear flag', 'value="trendline">Trendline breakdown (short)', 'id="paSide"', 'id="paCf"', 's.kind === "trendline" && g.line',
                   '(state.paSide === "all" || (s.side || "long") === state.paSide) && (!state.paCf || s.cf)'):
        assert needle in html, needle


def test_each_restored_kind_has_a_name_on_both_sides():
    for kind in ("double", "hs", "triple", "flag"):
        assert (kind, "long") in pa.NAMES and (kind, "short") in pa.NAMES
    assert pa.NAMES[("hs", "short")] == "Head and shoulders" and pa.NAMES[("triple", "short")] == "Triple top"
