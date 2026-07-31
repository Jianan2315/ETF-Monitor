from datetime import datetime, timedelta

import pandas as pd

from src.research_data import collect_research_snapshot


class NonferrousAK:
    def futures_zh_daily_sina(self, symbol):
        assert symbol == "CU0"
        return pd.DataFrame({
            "date": pd.date_range("2026-04-01", periods=70, freq="B"),
            "close": list(range(100, 170)),
        })

    def macro_euro_lme_stock(self):
        return pd.DataFrame({
            "日期": pd.date_range("2026-01-01", periods=70, freq="B"),
            "铜-库存": list(range(200, 270)),
        })

    def macro_china_pmi(self):
        return pd.DataFrame({
            "月份": ["2026年06月份", "2026年05月份"],
            "制造业-指数": [49.8, 50.1],
        })


class GridAK:
    def fund_etf_spot_em(self):
        return pd.DataFrame({
            "代码": ["561380", "510300"],
            "最新份额": [6_400_000_000, 1_000_000_000],
            "主力净流入-净额": [12_345, -1],
            "数据日期": [pd.Timestamp("2026-07-24"), pd.Timestamp("2026-07-24")],
        })

    def fund_etf_scale_sse(self, date):
        assert date in {"20260726", "20260727"}
        return pd.DataFrame({
            "序号": [1],
            "基金代码": ["561380"],
            "基金简称": ["电网ETF"],
            "ETF类型": ["跨市"],
            "统计日期": [pd.Timestamp("2026-07-24")],
            "基金份额": [6_410_000_000],
        })


def test_nonferrous_snapshot_records_metadata_and_keeps_pmi_partial(tmp_path):
    snapshot = collect_research_snapshot(
        {"id": "015596", "tracking_code": "399395"},
        tmp_path,
        now=datetime(2026, 7, 26, 10),
        ak_module=NonferrousAK(),
    )

    by_name = {item["metric_name"]: item for item in snapshot["observations"]}
    assert snapshot["profile"] == "nonferrous"
    assert snapshot["research_data_state"] == "DATA_PARTIAL"
    assert snapshot["thesis_state"] == "MANUAL_REVIEW_REQUIRED"
    assert snapshot["trade_automation"] is False
    assert snapshot["source_reliability"]["state"] == "AWAITING_SECOND_SUCCESS"
    assert by_name["沪铜连续合约收盘价"]["as_of_date"]
    assert by_name["LME铜库存13周变化率"]["frequency"] == "周"
    assert by_name["中国制造业PMI"]["status"] == "PARTIAL"
    assert by_name["中国制造业PMI"]["as_of_date"] == "2026-06-01"
    assert any("美元指数" in item for item in snapshot["manual_review_items"])


def test_grid_snapshot_uses_tracking_code_and_reuses_one_day_cache(tmp_path):
    asset = {"id": "023639", "tracking_code": "561380"}
    first = collect_research_snapshot(
        asset, tmp_path, now=datetime(2026, 7, 26, 10), ak_module=GridAK(),
    )
    second = collect_research_snapshot(
        asset,
        tmp_path,
        now=datetime(2026, 7, 26, 12),
        ak_module=object(),
    )
    third = collect_research_snapshot(
        asset,
        tmp_path,
        now=datetime(2026, 7, 27, 10, 1),
        ak_module=GridAK(),
    )

    assert first["profile"] == "grid_equipment"
    assert first["research_data_state"] == "DATA_OK"
    shares = next(item for item in first["observations"] if item["metric_name"] == "ETF最新份额")
    assert shares["value"] == 6_400_000_000
    exchange_shares = next(item for item in first["observations"] if item["metric_name"] == "上交所ETF份额")
    assert exchange_shares["value"] == 6_410_000_000
    assert second["collector_cache"]["etf_market"]["cache"] is True
    assert second["observations"] == first["observations"]
    assert third["collector_cache"]["etf_market"]["cache"] is False
    assert third["source_reliability"]["state"] == "VERIFIED"
    assert third["source_reliability"]["collectors"]["etf_market"]["consecutive_successful_fetches"] == 2


def test_research_collector_error_is_a_warning_not_a_trade_signal(tmp_path):
    class BrokenAK:
        def futures_zh_daily_sina(self, symbol):
            raise ConnectionError("temporary provider outage")

        def macro_euro_lme_stock(self):
            raise ConnectionError("temporary provider outage")

        def macro_china_pmi(self):
            raise ConnectionError("temporary provider outage")

    snapshot = collect_research_snapshot(
        {"id": "015596", "tracking_code": "399395"},
        tmp_path,
        now=datetime(2026, 7, 26, 10),
        ak_module=BrokenAK(),
    )

    assert snapshot["research_data_state"] == "DATA_PARTIAL"
    assert snapshot["trade_automation"] is False
    assert snapshot["source_reliability"]["state"] == "MANUAL_REVIEW_REQUIRED"
    assert {item["status"] for item in snapshot["observations"]} == {"ERROR"}
