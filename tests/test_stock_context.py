import json
from datetime import date

import numpy as np
import pandas as pd
import requests

from signals import macro, nse_events
from signals import price_action_board as pb
from signals import stock_context as sc

UP, DOWN = pd.Series(np.linspace(100, 120, 80)), pd.Series(np.linspace(120, 100, 80))


# ---------------------------------------------------------------- global macro
def test_reading_trend_needs_the_average_and_the_20_session_change_to_agree():
    assert macro.reading("spx", UP)["trend"] == "up" and macro.reading("spx", DOWN)["trend"] == "down"
    mixed = pd.Series([100.0] * 59 + [110.0] * 20 + [105.0])        # above its 50-session average, yet below where it was 20 sessions ago
    r = macro.reading("spx", mixed)
    assert r["above50"] and r["chg20"] < 0 and r["trend"] == "flat" and "going sideways" in r["text"]
    assert macro.reading("spx", UP.iloc[:40]) is None                # too little history
    assert "S&P 500 is rising" in macro.reading("spx", UP)["text"] and "+" in macro.reading("spx", UP)["text"]


def _factors(**trend):
    return {k: macro.reading(k, UP if t == "up" else DOWN) for k, t in trend.items()}


def test_assess_uses_the_sectors_own_factors_and_reasons():
    f = _factors(spx="up", dxy="down", us10y="down", nifty="up", vix="down", usdinr="up", ndx="up", brent="up")
    it = macro.assess("Information Technology", "long", f)
    assert it["verdict"] == "supportive" and "7 of 7" in it["summary"]
    usd = next(i for i in it["items"] if i["factor"] == "USD/INR")
    assert usd["verdict"] == "for" and usd["sector"] and "bill in dollars" in usd["why"] and it["items"][0]["sector"]      # sector factors come first
    assert all(i["factor"] != "Brent crude" for i in it["items"])                                                         # crude is not an IT factor
    auto = macro.assess("Automobile and Auto Components", "long", f)
    crude = next(i for i in auto["items"] if i["factor"] == "Brent crude")
    assert crude["verdict"] == "against" and "fuel" in crude["why"]                                                       # crude rising hurts autos


def test_assess_flips_for_a_short_and_counts_only_what_it_knows():
    f = _factors(spx="up", dxy="down", us10y="down", nifty="up", vix="down")
    assert macro.assess("Power", "long", f)["verdict"] == "supportive" and macro.assess("Power", "short", f)["verdict"] == "against"
    partial = macro.assess("Power", "long", _factors(spx="up", dxy="up"))                                                 # one for, one against, three missing
    assert partial["verdict"] == "mixed" and "1 of 2" in partial["summary"] and sum(i["verdict"] == "nodata" for i in partial["items"]) == 3
    assert macro.assess("Power", "long", {})["verdict"] == "nodata"


def test_a_mixed_factor_never_counts_and_the_sector_reason_replaces_the_general_one():
    f = _factors(brent="up", spx="up", dxy="down", us10y="down", nifty="up", vix="down")
    oil = macro.assess("Oil Gas & Consumable Fuels", "long", f)
    assert next(i for i in oil["items"] if i["factor"] == "Brent crude")["verdict"] == "neutral"
    metals = macro.assess("Metals & Mining", "long", f)
    assert sum(i["factor"] == "Dollar index" for i in metals["items"]) == 1 and "priced in dollars" in next(i for i in metals["items"] if i["factor"] == "Dollar index")["why"]
    assert macro.assess("No such sector", "long", f)["verdict"] == "supportive"                                           # unknown sector: the general factors only


def test_fetch_global_never_raises(monkeypatch):
    def boom(self, *a, **k):
        raise requests.ConnectionError("blocked")
    monkeypatch.setattr(requests.Session, "get", boom)
    series, status = macro.fetch_global()
    assert series == {} and set(status) == set(macro.SOURCES) and all("ConnectionError" in v for v in status.values())


# ---------------------------------------------------------------- NSE events
CAL = [{"symbol": "ABC", "purpose": "Financial Results", "date": "18-Nov-2026"}, {"symbol": "ABC", "purpose": "Fund Raising", "date": "02-Jan-2027"},
       {"symbol": "XYZ", "bm_desc": "Dividend", "date": "12-Nov-2026"}, {"symbol": "", "date": "18-Nov-2026"}, {"symbol": "BAD", "date": "soon"}]
ACT = [{"symbol": "ABC", "subject": "Interim Dividend - Rs 4 Per Share", "exDate": "20-Nov-2026"}, {"symbol": "XYZ", "subject": "Bonus 1:1", "exDate": "-"}]


def test_calendar_and_actions_are_parsed_and_bad_rows_dropped():
    m, a = nse_events.parse_calendar(CAL), nse_events.parse_actions(ACT)
    assert set(m) == {"ABC", "XYZ"} and m["ABC"][0] == {"date": date(2026, 11, 18), "what": "Financial Results"} and m["XYZ"][0]["what"] == "Dividend"
    assert set(a) == {"ABC"} and a["ABC"][0]["date"] == date(2026, 11, 20)
    assert nse_events.parse_calendar(None) == {} and nse_events.parse_actions([]) == {}


def test_events_for_a_symbol_are_windowed_sorted_and_explained():
    m, a = nse_events.parse_calendar(CAL), nse_events.parse_actions(ACT)
    ev = nse_events.for_symbol("ABC", m, a, date(2026, 11, 13))
    assert [e["date"] for e in ev] == ["2026-11-18", "2026-11-20"] and ev[0]["days"] == 5 and ev[0]["upcoming"]                 # January is outside the window
    assert "gap the stock straight through a stop" in ev[0]["why"] and "ex-date" in ev[1]["why"] and ev[0]["text"] == "Board meeting 18 Nov: Financial Results"
    past = nse_events.for_symbol("ABC", m, a, date(2026, 11, 22))
    assert [e["upcoming"] for e in past] == [False, False] and "behind the stock" in past[0]["why"]
    assert nse_events.for_symbol("NONE", m, a, date(2026, 11, 13)) == []


def test_events_fetch_never_raises(monkeypatch):
    def boom(self, *a, **k):
        raise requests.ConnectionError("blocked")
    monkeypatch.setattr(requests.Session, "get", boom)
    m, a, status = nse_events.fetch(date(2026, 11, 13))
    assert m == {} and a == {} and status["calendar"].startswith("failed") and status["actions"].startswith("failed")


def test_events_fetch_asks_for_the_date_range_and_keeps_the_fuller_answer(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def fake(self, url, **k):
        calls.append(url)
        if "event-calendar" in url:
            return Resp(CAL if "from_date" in url else CAL[:1])
        if "corporateActions" in url:
            if "from_date" in url:
                raise requests.HTTPError("400")
            return Resp(ACT)
        return Resp({})
    monkeypatch.setattr(requests.Session, "get", fake)
    m, a, status = nse_events.fetch(date(2026, 11, 13))
    assert set(m) == {"ABC", "XYZ"} and "range" in status["calendar"] and set(a) == {"ABC"} and "default list" in status["actions"]
    assert any("from_date=06-11-2026&to_date=04-12-2026" in u for u in calls)


# ---------------------------------------------------------------- value migration
def _frames(n=400):
    """12 stocks in 3 sectors: Winners rise and trade more lately, Losers fall and trade less, Others drift."""
    idx = pd.bdate_range("2025-01-01", periods=n)
    out, sectors = {}, {}
    for k in range(12):
        sec = ("Winners", "Losers", "Others")[k % 3]
        drift = {"Winners": 0.0015, "Losers": -0.0010, "Others": 0.0002}[sec]
        c = 100 * np.cumprod(np.full(n, 1 + drift))
        v = np.full(n, 1e6)
        if sec == "Winners":
            v[-63:] *= 3
        if sec == "Losers":
            v[-63:] *= 0.4
        out[f"S{k:02d}"] = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": v}, index=idx)
        sectors[f"S{k:02d}"] = sec
    return out, sectors


def test_sector_flows_find_where_the_money_is_going():
    frames, sectors = _frames()
    frames.update({f"X{k}": frames["S02"] for k in range(10)})
    sectors.update({f"X{k}": "Others" for k in range(10)})          # enough members overall
    fl = sc.sector_flows(frames, sectors)
    assert fl["Winners"]["verdict"] == "in" and fl["Winners"]["rank"] == 1 and fl["Winners"]["share_now"] > fl["Winners"]["share_before"] and fl["Winners"]["rel6"] > 2
    assert fl["Losers"]["verdict"] == "out" and fl["Losers"]["rank"] == 3 and fl["Losers"]["share_now"] < fl["Losers"]["share_before"]
    assert fl["Others"]["verdict"] == "neutral" and fl["Winners"]["of"] == 3
    assert sc.sector_flows(dict(list(frames.items())[:5]), sectors) == {}                              # too few stocks to say anything


def test_value_migration_helps_longs_in_and_shorts_out_with_reasons():
    frames, sectors = _frames()
    frames.update({f"X{k}": frames["S02"] for k in range(10)})
    sectors.update({f"X{k}": "Others" for k in range(10)})
    fl = sc.sector_flows(frames, sectors)
    w = sc.value_migration("S00", "Winners", "long", fl, frames)
    assert w["verdict"] == "in" and w["for"] is True and "migrating INTO Winners" in w["summary"] and "tailwind for this long" in w["reasons"][-1]
    assert any("share of all turnover" in r for r in w["reasons"]) and any("in line with its sector" in r for r in w["reasons"])
    assert sc.value_migration("S00", "Winners", "short", fl, frames)["for"] is False
    lo = sc.value_migration("S01", "Losers", "short", fl, frames)
    assert lo["verdict"] == "out" and lo["for"] is True and "tailwind for this short" in lo["reasons"][-1]
    assert sc.value_migration("S02", "Others", "long", fl, frames)["for"] is None
    assert sc.value_migration("S00", None, "long", fl, frames)["verdict"] == "nodata"


# ---------------------------------------------------------------- catalysts
def _quiet(n=300):
    idx = pd.bdate_range("2025-06-02", periods=n)
    c = np.full(n, 100.0)
    return pd.DataFrame({"open": c.copy(), "high": c + 0.5, "low": c - 0.5, "close": c.copy(), "volume": np.full(n, 1e6)}, index=idx)


def test_price_catalysts_spot_an_event_day_a_gap_and_a_fresh_high():
    df = _quiet()
    assert sc.price_catalysts(df) == [] or all(e["kind"] in ("high", "low") for e in sc.price_catalysts(df))
    df.iloc[-4, df.columns.get_loc("close")] = 106.0                # +6% on 4x volume, then back
    df.iloc[-4, df.columns.get_loc("high")] = 106.5
    df.iloc[-4, df.columns.get_loc("volume")] = 4e6
    df.iloc[-3:, [df.columns.get_loc("open"), df.columns.get_loc("close")]] = 106.0
    df.iloc[-3:, df.columns.get_loc("high")] = 106.4
    df.iloc[-3:, df.columns.get_loc("low")] = 105.6
    ev = sc.price_catalysts(df, "long")
    move = next(e for e in ev if e["kind"] == "move")
    assert "+6.0% on 4.0× its usual volume" in move["text"] and move["for"] is True and "results or news" in move["why"]
    assert any(e["kind"] == "high" and e["for"] is True for e in ev)
    assert next(e for e in sc.price_catalysts(df, "short") if e["kind"] == "move")["for"] is False
    json.dumps(ev)                                                  # plain bools, not numpy ones
    g = _quiet()
    g.iloc[-2, g.columns.get_loc("open")] = 96.0                    # opens 4% lower, closes flat on normal volume
    g.iloc[-2, g.columns.get_loc("low")] = 95.5
    gap = next(e for e in sc.price_catalysts(g, "long") if e["kind"] == "gap")
    assert "opened -4.0%" in gap["text"] and gap["for"] is False


def test_catalysts_summary_puts_results_ahead_first_and_says_when_the_calendar_is_missing():
    df = _quiet()
    m = {"ABC": [{"date": date(2026, 11, 18), "what": "Financial Results"}]}
    c = sc.catalysts("ABC", df, "long", m, {}, date(2026, 11, 13), True)
    assert c["summary"].startswith("Event ahead - Board meeting 18 Nov: Financial Results (in 5 days)") and c["calendar"] and c["note"] is None
    none = sc.catalysts("ZZZ", df, "long", m, {}, date(2026, 11, 13), True)
    assert none["dated"] == [] and ("No catalyst found" in none["summary"] or none["price"])
    off = sc.catalysts("ABC", df, "long", m, {}, date(2026, 11, 13), False)
    assert off["dated"] == [] and "could not be read" in off["note"]


# ---------------------------------------------------------------- on the board
def test_board_rows_carry_the_three_context_blocks():
    from tests.test_price_action import _daily
    frames = {f"S{k:02d}": _daily(k) for k in range(24)}
    sectors = {s: ("Information Technology", "Financial Services", "Metals & Mining")[i % 3] for i, s in enumerate(frames)}
    factors = _factors(spx="up", dxy="down", us10y="up", nifty="up", usdinr="up")
    asof = frames["S00"].index[-1].date()
    first = sorted(frames)[0]
    payload, _ = pb.build(frames, fo=set(frames), sectors=sectors, macro_factors=factors,
                          events=({s: [{"date": asof + pd.Timedelta(days=3), "what": "Financial Results"}] for s in frames}, {}, True))
    json.dumps(payload)
    assert payload["context"]["calendar"] is True and len(payload["context"]["macro"]) == 5 and set(payload["context"]["sectors"]) <= set(sectors.values())
    assert payload["setups"], "the walks should give at least one setup"
    for r in payload["setups"]:
        assert r["macro"]["verdict"] in ("supportive", "mixed", "against") and r["macro"]["items"] and all(i["why"] for i in r["macro"]["items"])
        assert r["vm"]["verdict"] in ("in", "out", "neutral", "nodata") and r["vm"]["summary"]
        assert r["catalysts"]["dated"] and r["catalysts"]["summary"].startswith("Event ahead") and r["catalysts"]["dated"][0]["days"] == 3
    bare, _ = pb.build({k: frames[k] for k in list(frames)[:14]}, fo=set(frames))
    for r in bare["setups"]:                                        # no context given: every block says so instead of guessing
        assert r["macro"]["verdict"] == "nodata" and r["vm"]["verdict"] == "nodata" and r["catalysts"]["calendar"] is False and r["catalysts"]["note"]
    assert first
