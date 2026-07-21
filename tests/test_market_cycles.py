import pandas as pd

from src.market_cycles import analyze_market_cycles


def history(prices):
    return pd.DataFrame({"date": pd.date_range("2026-01-01", periods=len(prices), freq="D"), "close": prices})


def test_high_and_low_use_later_confirmation_dates_without_lookahead():
    analysis = analyze_market_cycles(history([100, 110, 100, 95, 103]), 103, confirmation_pct=8)
    assert analysis["events"][0]["kind"] == "HIGH"
    assert analysis["events"][0]["date"] == "2026-01-02"
    assert analysis["events"][0]["confirmed_date"] == "2026-01-03"
    assert analysis["events"][1]["kind"] == "LOW"
    assert analysis["events"][1]["date"] == "2026-01-04"
    assert analysis["events"][1]["confirmed_date"] == "2026-01-05"


def test_cycle_distributions_and_current_recovery_are_calculated():
    analysis = analyze_market_cycles(history([100, 110, 100, 90, 100, 108]), 108, confirmation_pct=8)
    assert analysis["drawdowns"] == [round((90 / 110 - 1) * -100, 4)]
    assert analysis["recovery_pct"] == round((108 - 90) / (110 - 90) * 100, 4)
