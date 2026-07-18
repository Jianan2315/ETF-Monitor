from datetime import datetime

import pandas as pd

from src.data_fetcher import (
    _build_daily_ohlc_approximation,
    _history_api_success_today,
    _save_provider_state,
)


def test_history_success_today_uses_provider_state_date(tmp_path):
    config = {
        "data": {
            "provider_state_file": "data/provider_state.json",
        }
    }
    _save_provider_state(
        tmp_path,
        config,
        {
            "eastmoney_history": {
                "last_success": "2026-07-16T08:30:00",
            }
        },
    )
    assert _history_api_success_today(
        tmp_path,
        config,
        datetime(2026, 7, 16, 9, 0),
    ) is True
    assert _history_api_success_today(
        tmp_path,
        config,
        datetime(2026, 7, 17, 9, 0),
    ) is False


def test_previous_day_ohlc_approximation_is_explicit_four_points():
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-07-15"]),
        "open": [0.76],
        "high": [0.78],
        "low": [0.74],
        "close": [0.75],
    })
    frame = _build_daily_ohlc_approximation(
        history,
        datetime(2026, 7, 15),
    )
    assert len(frame) == 4
    assert list(frame["price"]) == [0.76, 0.78, 0.74, 0.75]
