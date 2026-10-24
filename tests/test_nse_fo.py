from datetime import date

import numpy as np
import pandas as pd
import pytest

from signals import nse_fo


def bhav(rows):
    """rows: (type, symbol, expiry, option, close, prev, oi, doi, vol)"""
    return pd.DataFrame(rows, columns=["FinInstrmTp", "TckrSymb", "XpryDt", "OptnTp", "ClsPric", "PrvsClsgPric",
                                       "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol"])


def _day():
    return bhav([
        # AAA: near month 100 -> 102 (+2%); OI 1000 (+100) in the near month, 500 (+50) next month
        ("STF", "AAA", "2025-03-27", None, 102.0, 100.0, 1000, 100, 50),
        ("STF", "AAA", "2025-04-24", None, 103.0, 101.0, 500, 50, 10),
        # near-month options: puts 300, calls 200 -> PCR 1.5; the far month must not count
        ("STO", "AAA", "2025-03-27", "PE", 5.0, 4.0, 100, 0, 1),
        ("STO", "AAA", "2025-03-27", "PE", 6.0, 5.0, 200, 0, 1),
        ("STO", "AAA", "2025-03-27", "CE", 7.0, 6.0, 200, 0, 1),
        ("STO", "AAA", "2025-04-24", "CE", 9.0, 8.0, 9999, 0, 1),
        # an index contract that must be ignored
        ("IDF", "NIFTY", "2025-03-27", None, 22000.0, 21900.0, 5, 1, 1),
    ])


def test_oi_by_symbol_futures_fields():
    out = nse_fo.oi_by_symbol(_day())
    assert list(out.index) == ["AAA"]
    row = out.loc["AAA"]
    assert row["fut_ret"] == pytest.approx(0.02)                            # nearest expiry's close vs previous close
    assert row["oi_total"] == 1500                                          # all expiries
    assert row["oi_chg_pct"] == pytest.approx(150 / 1350)                   # (100 + 50) / (1500 - 150)
    assert row["fut_vol"] == 60
    assert row["pcr"] == pytest.approx(300 / 200)                           # near-month options only


def test_rolling_oi_into_the_next_month_is_not_read_as_unwinding():
    day = bhav([
        ("STF", "AAA", "2025-03-27", None, 100.0, 100.0, 400, -100, 5),     # near month bleeds 100 ...
        ("STF", "AAA", "2025-04-24", None, 100.0, 100.0, 600, 100, 5),      # ... into the next
    ])
    assert nse_fo.oi_by_symbol(day).loc["AAA", "oi_chg_pct"] == pytest.approx(0.0)


def test_missing_columns_fail_loudly():
    with pytest.raises(ValueError, match="missing columns"):
        nse_fo.oi_by_symbol(pd.DataFrame({"TckrSymb": ["AAA"]}))


@pytest.mark.parametrize("ret,oi,code", [
    (0.02, 0.05, 1),       # price up, OI up: long build-up
    (-0.02, 0.05, -1),     # price down, OI up: short build-up
    (0.02, -0.05, 2),      # price up, OI down: short covering
    (-0.02, -0.05, -2),    # price down, OI down: long unwinding
    (0.001, 0.05, 0),      # price barely moved
    (0.02, 0.002, 0),      # OI barely moved
])
def test_buildup_codes_need_both_moves_to_be_material(ret, oi, code):
    assert nse_fo.buildup_code([ret], [oi])[0] == code


def _oi_table():
    rows = []
    for i, d in enumerate(pd.bdate_range("2025-03-03", periods=8)):          # Mon 3 Mar .. Wed 12 Mar
        rows.append({"date": d, "symbol": "AAA", "oi_total": 1000.0 * (1 + 0.1 * i), "oi_chg_pct": 0.1 if i % 2 else -0.1,
                     "fut_ret": 0.02 if i % 2 else -0.02, "fut_vol": 1.0, "pcr": 1.0 + 0.1 * i})
    return pd.DataFrame(rows)


def _sessions(dates):
    return pd.DataFrame({"date": pd.to_datetime(dates), "symbol": "AAA", "t": "10:00"})


def test_a_session_only_sees_the_previous_trading_days_oi():
    out = nse_fo.attach_prior_day_oi(_sessions(["2025-03-04", "2025-03-10"]), _oi_table())
    tue, mon = out.iloc[0], out.iloc[1]
    assert tue["oi_date"] == pd.Timestamp("2025-03-03")                     # strictly before the session, never the same day
    assert mon["oi_date"] == pd.Timestamp("2025-03-07")                     # Monday reads Friday's file
    assert mon["pcr"] == pytest.approx(1.0 + 0.1 * 4)                       # Fri 7 Mar is index 4


def test_prior_day_features_derived_from_the_oi_history():
    out = nse_fo.attach_prior_day_oi(_sessions(["2025-03-11"]), _oi_table()).iloc[0]    # reads Mon 10 Mar = index 5
    assert out["oi_date"] == pd.Timestamp("2025-03-10")
    assert out["oi_chg_3d"] == pytest.approx(1500.0 / 1200.0 - 1)           # vs 3 trading days earlier (index 2)
    assert out["pcr_chg"] == pytest.approx(0.1)
    assert out["buildup"] == 1 and out["oi_dir"] == pytest.approx(0.1)      # index 5 is odd: price up, OI +10%


def test_stale_oi_is_not_carried_forward_across_a_long_gap():
    out = nse_fo.attach_prior_day_oi(_sessions(["2025-04-15"]), _oi_table())
    assert pd.isna(out.iloc[0]["oi_date"]) and pd.isna(out.iloc[0]["buildup"])


def test_empty_oi_table_leaves_the_features_untouched():
    df = _sessions(["2025-03-04"])
    assert nse_fo.attach_prior_day_oi(df, pd.DataFrame()).equals(df)


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class _Session:
    def __init__(self, resp):
        self.resp, self.calls = resp, 0

    def get(self, *a, **k):
        self.calls += 1
        return self.resp


def test_fetch_bhavcopy_treats_404_as_a_holiday_and_other_errors_as_failures(monkeypatch):
    monkeypatch.setattr(nse_fo.time, "sleep", lambda s: None)
    assert nse_fo.fetch_bhavcopy(_Session(_Resp(404)), date(2025, 1, 26)) is None
    sess = _Session(_Resp(403))
    with pytest.raises(RuntimeError, match="Could not fetch"):
        nse_fo.fetch_bhavcopy(sess, date(2025, 3, 3), retries=2)
    assert sess.calls == 2


def test_build_oi_table_caches_each_day_and_skips_weekends_and_holidays(tmp_path, monkeypatch):
    monkeypatch.setattr(nse_fo.time, "sleep", lambda s: None)
    monkeypatch.setattr(nse_fo.requests.Session, "get", lambda self, *a, **k: None)     # the homepage warm-up
    calls = []

    def fake_fetch(session, d, retries=3):
        calls.append(d)
        return None if d == date(2025, 3, 5) else _day()                    # Wed 5 Mar: pretend it's a holiday

    monkeypatch.setattr(nse_fo, "fetch_bhavcopy", fake_fetch)
    out = nse_fo.build_oi_table(date(2025, 3, 3), date(2025, 3, 9), str(tmp_path))      # Mon .. Sun
    assert sorted(out["date"].dt.date.unique()) == [date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 6), date(2025, 3, 7)]
    assert len(calls) == 5                                                  # five weekdays; the weekend was never requested
    calls.clear()
    again = nse_fo.build_oi_table(date(2025, 3, 3), date(2025, 3, 9), str(tmp_path))
    assert calls == [] and len(again) == len(out)                           # served entirely from the cache, holiday included
