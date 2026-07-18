from datetime import datetime

import pandas as pd

from src.data_fetcher import _enrich_spot, _fetch_spot


class FakeAk:
    @staticmethod
    def fund_etf_spot_em():
        return pd.DataFrame(
            [
                {
                    "代码": "561380",
                    "最新价": 0.765,
                    "涨跌幅": 2.41,
                    "今开": None,
                    "最高": None,
                    "最低": None,
                    "昨收": 0.747,
                    "成交量": None,
                    "成交额": None,
                }
            ]
        )


def test_only_latest_price_is_required():
    spot, warnings = _fetch_spot(
        FakeAk(),
        {"symbol": "561380"},
        datetime(2026, 7, 15, 12, 20),
    )
    assert spot["price"] == 0.765
    assert spot["open"] is None
    assert any("今开" in warning for warning in warnings)


def test_optional_fields_receive_fallback_values():
    history = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-14"]),
            "close": [0.747],
        }
    )
    intraday = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-07-15 09:30", "2026-07-15 11:30"]),
            "price": [0.750, 0.765],
            "open": [0.750, 0.750],
        }
    )
    spot = {
        "timestamp": datetime(2026, 7, 15, 12, 20),
        "price": 0.765,
        "daily_change_pct": None,
        "open": None,
        "high": None,
        "low": None,
        "previous_close": None,
        "volume": None,
        "amount": None,
    }
    enriched, warnings = _enrich_spot(spot, history, intraday)
    assert enriched["open"] == 0.750
    assert enriched["high"] == 0.765
    assert enriched["low"] == 0.750
    assert enriched["volume"] == 0.0
    assert enriched["daily_change_pct"] > 0
    assert warnings
