from datetime import datetime

import pandas as pd

from src.data_fetcher import _build_latest, _fetch_spot
from src.indicators import calculate_indicators


class FakeAK:
    def fund_etf_spot_em(self):
        return pd.DataFrame({
            "代码": ["561380"],
            "最新价": [1.234],
            "涨跌幅": [0.5],
            "今开": [1.2],
            "最高": [1.25],
            "最低": [1.19],
            "昨收": [1.228],
            "成交量": [1000],
            "成交额": [1234],
            "主力净流入-净额": [-8937528],
        })


def test_realtime_snapshot_maps_single_day_main_fund_flow_without_trend_claim():
    asset = {"symbol": "561380", "name": "示例 ETF", "strategy": {}, "position": {}}
    spot, warnings = _fetch_spot(FakeAK(), asset, now=datetime(2026, 8, 9, 10))
    latest = _build_latest(asset, pd.DataFrame({"date": ["2026-08-08"], "close": [1.2]}), spot)
    history = pd.DataFrame({
        "date": pd.date_range("2026-07-01", periods=30, freq="B"),
        "close": [1 + index * 0.01 for index in range(30)],
        "volume": [100] * 30,
    })

    indicators = calculate_indicators(asset, history, latest)

    assert not warnings
    assert latest.main_fund_flow == -8937528
    assert indicators.fund_flow_status == "OUTFLOW"
    assert indicators.main_fund_flow == -8937528
    assert "单日快照" in indicators.fund_flow_impact
    assert "趋势判断" in indicators.fund_flow_impact
