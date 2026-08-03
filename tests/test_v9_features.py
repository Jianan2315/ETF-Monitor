from datetime import datetime

from src.market_clock import classify_market_phase


def test_market_phase_new_names():
    assert classify_market_phase(datetime(2026, 7, 15, 10, 0)) == "AM_SESSION"
    assert classify_market_phase(datetime(2026, 7, 15, 12, 20)) == "MIDDAY_BREAK"
    assert classify_market_phase(datetime(2026, 7, 18, 10, 0)) == "WEEKEND"
