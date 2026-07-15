from datetime import datetime
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from src.data_fetcher import (
    _fetch_history_direct_eastmoney,
    classify_data_exception,
)


def test_classify_remote_disconnect():
    exc = requests.exceptions.ConnectionError(
        "RemoteDisconnected('Remote end closed connection without response')"
    )
    assert classify_data_exception(exc) == "REMOTE_DISCONNECTED"


def test_classify_timeout():
    assert classify_data_exception(
        requests.exceptions.Timeout("timed out")
    ) == "TIMEOUT"


def test_direct_eastmoney_history_parsing(monkeypatch):
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
                "2026-07-14,1.00,1.02,1.03,0.99,1000,1020,0,2.00,0,0",
                "2026-07-15,1.02,1.04,1.05,1.01,1200,1248,0,1.96,0,0",
            ]
        }
    }

    monkeypatch.setattr(
        "src.data_fetcher.requests.get",
        lambda *args, **kwargs: response,
    )

    frame = _fetch_history_direct_eastmoney(
        {"symbol": "561380"},
        {
            "data": {
                "history_days": 100,
                "adjust": "qfq",
                "request_timeout_seconds": 20,
            }
        },
        datetime(2026, 7, 16, 10, 0),
    )

    assert isinstance(frame, pd.DataFrame)
    assert list(frame["close"]) == [1.02, 1.04]
    assert frame.iloc[-1]["date"].strftime("%Y-%m-%d") == "2026-07-15"
