from datetime import datetime

import pandas as pd

from src.data_fetcher import (
    _adjust_unadjusted_history_for_splits,
    _fetch_intraday_sina_for_date,
)


def test_unadjusted_split_is_back_adjusted_before_return_calculation():
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-06-24", "2026-06-25", "2026-06-26"]),
        "open": [2.35, 0.95, 0.96],
        "high": [2.40, 0.97, 0.98],
        "low": [2.30, 0.94, 0.95],
        "close": [2.373, 0.951, 0.970],
        "volume": [100.0, 250.0, 220.0],
        "amount": [237.3, 237.75, 213.4],
    })

    adjusted, notes = _adjust_unadjusted_history_for_splits(frame)

    assert notes
    assert round(float(adjusted.iloc[0]["close"]), 4) == 0.9492
    assert abs(float(adjusted.iloc[1]["daily_change_pct"])) < 1.0


def test_sina_intraday_fallback_filters_requested_trading_date():
    class FakeAk:
        @staticmethod
        def stock_zh_a_minute(**kwargs):
            assert kwargs["symbol"] == "sh561380"
            assert kwargs["period"] == "5"
            return pd.DataFrame({
                "day": [
                    "2026-07-16 14:55:00",
                    "2026-07-17 09:30:00",
                    "2026-07-17 09:35:00",
                ],
                "open": [0.90, 0.91, 0.92],
                "high": [0.91, 0.93, 0.94],
                "low": [0.89, 0.90, 0.91],
                "close": [0.905, 0.925, 0.935],
                "volume": [100, 200, 300],
            })

    result = _fetch_intraday_sina_for_date(
        FakeAk(), {"symbol": "561380"}, datetime(2026, 7, 17), "5"
    )

    assert len(result) == 2
    assert result["time"].dt.strftime("%Y-%m-%d").unique().tolist() == ["2026-07-17"]
