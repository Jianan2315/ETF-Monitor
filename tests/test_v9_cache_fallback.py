from pathlib import Path

from src.data_fetcher import _load_history_cache, _market_phase


def test_returns_empty_when_asset_cache_is_missing(tmp_path: Path):
    frame, cached_at, source = _load_history_cache(
        tmp_path,
        {"data": {"cache_directory": "data/assets/demo/cache"}},
        "561380",
    )
    assert frame is None
    assert cached_at is None
    assert source is None


def test_intraday_argument_does_not_force_realtime_before_open():
    from datetime import datetime

    assert (
        _market_phase(
            datetime(2026, 7, 15, 8, 0),
            "intraday",
        )
        == "PRE_OPEN"
    )
