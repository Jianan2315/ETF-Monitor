import json
from pathlib import Path

from src.data_fetcher import _load_history_cache, _market_phase


def test_loads_legacy_latest_json_when_new_cache_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "latest.json").write_text(
        json.dumps(
            {
                "561380": {
                    "generated_at": "2026-07-15T15:10:00",
                    "chart_data": [
                        {"date": "2026-07-14", "price": 1.01},
                        {"date": "2026-07-15", "price": 1.02},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    config = {
        "data": {"cache_directory": "data/cache"},
        "storage": {"latest_file": "data/latest.json"},
    }

    frame, cached_at, source = _load_history_cache(
        tmp_path,
        config,
        "561380",
    )

    assert frame is not None
    assert len(frame) == 2
    assert frame.iloc[-1]["close"] == 1.02
    assert cached_at == "2026-07-15T15:10:00"
    assert source == "data/latest.json"


def test_intraday_argument_does_not_force_realtime_before_open():
    from datetime import datetime

    assert (
        _market_phase(
            datetime(2026, 7, 15, 8, 0),
            "intraday",
        )
        == "PRE_OPEN"
    )
