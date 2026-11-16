import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from signals import macro, news, peers, stock_macro
from signals import price_action_board as pb

TODAY = date(2026, 11, 16)
IDX = pd.bdate_range(end="2026-11-16", periods=300)


def _series(drift, n=300):
    return pd.Series(100 * np.cumprod(np.full(n, 1 + drift)), index=IDX[-n:])


UP, DOWN = _series(0.002), _series(-0.002)


def _frame(drift, n=300):
    c = _series(drift, n).to_numpy()
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": np.full(n, 1e6)}, index=IDX[-n:])


# ---------------------------------------------------------------- peers
def test_group_and_tickers_prefer_the_curated_group_then_the_sector():
    g = peers.group_of("INFY", "Information Technology")
    assert g["basis"] == "peer group" and g["fund"][0] == "XLK" and g["global"]
    s = peers.group_of("NOSUCHSTOCK", "Healthcare")
    assert s["basis"] == "sector" and s["fund"][0] == "XLV" and s["global"] == []
    assert peers.group_of("NOSUCHSTOCK", None)["basis"] is None
    assert peers.tickers_for("INFY", "Information Technology")[0] == "XLK" and peers.tickers_for("X", None) == []
    assert all(sym in peers.SYMBOL_GROUP for sym in ("INFY", "SBIN", "TATASTEEL"))


def _india(stock_drift, peer_drift, market_drift=0.0003):
    members = [m for m in peers.GROUPS[peers.SYMBOL_GROUP["INFY"]]["members"] if m != "INFY"][:5]
    frames = {m: _frame(peer_drift) for m in members}
    frames["INFY"] = _frame(stock_drift)
    for k in range(20):
        frames[f"OTH{k}"] = _frame(market_drift)
    sectors = {s: "Information Technology" for s in frames}
    return peers.india_peers("INFY", "Information Technology", frames, sectors)


def test_indian_peers_strong_weak_and_the_stocks_place_with_reasons():
    strong = _india(-0.001, 0.002)
    assert strong["verdict"] == "strong" and strong["basis"] == "peer group" and strong["rel3m"] > 2 and strong["share_above50"] == 100
    assert strong["stock_place"] == strong["of"] and any("lagging" in r for r in strong["reasons"])
    weak = _india(0.002, -0.002)
    assert weak["verdict"] == "weak" and any("leading" in r for r in weak["reasons"])
    assert _india(0.0003, 0.0003)["verdict"] == "mixed"


def test_indian_peers_fall_back_to_the_sector_and_say_when_there_are_too_few():
    frames = {f"Z{k}": _frame(0.001) for k in range(6)}
    sectors = {s: "Power" for s in frames}
    r = peers.india_peers("Z0", "Power", frames, sectors)
    assert r["basis"] == "sector" and r["n"] == 5 and r["verdict"] in ("strong", "weak", "mixed")
    few = peers.india_peers("Z0", "Power", {k: frames[k] for k in ("Z0", "Z1")}, {"Z0": "Power", "Z1": "Power"})
    assert few["verdict"] == "nodata" and "Too few" in few["summary"]


def test_global_view_reads_each_reference_and_marks_missing_ones_no_data():
    want = peers.tickers_for("INFY", "Information Technology")
    series = {t: UP for t in want}
    series["SPX"] = _series(0.0005)
    del series[want[-1]]
    v = peers.global_view("INFY", "Information Technology", series, series["SPX"])
    assert v["verdict"] == "strong" and v["items"][0]["fund"] and v["items"][-1]["text"].endswith("no data") and "S&P 500" in v["summary"]
    d = peers.global_view("INFY", "Information Technology", {t: DOWN for t in want})
    assert d["verdict"] == "weak"
    assert peers.global_view("INFY", "Information Technology", {})["verdict"] == "nodata"
    assert peers.global_view("NOSUCH", None, {})["verdict"] == "nodata"


# ---------------------------------------------------------------- news
@pytest.mark.parametrize("title,score,category", [
    ("Jefferies upgrades Infosys, raises target price to Rs 1,900", 1, "analyst"),
    ("Infosys net profit rises 8% in Q2", 1, "results"),
    ("Infosys wins $1.6 billion deal from a European bank", 1, "orders"),
    ("Infosys board approves buyback of shares", 1, "corporate"),
    ("Brokerage downgrades Infosys on weak demand", -1, "analyst"),
    ("Infosys faces SEBI probe over disclosures", -1, "probe"),
    ("Infosys CFO resigns", -1, "management"),
    ("Infosys hosts annual shareholder meeting", 0, None),
])
def test_classify_gives_the_cue_and_the_reason(title, score, category):
    c = news.classify(title)
    assert c["score"] == score and c["category"] == category
    assert (c["cue"] and c["why"]) if score else (c["cue"] is None and c["why"] is None)


def test_a_negative_cue_beats_a_positive_one_in_the_same_headline():
    assert news.classify("Stock surges despite fraud probe")["score"] == -1


def test_key_phrase_and_mentions_do_not_confuse_group_companies():
    assert news.key_phrase("Tata Steel Ltd.", "TATASTEEL") == "tata steel"
    assert news.key_phrase("Infosys Ltd.", "INFY") == "infosys" and news.key_phrase("", "SBIN") == "sbin"
    assert news.mentions("Tata Steel posts record profit", "Tata Steel Ltd.", "TATASTEEL")
    assert not news.mentions("Tata Motors posts record profit", "Tata Steel Ltd.", "TATASTEEL")
    assert news.mentions("TCS wins order", "Tata Consultancy Services Ltd.", "TCS") and not news.mentions("Tcs lunch", "Other", "XYZ")


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Infosys wins big order - Mint</title><pubDate>Mon, 16 Nov 2026 08:00:00 GMT</pubDate><link>https://x.test/a</link><source>Mint</source></item>
<item><title>Infosys wins big order - Mint</title><pubDate>Mon, 16 Nov 2026 07:00:00 GMT</pubDate><link>https://x.test/dup</link></item>
<item><title>Reliance raises target - ET</title><pubDate>Sun, 15 Nov 2026 08:00:00 GMT</pubDate><link>https://x.test/b</link></item>
<item><title>Infosys old story - ET</title><pubDate>Mon, 01 Jan 2026 08:00:00 GMT</pubDate><link>https://x.test/c</link></item>
<item><title>No date Infosys</title><link>https://x.test/d</link></item></channel></rss>"""


def test_parse_rss_and_relevance_window_dedupe_and_name_match():
    items = news.parse_rss(RSS)
    assert len(items) == 4 and items[0]["title"] == "Infosys wins big order" and items[0]["source"] == "Mint"
    rel = news.relevant(items, "Infosys Ltd.", "INFY", TODAY)
    assert [i["link"] for i in rel] == ["https://x.test/a"]
    assert news.parse_rss("not xml") == []


def test_assess_verdicts_flip_for_shorts_and_no_news_is_not_bad_news():
    def items(*titles):
        return [{"title": t, "source": "s", "date": TODAY, "link": "https://x.test/" + str(i)} for i, t in enumerate(titles)]
    pos = news.assess(items("Upgrades A", "Profit rises", "Wins order from X", "plain"), "long", "google", TODAY)
    assert pos["verdict"] == "positive" and pos["for"] is True and pos["pos"] == 3 and len(pos["items"]) == 4
    assert news.assess(items("Upgrades A", "Profit rises", "plain"), "short", "google", TODAY)["for"] is False
    assert news.assess(items("Probe launched", "Profit falls"), "short", "google", TODAY)["for"] is True
    assert news.assess(items("Upgrades A", "Probe launched"), "long", "google", TODAY)["verdict"] == "mixed"
    assert news.assess(items("plain one", "plain two"), "long", "google", TODAY)["verdict"] == "quiet"
    none = news.assess([], "long", "google: no recent headlines", TODAY)
    assert none["verdict"] == "nodata" and none["for"] is None and "not bad news" in none["summary"]


def test_news_fetch_never_raises_and_falls_back_to_the_second_feed(monkeypatch):
    class Resp:
        def __init__(self, text, ok=True):
            self.text, self.ok = text, ok

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("blocked")

    class S:
        calls = []

        def get(self, url, **kw):
            self.calls.append(url)
            return Resp("", False) if "google" in url else Resp(RSS)
    s = S()
    out, src = news.fetch("Infosys Ltd.", "INFY", TODAY, s)
    assert src == "yahoo" and out and len(s.calls) == 2

    class Boom:
        def get(self, *a, **k):
            raise ConnectionError("down")
    out, why = news.fetch("Infosys Ltd.", "INFY", TODAY, Boom())
    assert out == [] and "ConnectionError" in why


# ---------------------------------------------------------------- the combined view
def _market(**trend):
    return {k: macro.reading(k, UP if t == "up" else DOWN) for k, t in trend.items()}


def _universe(peer_drift):
    members = [m for m in peers.GROUPS[peers.SYMBOL_GROUP["INFY"]]["members"] if m != "INFY"][:5]
    frames = {m: _frame(peer_drift) for m in members}
    frames["INFY"] = _frame(0.0)
    for k in range(20):
        frames[f"OTH{k}"] = _frame(0.0003)
    return frames, {s: "Information Technology" for s in frames}


def _news(*titles):
    return ([{"title": t, "source": "s", "date": TODAY, "link": "https://x.test/%d" % i} for i, t in enumerate(titles)], "google")


def test_stock_view_counts_what_is_with_the_trade_and_flips_for_a_short():
    frames, sectors = _universe(0.002)
    series = {t: UP for t in peers.tickers_for("INFY", "Information Technology")}
    factors = _market(spx="up", ndx="up", dxy="down", us10y="down", nifty="up", vix="down", usdinr="up", brent="up")
    args = ("INFY", "Information Technology", None, factors, series, frames, sectors, _news("Upgrades Infosys", "Profit rises", "Wins order"), TODAY)
    long_ = stock_macro.assess_stock(*args[:2], "long", *args[3:])
    assert long_["verdict"] == "supportive" and long_["score"] >= 2 and set(long_["sections"]) == {"world", "peers", "news", "backdrop"}
    assert long_["sections"]["world"]["for"] is True and long_["sections"]["peers"]["for"] is True and long_["sections"]["news"]["for"] is True
    assert long_["items"] and "supportive" in long_["summary"]
    short = stock_macro.assess_stock(*args[:2], "short", *args[3:])
    assert short["verdict"] == "against" and short["sections"]["world"]["for"] is False
    json.dumps(long_)


def test_stock_view_says_nodata_and_never_raises_when_nothing_is_known():
    r = stock_macro.assess_stock("NOSUCH", None, "long", {}, None, {}, {}, None, TODAY)
    assert r["verdict"] == "nodata" and r["sections"]["news"]["verdict"] == "nodata" and "could not be fetched" in r["sections"]["news"]["summary"]
    mixed = stock_macro.assess_stock("INFY", "Information Technology", "long", {}, {t: UP for t in peers.tickers_for("INFY", "Information Technology")}, *_universe(-0.002), _news(), TODAY)
    assert mixed["verdict"] in ("mixed", "against") and mixed["sections"]["world"]["verdict"] == "strong" and mixed["sections"]["peers"]["verdict"] == "weak"


# ---------------------------------------------------------------- on the board
def test_board_rows_carry_the_four_stock_sections_when_fetchers_are_given():
    from tests.test_price_action import _daily
    frames = {f"S{k:02d}": _daily(k) for k in range(24)}
    sectors = {s: "Information Technology" for s in frames}
    calls = {"news": [], "world": []}

    def news_fn(sym, name):
        calls["news"].append((sym, name))
        return _news("Upgrades " + name)

    def global_fn(tickers):
        calls["world"].append(tuple(tickers))
        return {t: UP for t in tickers} | {"SPX": UP}
    payload, _ = pb.build(frames, fo=set(frames), sectors=sectors, macro_factors=_market(spx="up", nifty="up"),
                          names={s: f"Name {s}" for s in frames}, news_fn=news_fn, global_fn=global_fn)
    json.dumps(payload)
    assert payload["setups"]
    for r in payload["setups"]:
        sec = r["macro"]["sections"]
        assert set(sec) == {"world", "peers", "news", "backdrop"} and sec["news"]["items"][0]["link"].startswith("https://x.test/")
        assert sec["peers"]["basis"] == "sector"
    listed = {r["symbol"] for r in payload["setups"]}
    assert {s for s, _ in calls["news"]} <= listed and len({s for s, _ in calls["news"]}) == len(calls["news"]), "news is fetched once per listed stock only"


def test_board_survives_failing_fetchers():
    from tests.test_price_action import _daily
    frames = {f"S{k:02d}": _daily(k) for k in range(24)}

    def boom(*a, **k):
        raise RuntimeError("no network")
    payload, _ = pb.build(frames, fo=set(frames), sectors={s: "Power" for s in frames}, news_fn=boom, global_fn=boom)
    assert payload["setups"] and all(r["macro"]["verdict"] for r in payload["setups"])
