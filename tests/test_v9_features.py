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


def test_intraday_mode_before_open_falls_back_to_pre_market():
    now = datetime(2026, 7, 15, 8, 30)
    assert _market_phase(now, "intraday") == "PRE_MARKET"


def test_intraday_mode_after_close_falls_back_to_closed():
    now = datetime(2026, 7, 15, 16, 0)
    assert _market_phase(now, "intraday") == "CLOSED"


def test_manual_mode_during_market_is_intraday():
    now = datetime(2026, 7, 15, 14, 45)
    assert _market_phase(now, "manual") == "INTRADAY"
