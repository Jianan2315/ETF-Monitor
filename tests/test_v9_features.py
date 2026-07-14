from datetime import datetime
from pathlib import Path

import pandas as pd

from src.data_fetcher import _market_phase
from src.html_report import generate_html_report


def test_market_phase_explicit_modes():
    now = datetime(2026, 7, 15, 10, 0)
    assert _market_phase(now, "intraday") == "INTRADAY"
    assert _market_phase(now, "close") == "CLOSED"


def test_market_phase_weekend():
    now = datetime(2026, 7, 18, 10, 0)
    assert _market_phase(now, "auto") == "NON_TRADING_DAY"
