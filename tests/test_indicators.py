import pandas as pd

from src.indicators import (
    count_consecutive_down_days,
    is_new_low,
    period_return,
)


def test_down():
    assert count_consecutive_down_days(pd.Series([10, 11, 12, 11, 10, 9])) == 3


def test_low():
    assert is_new_low(pd.Series([10, 11, 9, 8]), 4) is True


def test_return():
    assert round(period_return(pd.Series([100, 101, 102, 103, 110, 120]), 5), 2) == 20.0
