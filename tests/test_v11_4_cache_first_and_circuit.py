from datetime import datetime, timedelta

import pandas as pd

from src.data_fetcher import (
    _cache_covers_previous_business_day,
    _history_circuit_status,
    _merge_spot_into_history,
    _open_history_circuit,
)


def test_cache_previous_business_day_is_usable_intraday():
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-07-14", "2026-07-15"]),
        "close": [1.0, 1.1],
    })
    ok, latest, required = _cache_covers_previous_business_day(
        history,
        datetime(2026, 7, 16, 10, 0),
    )
    assert ok is True
    assert latest == "2026-07-15"
    assert required == "2026-07-15"


def test_close_snapshot_is_marked_provisional():
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-07-15"]),
        "open": [1.0],
        "close": [1.0],
        "high": [1.0],
        "low": [1.0],
        "volume": [1.0],
        "amount": [1.0],
        "daily_change_pct": [0.0],
    })
    spot = {
        "timestamp": datetime(2026, 7, 16, 15, 10),
        "open": 1.01,
        "price": 1.02,
        "high": 1.03,
        "low": 1.00,
        "volume": 100.0,
        "amount": 102.0,
        "daily_change_pct": 2.0,
    }
    merged = _merge_spot_into_history(
        history,
        spot,
        provisional=True,
    )
    assert bool(merged.iloc[-1]["provisional"]) is True
    assert merged.iloc[-1]["source"] == "realtime_close_snapshot"


def test_history_circuit_roundtrip(tmp_path):
    config = {
        "data": {
            "provider_state_file": "data/provider_state.json",
            "history_circuit_breaker_hours": 6,
        }
    }
    now = datetime(2026, 7, 16, 10, 0)
    _open_history_circuit(
        tmp_path,
        config,
        now,
        "ANTI_BOT_CHALLENGE",
        ["REMOTE_DISCONNECTED"],
    )
    open_now, until = _history_circuit_status(
        tmp_path,
        config,
        now + timedelta(minutes=1),
    )
    assert open_now is True
    assert until is not None
