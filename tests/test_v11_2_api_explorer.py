from datetime import datetime
from unittest.mock import Mock

from src.data_fetcher import explore_eastmoney_history_endpoints


def test_api_explorer_records_successful_candidate(monkeypatch):
    response = Mock()
    response.status_code = 200
    response.url = "https://push2his.eastmoney.com/example"
    response.headers = {"Content-Type": "application/json"}
    response.content = b"{}"
    response.text = "{}"
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": {
            "klines": [
                "2026-07-14,1.00,1.02,1.03,0.99,1000,1020,0,2.00,0,0"
            ]
        }
    }

    monkeypatch.setattr(
        "src.data_fetcher.requests.get",
        lambda *args, **kwargs: response,
    )

    result = explore_eastmoney_history_endpoints(
        {"symbol": "561380"},
        {
            "data": {
                "history_days": 100,
                "request_timeout_seconds": 20,
            }
        },
        now=datetime(2026, 7, 16, 10, 0),
    )

    assert result["attempt_count"] == 16
    assert result["success_count"] == 16
    assert result["ok"] is True
    assert all(item["http_status"] == 200 for item in result["attempts"])
    assert all(item["kline_count"] == 1 for item in result["attempts"])
