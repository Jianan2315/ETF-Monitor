from datetime import datetime
import json
import sys
from types import SimpleNamespace

import pandas as pd

from src.data_fetcher import fetch_asset_data


def test_midday_uses_cache_and_tolerates_missing_open(tmp_path, monkeypatch):
    cache_dir = tmp_path / "data" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "561380_daily.json").write_text(
        json.dumps(
            {
                "symbol": "561380",
                "cached_at": "2026-07-15T05:07:43",
                "rows": [
                    {
                        "date": "2026-07-14",
                        "open": 0.747,
                        "close": 0.747,
                        "high": 0.750,
                        "low": 0.740,
                        "volume": 100,
                        "amount": 74.7,
                        "daily_change_pct": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def history_fail(**kwargs):
        raise ConnectionError("historical unavailable")

    def spot_ok():
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

    def minute_ok(**kwargs):
        return pd.DataFrame(
            [
                {
                    "时间": "2026-07-15 09:30:00",
                    "开盘": 0.750,
                    "收盘": 0.750,
                    "最高": 0.750,
                    "最低": 0.750,
                    "成交量": 10,
                    "成交额": 7.5,
                },
                {
                    "时间": "2026-07-15 11:30:00",
                    "开盘": 0.750,
                    "收盘": 0.765,
                    "最高": 0.765,
                    "最低": 0.750,
                    "成交量": 20,
                    "成交额": 15.3,
                },
            ]
        )

    fake_ak = SimpleNamespace(
        fund_etf_hist_em=history_fail,
        fund_etf_spot_em=spot_ok,
        fund_etf_hist_min_em=minute_ok,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    config = {
        "mock_mode": False,
        "timezone": "Asia/Shanghai",
        "schedule": {"timezone": "Asia/Shanghai"},
        "data": {
            "history_days": 1000,
            "adjust": "qfq",
            "cache_directory": "data/cache",
            "retry": {"max_attempts": 1, "delays_seconds": []},
        },
        "storage": {"latest_file": "data/latest.json"},
    }
    asset = {"symbol": "561380", "name": "电网设备ETF"}

    bundle = fetch_asset_data(
        asset,
        config,
        mode="auto",
        project_root=tmp_path,
        now=datetime(2026, 7, 15, 12, 20),
    )

    assert bundle.data_phase == "MIDDAY_BREAK"
    assert bundle.using_local_cache is True
    assert bundle.latest.price == 0.765
    assert bundle.latest.daily_change_pct == 2.41
    assert bundle.history.iloc[-1]["date"].strftime("%Y-%m-%d") == "2026-07-15"
    assert bundle.intraday is not None and len(bundle.intraday) == 2
