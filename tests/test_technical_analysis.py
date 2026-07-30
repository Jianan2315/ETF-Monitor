import pandas as pd

from src.technical_analysis import moving_average_facts, relative_strength_facts, volume_facts


def test_moving_average_facts_report_price_relation_slope_and_alignment():
    close = pd.Series(range(1, 131), dtype="float64")
    facts = moving_average_facts(close)

    assert facts["periods"]["5"]["price_relation"] == "ABOVE"
    assert facts["periods"]["20"]["slope"] == "UP"
    assert facts["relationships"]["5_10"] == "ABOVE"
    assert facts["alignment"] == "BULLISH"


def test_volume_facts_compare_latest_volume_to_prior_windows():
    facts = volume_facts(pd.Series([10] * 20 + [30], dtype="float64"))

    assert facts["available"] is True
    assert facts["ratios"]["5"]["ratio"] == 3
    assert facts["ratios"]["20"]["ratio"] == 3


def test_relative_strength_uses_overlapping_return_history_only():
    dates = pd.date_range("2026-01-01", periods=8, freq="D")
    history = pd.DataFrame({"date": dates, "close": [100, 101, 102, 103, 104, 105, 106, 108]})
    benchmark = pd.DataFrame({"date": dates, "close": [100, 100, 101, 102, 102, 103, 104, 105]})

    facts = relative_strength_facts(history, benchmark)

    assert facts["available"] is True
    assert facts["periods"]["5"]["relation"] == "STRONGER"
