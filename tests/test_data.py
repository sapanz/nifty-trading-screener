import pandas as pd
import pytest

from signals import config, data


def _daily_df():
    dates = pd.date_range("2024-01-01", periods=10, freq="B")  # two-ish trading weeks
    return pd.DataFrame(
        {
            "open": range(100, 110),
            "high": [v + 2 for v in range(100, 110)],
            "low": [v - 2 for v in range(100, 110)],
            "close": range(101, 111),
            "volume": [1000] * 10,
        },
        index=dates,
    )


def test_to_weekly_aggregates_ohlcv_correctly():
    daily = {"TESTCO": _daily_df()}
    weekly = data.to_weekly(daily)
    df = weekly["TESTCO"]

    first_week = daily["TESTCO"].loc["2024-01-01":"2024-01-05"]
    assert df.iloc[0]["open"] == first_week.iloc[0]["open"]
    assert df.iloc[0]["close"] == first_week.iloc[-1]["close"]
    assert df.iloc[0]["high"] == first_week["high"].max()
    assert df.iloc[0]["low"] == first_week["low"].min()
    assert df.iloc[0]["volume"] == first_week["volume"].sum()


def test_to_monthly_aggregates_ohlcv_correctly():
    daily = {"TESTCO": _daily_df()}
    monthly = data.to_monthly(daily)
    df = monthly["TESTCO"]

    assert len(df) == 1  # all 10 business days fall in January 2024
    full = daily["TESTCO"]
    assert df.iloc[0]["open"] == full.iloc[0]["open"]
    assert df.iloc[0]["close"] == full.iloc[-1]["close"]
    assert df.iloc[0]["high"] == full["high"].max()
    assert df.iloc[0]["low"] == full["low"].min()
    assert df.iloc[0]["volume"] == full["volume"].sum()


def test_resample_handles_empty_frame():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert data.to_weekly({"TESTCO": empty})["TESTCO"].empty
    assert data.to_monthly({"TESTCO": empty})["TESTCO"].empty


class _FakeClient:
    """Stands in for UpstoxClient: every symbol whose instrument_key starts
    with 'FAIL' raises, everything else returns a small valid frame."""

    def get_daily_history(self, instrument_key, years):
        if instrument_key.startswith("FAIL"):
            raise RuntimeError("simulated blocked request")
        return _daily_df()

    def get_weekly_history(self, instrument_key, years):
        if instrument_key.startswith("FAIL"):
            raise RuntimeError("simulated blocked request")
        return _daily_df()

    def get_monthly_history(self, instrument_key, years):
        if instrument_key.startswith("FAIL"):
            raise RuntimeError("simulated blocked request")
        return _daily_df()

    def throttle(self):
        pass


def test_fetch_daily_skips_individual_failures():
    instrument_map = {"GOOD1": "OK1", "BAD1": "FAIL1", "GOOD2": "OK2"}
    result = data.fetch_daily(_FakeClient(), instrument_map)
    assert set(result.keys()) == {"GOOD1", "GOOD2"}


def test_fetch_daily_circuit_breaker_aborts_on_systemic_failure(monkeypatch):
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_SAMPLE_SIZE", 5)
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_FAILURE_RATIO", 0.8)

    # 5 symbols, all failing -> should abort right after the 5th, not
    # continue on to the remaining 95.
    instrument_map = {f"BAD{i}": f"FAIL{i}" for i in range(5)}
    instrument_map.update({f"GOOD{i}": f"OK{i}" for i in range(95)})

    with pytest.raises(RuntimeError, match="blocking requests wholesale"):
        data.fetch_daily(_FakeClient(), instrument_map)


def test_fetch_weekly_history_skips_individual_failures():
    instrument_map = {"GOOD1": "OK1", "BAD1": "FAIL1", "GOOD2": "OK2"}
    result = data.fetch_weekly_history(_FakeClient(), instrument_map)
    assert set(result.keys()) == {"GOOD1", "GOOD2"}


def test_fetch_weekly_history_circuit_breaker_aborts_on_systemic_failure(monkeypatch):
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_SAMPLE_SIZE", 5)
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_FAILURE_RATIO", 0.8)

    instrument_map = {f"BAD{i}": f"FAIL{i}" for i in range(5)}
    instrument_map.update({f"GOOD{i}": f"OK{i}" for i in range(95)})

    with pytest.raises(RuntimeError, match="blocking requests wholesale"):
        data.fetch_weekly_history(_FakeClient(), instrument_map)


def test_fetch_monthly_ath_history_skips_individual_failures():
    instrument_map = {"GOOD1": "OK1", "BAD1": "FAIL1", "GOOD2": "OK2"}
    result = data.fetch_monthly_ath_history(_FakeClient(), instrument_map)
    assert set(result.keys()) == {"GOOD1", "GOOD2"}


def test_fetch_monthly_ath_history_circuit_breaker_aborts_on_systemic_failure(monkeypatch):
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_SAMPLE_SIZE", 5)
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_FAILURE_RATIO", 0.8)

    instrument_map = {f"BAD{i}": f"FAIL{i}" for i in range(5)}
    instrument_map.update({f"GOOD{i}": f"OK{i}" for i in range(95)})

    with pytest.raises(RuntimeError, match="blocking requests wholesale"):
        data.fetch_monthly_ath_history(_FakeClient(), instrument_map)


class _FakeInstrumentClient:
    def __init__(self, full_map):
        self._full_map = full_map

    def fetch_instrument_map(self):
        return self._full_map


def test_build_instrument_map_keeps_only_requested_symbols():
    client = _FakeInstrumentClient({"GOOD1": "KEY1", "GOOD2": "KEY2", "OTHERSTOCK": "KEY3"})
    mapping = data.build_instrument_map(client, ["GOOD1", "GOOD2"])
    assert mapping == {"GOOD1": "KEY1", "GOOD2": "KEY2"}


def test_build_instrument_map_raises_when_almost_nothing_matches():
    # Mirrors the real incident: a filter bug zeroed out the whole
    # instrument master, matching 0 of ~500 requested symbols.
    client = _FakeInstrumentClient({})
    symbols = [f"SYM{i}" for i in range(500)]
    with pytest.raises(RuntimeError, match="instrument master likely changed shape"):
        data.build_instrument_map(client, symbols)
