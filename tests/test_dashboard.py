import subprocess
from datetime import datetime

import pandas as pd
import pytest

from signals import dashboard
from signals.publish import publish_directory
from signals.upstox_client import UpstoxClient, UpstoxError

NOW = datetime(2026, 10, 23, 11, 0, 0)


def _q(key, last, net, volume, high=None, low=None, ts=1761200000000):
    return {"instrument_token": key, "last_price": last, "net_change": net, "volume": volume,
            "ohlc": {"high": high or last, "low": low or last, "close": last}, "last_trade_time": ts}


def _snapshot(quotes, **kw):
    mapping = {f"K{i}": s for i, s in enumerate(["AAA", "BBB", "CCC", "DDD"])}
    industries = {"AAA": "Banks", "BBB": "Banks", "CCC": "IT", "DDD": "IT"}
    avg = kw.pop("avg", {"AAA": 1000.0, "BBB": 1000.0, "CCC": 4000.0})
    return dashboard.build_snapshot(quotes, mapping, industries, avg, kw.pop("fo", {"AAA", "CCC"}), NOW, kw.pop("levels", None))


def test_sector_change_is_the_average_with_breadth_and_ranked_best_first():
    snap = _snapshot([
        _q("K0", 102, 2, 500), _q("K1", 98, -2, 500),      # Banks: +2%, -2%  -> avg 0.0
        _q("K2", 110, 10, 800), _q("K3", 105, 5, 100),     # IT: +10%, +5%    -> avg ~7.5%
    ])
    assert [s["name"] for s in snap["sectors"]] == ["IT", "Banks"]
    it, banks = snap["sectors"]
    assert it["advancers"] == 2 and it["decliners"] == 0 and it["stocks"] == 2
    assert it["change_pct"] == pytest.approx((10 / 100 * 100 + 5 / 100 * 100) / 2, abs=0.5)
    assert banks["advancers"] == 1 and banks["decliners"] == 1
    assert it["turnover_cr"] == pytest.approx((110 * 800 + 105 * 100) / 1e7 * 1, abs=0.1)


def test_stocks_are_sorted_by_volume_with_turnover_relative_volume_and_fo_flag():
    snap = _snapshot([_q("K0", 100, 1, 500), _q("K1", 50, 0.5, 2000), _q("K2", 200, -4, 1000), _q("K3", 10, 0.1, 10)])
    assert [s["symbol"] for s in snap["stocks"]] == ["BBB", "CCC", "AAA", "DDD"]
    bbb = snap["stocks"][0]
    assert bbb["volume"] == 2000 and bbb["turnover_cr"] == pytest.approx(50 * 2000 / 1e7, abs=0.01)
    assert bbb["vol_vs_avg"] == 2.0 and bbb["fo"] is False
    assert snap["stocks"][1]["fo"] is True and snap["stocks"][1]["change_pct"] == pytest.approx(-1.96, abs=0.01)
    assert snap["stocks"][3]["vol_vs_avg"] is None      # no average volume known for DDD
    assert snap["stock_count"] == 4 and snap["has_fo"] is True


def test_unusable_quotes_are_skipped_and_prev_close_falls_back_to_ohlc():
    quotes = [
        _q("K0", 100, 1, 500),
        {"instrument_token": "UNKNOWN", "last_price": 5, "net_change": 0, "volume": 1},   # not in the map
        {"instrument_token": "K1", "last_price": 0, "net_change": 0, "volume": 5},         # no price
        {"instrument_token": "K2", "last_price": 110, "volume": 5, "ohlc": {"close": 100}},  # no net_change
        {"instrument_token": "K3", "last_price": 10, "net_change": 10, "volume": 5},       # prev close 0
    ]
    snap = _snapshot(quotes)
    assert {s["symbol"] for s in snap["stocks"]} == {"AAA", "CCC"}
    assert [s for s in snap["stocks"] if s["symbol"] == "CCC"][0]["change_pct"] == pytest.approx(10.0)


def test_unknown_sector_and_missing_fo_list():
    mapping = {"K9": "ZZZ"}
    snap = dashboard.build_snapshot([_q("K9", 10, 1, 5)], mapping, {}, {}, None, NOW)
    assert snap["sectors"][0]["name"] == "Other" and snap["has_fo"] is False
    assert snap["stocks"][0]["fo"] is False


def test_average_volume_uses_completed_sessions_only():
    idx = pd.bdate_range(end="2026-10-23", periods=30)
    df = pd.DataFrame({"volume": [100.0] * 29 + [9999.0]}, index=idx)      # today's partial row is the last
    out = dashboard.average_volumes({"AAA": df, "NEW": df.tail(3)}, today=pd.Timestamp("2026-10-23"))
    assert out == {"AAA": 100.0}                  # today's row excluded; 3 rows is too little history


class _FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class _FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append(params["instrument_key"].split(","))
        return self.responses.pop(0)


def _client(monkeypatch, responses):
    client = UpstoxClient("t")
    client._session = _FakeSession(responses)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return client


def test_get_quotes_batches_keys_and_flattens(monkeypatch):
    client = _client(monkeypatch, [
        _FakeResponse(200, {"data": {"NSE_EQ:A": {"instrument_token": "k0"}, "NSE_EQ:B": {"instrument_token": "k1"}}}),
        _FakeResponse(200, {"data": {"NSE_EQ:C": {"instrument_token": "k2"}}}),
    ])
    quotes = client.get_quotes(["k0", "k1", "k2"], batch_size=2)
    assert [q["instrument_token"] for q in quotes] == ["k0", "k1", "k2"]
    assert client._session.calls == [["k0", "k1"], ["k2"]]


def test_get_quotes_retries_when_rate_limited_then_gives_up(monkeypatch):
    client = _client(monkeypatch, [_FakeResponse(429), _FakeResponse(200, {"data": {"x": {"instrument_token": "k0"}}})])
    assert len(client.get_quotes(["k0"])) == 1
    client = _client(monkeypatch, [_FakeResponse(500)] * 5)
    with pytest.raises(UpstoxError):
        client.get_quotes(["k0"])


def test_publish_force_pushes_a_single_parentless_commit(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    site = tmp_path / "site"
    site.mkdir()
    for n in (1, 2):                                   # publishing twice must not accumulate history
        (site / "data.json").write_text(f'{{"n": {n}}}')
        publish_directory(str(site), "o/r", "secret-token", "dashboard-data", f"snapshot {n}", remote=str(remote))
    log = subprocess.run(["git", "--git-dir", str(remote), "log", "--format=%s", "dashboard-data"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert log == ["snapshot", "2"]
    shown = subprocess.run(["git", "--git-dir", str(remote), "show", "dashboard-data:data.json"],
                           capture_output=True, text=True, check=True).stdout
    assert shown == '{"n": 2}'


def test_publish_errors_do_not_leak_the_token(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "data.json").write_text("{}")
    with pytest.raises(RuntimeError) as err:
        publish_directory(str(site), "o/r", "secret-token", "b", "m", remote="https://x-access-token:secret-token@127.0.0.1:1/none.git")
    assert "secret-token" not in str(err.value) and "secret-token" not in repr(err.value.__cause__)


def test_publish_accepts_a_relative_directory(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "data.json").write_text("{}")
    monkeypatch.chdir(tmp_path)                       # how the workflow runs it: "dashboard_site"
    publish_directory("site", "o/r", "t", "dashboard-data", "m", remote=str(remote))
    shown = subprocess.run(["git", "--git-dir", str(remote), "show", "dashboard-data:data.json"],
                           capture_output=True, text=True, check=True).stdout
    assert shown == "{}"


def test_previous_levels_come_from_the_last_completed_session():
    idx = pd.bdate_range(end="2026-10-23", periods=5)
    df = pd.DataFrame({"open": 1.0, "high": [10, 11, 12, 13, 99], "low": [1, 2, 3, 4, 0.5], "close": [5, 6, 7, 8, 9], "volume": 1.0}, index=idx)
    out = dashboard.previous_levels({"AAA": df}, pd.Timestamp("2026-10-23"))      # today's own row is excluded
    assert out["AAA"] == {"pdh": 13.0, "pdl": 4.0, "pdc": 8.0}
    assert dashboard.previous_levels({"AAA": df}, pd.Timestamp("2026-10-01")) == {}


def test_snapshot_keeps_previous_day_levels_only_when_history_agrees_with_the_quote():
    levels = {"AAA": {"pdh": 105.0, "pdl": 95.0, "pdc": 100.0},
              "BBB": {"pdh": 205.0, "pdl": 195.0, "pdc": 150.0},      # history is a session stale: close disagrees with the quote
              "CCC": {"pdh": 55.0, "pdl": 45.0, "pdc": 50.2}}         # within 0.5% of the quote's previous close: fine
    quotes = [_q("K0", 102, 2, 500), _q("K1", 201, 1, 500), _q("K2", 51, 1, 500), _q("K3", 10, 1, 500)]
    snap = _snapshot(quotes, levels=levels)
    rows = {s["symbol"]: s for s in snap["stocks"]}
    assert (rows["AAA"]["pdh"], rows["AAA"]["pdl"]) == (105.0, 95.0)
    assert rows["BBB"]["pdh"] is None and rows["BBB"]["pdl"] is None
    assert rows["CCC"]["pdh"] == 55.0
    assert rows["DDD"]["pdh"] is None                                     # no history for it at all
