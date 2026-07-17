from datetime import datetime
from pathlib import Path

import pandas as pd

from run import format_market_time, format_run_time
from src.data_fetcher import _history_is_fresh_enough


def test_closed_market_time_is_date_only():
    value = datetime(2026, 7, 14, 0, 0, 0)
    assert format_market_time(value, "AFTER_CLOSE") == "2026-07-14"


def test_intraday_market_time_keeps_clock():
    value = datetime(2026, 7, 15, 14, 45, 7)
    assert format_market_time(value, "PM_SESSION") == "2026-07-15 14:45:07"


def test_run_time_is_actual_datetime():
    value = datetime(2026, 7, 15, 5, 23, 9)
    assert format_run_time(value) == "2026-07-15 05:23:09"


def test_stale_history_is_detected_before_market():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-13"]),
            "close": [1.0],
        }
    )
    fresh, latest, expected = _history_is_fresh_enough(
        frame,
        datetime(2026, 7, 15, 8, 0),
        "PRE_OPEN",
    )
    assert fresh is False
    assert latest == "2026-07-13"
    assert expected == "2026-07-14"


def test_previous_business_day_is_fresh_before_market():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-14"]),
            "close": [1.0],
        }
    )
    fresh, latest, expected = _history_is_fresh_enough(
        frame,
        datetime(2026, 7, 15, 8, 0),
        "PRE_OPEN",
    )
    assert fresh is True
    assert latest == expected == "2026-07-14"
