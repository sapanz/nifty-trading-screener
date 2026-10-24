import pytest

from signals import rfactor as rf


def _stock(oi_start, oi_end, p_start, p_end, vol_per_slot, av, fo=1):
    n = 75
    o = [oi_start + (oi_end - oi_start) * i / 74 for i in range(n)]
    p = [p_start + (p_end - p_start) * i / 74 for i in range(n)]
    return {"fo": fo, "pc": p_start, "av": av, "c": [round(x, 2) for x in p], "p": [round(x, 2) for x in p],
            "o": [round(x) for x in o], "v": [vol_per_slot] * n, "s": "X"}


def _day(n=20):
    stocks = {}
    for i in range(n):
        # stock i: more OI added, more volume and a bigger rise the higher i is
        stocks[f"S{i}"] = _stock(1_000_000, 1_000_000 * (1 + 0.01 * i), 100, 100 * (1 + 0.002 * i), 1000 * (i + 1), 100_000)
    stocks["FALLING_OI"] = _stock(1_000_000, 900_000, 100, 102, 50_000, 100_000)
    stocks["NOT_FO"] = _stock(1_000_000, 2_000_000, 100, 110, 90_000, 100_000, fo=0)
    return {"date": "2026-10-22", "stocks": stocks}


def test_slot_for_is_the_candle_that_ends_at_that_time():
    assert rf.slot_for("09:20") == 0 and rf.slot_for("10:00") == 8 and rf.slot_for("15:30") == 74


def test_percentile_ranks_average_ties():
    assert rf.percentile_ranks([10, 20, 30]) == [0.0, 50.0, 100.0]
    assert rf.percentile_ranks([5, 5, 9]) == [25.0, 25.0, 100.0]
    assert rf.percentile_ranks([7]) == [50.0]


def test_r_is_highest_where_volume_oi_and_price_move_are_all_highest():
    rows = {r["symbol"]: r for r in rf.snapshot_rows(_day(), rf.slot_for("11:00"))}
    assert "NOT_FO" not in rows                               # no OI -> not ranked
    # S19 leads on all three, but FALLING_OI has even more volume, so its volume rank (and R) is a touch under 100
    assert rows["S19"]["r"] >= 95 and rows["S19"]["bias"] == "long"
    assert rows["S19"]["r"] == max(r["r"] for r in rows.values())
    assert rows["S19"]["r"] > rows["S10"]["r"] > rows["S1"]["r"]
    assert rows["FALLING_OI"]["r"] == 0 and rows["FALLING_OI"]["bias"] == "cover"


def test_too_few_stocks_gives_no_ranking():
    day = _day(5)
    assert rf.snapshot_rows(day, 20) == []


def test_forward_return_is_direction_adjusted_and_bounded_by_the_session():
    day = {"stocks": {"A": {"c": [100.0] * 30 + [110.0] * 45}}}
    assert rf.forward_return(day, "A", 10, 6, 1) == 0.0
    assert rf.forward_return(day, "A", 29, 6, 1) == pytest.approx(10.0)
    assert rf.forward_return(day, "A", 29, 6, -1) == pytest.approx(-10.0)
    assert rf.forward_return(day, "A", 29, None, 1) == pytest.approx(10.0)
    assert rf.forward_return(day, "A", 72, 6, 1) is None        # would run past the close


def test_backtest_groups_returns_by_day_and_summary_renders():
    days = [_day(), _day()]
    for d in days:                                         # price keeps rising after 11:00 for the long picks
        for s in d["stocks"].values():
            s["c"] = [c * (1 + 0.0005 * max(0, i - rf.slot_for("11:00"))) for i, c in enumerate(s["c"])]
    results = rf.run_backtest(days, ["11:00"], top_n=3)
    top = results["Top by R-factor"]["next 30 min"]
    assert len(top) == 6 and {d for d, _ in top} == {0, 1} and all(r > 0 for _, r in top)
    text = rf.summarize(results, 2, ["11:00"], 3, 0.10)
    assert "Top by R-factor" in text and "day-clustered" in text
