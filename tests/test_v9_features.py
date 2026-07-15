from datetime import datetime

from src.data_fetcher import _market_phase


def test_market_phase_new_names():
    assert _market_phase(datetime(2026, 7, 15, 10, 0), "auto") == "AM_SESSION"
    assert _market_phase(datetime(2026, 7, 15, 12, 20), "auto") == "MIDDAY_BREAK"
    assert _market_phase(datetime(2026, 7, 18, 10, 0), "auto") == "WEEKEND"
