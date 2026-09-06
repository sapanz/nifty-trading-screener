import pandas as pd

from signals import data


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
