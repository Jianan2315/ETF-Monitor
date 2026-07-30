from __future__ import annotations

import math
import pandas as pd

from src.models import IndicatorResult, MarketSnapshot
from src.market_cycles import analyze_market_cycles
from src.technical_analysis import moving_average_facts, volume_facts


def calculate_indicators(asset: dict, history: pd.DataFrame, latest: MarketSnapshot) -> IndicatorResult:
    if history.empty:
        raise ValueError("History data is empty")

    data = history.copy()
    data["date"] = pd.to_datetime(data["date"])
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data = data.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    close = data["close"]
    if len(close) < 21:
        raise ValueError("At least 21 valid closing prices are required")

    strategy = asset["strategy"]
    last_buy_price = float(asset['position'].get('last_buy_price', latest.price) or latest.price)
    up_days = count_consecutive_up_days(close)
    down_days = count_consecutive_down_days(close)
    trend_days = up_days if up_days > 0 else down_days
    consecutive_trend_pct = cumulative_return_for_last_days(close, trend_days) if trend_days else 0.0
    preceding_down_days = count_preceding_down_days(close, up_days)
    rebound_pct = cumulative_return_for_last_days(close, up_days) if up_days else 0.0

    one_m = drawdown_from_window_high(data, 21)
    three_m = drawdown_from_window_high(data, 63)
    six_m = drawdown_from_window_high(data, 126)

    cycle_analysis = analyze_market_cycles(
        data,
        float(latest.price),
        float(strategy.get("cycle_confirmation_pct", 8.0)),
    )
    if latest.main_fund_flow is None:
        fund_flow_status = "UNAVAILABLE"
        fund_flow_impact = "中性：资金流数据不可用，不阻断价格信号"
    elif latest.main_fund_flow < 0:
        fund_flow_status = "WEAK"
        fund_flow_impact = "轻度降置信：当日资金流偏弱"
    else:
        fund_flow_status = "IMPROVING"
        fund_flow_impact = "轻度增置信：当日资金流偏强"

    return IndicatorResult(
        daily_change_pct=float(latest.daily_change_pct),
        five_day_change_pct=period_return(close, 5),
        ten_day_change_pct=period_return(close, 10),
        twenty_day_change_pct=period_return(close, 20),
        one_month_drawdown_pct=one_m[0],
        three_month_drawdown_pct=three_m[0],
        six_month_drawdown_pct=six_m[0],
        one_month_high_date=one_m[1],
        three_month_high_date=three_m[1],
        six_month_high_date=six_m[1],
        drawdown_from_last_buy_pct=(latest.price / last_buy_price - 1) * 100,
        consecutive_down_days=down_days,
        consecutive_up_days=up_days,
        consecutive_trend_pct=consecutive_trend_pct,
        preceding_down_days=preceding_down_days,
        rebound_pct=rebound_pct,
        is_new_5d_low=is_new_low(close, 5),
        is_new_10d_low=is_new_low(close, max(int(strategy.get("new_low_window_days", 10)), 1)),
        stable_days=count_stable_days(close, float(strategy.get("stable_range_pct", 3.0))),
        latest_price=float(latest.price),
        cycle_analysis=cycle_analysis,
        fund_flow_status=fund_flow_status,
        fund_flow_impact=fund_flow_impact,
        moving_averages=moving_average_facts(close),
        volume_analysis=(
            volume_facts(
                data["volume"],
                close,
                downtrend_active=cycle_analysis.get("active_phase") == "DOWN",
            )
            if "volume" in data else {"available": False}
        ),
    )


def period_return(close: pd.Series, days: int) -> float:
    if len(close) <= days:
        return math.nan
    return float((close.iloc[-1] / close.iloc[-days - 1] - 1) * 100)


def drawdown_from_window_high(data: pd.DataFrame, window: int) -> tuple[float, str]:
    recent = data.tail(min(window, len(data)))
    idx = recent["close"].idxmax()
    high = float(recent.loc[idx, "close"])
    date = pd.Timestamp(recent.loc[idx, "date"]).strftime("%Y-%m-%d")
    latest = float(recent.iloc[-1]["close"])
    return float((latest / high - 1) * 100), date


def count_consecutive_down_days(close: pd.Series) -> int:
    changes = close.diff()
    count = 0
    for value in reversed(changes.iloc[1:].tolist()):
        if value < 0:
            count += 1
        else:
            break
    return count


def count_consecutive_up_days(close: pd.Series) -> int:
    changes = close.diff()
    count = 0
    for value in reversed(changes.iloc[1:].tolist()):
        if value > 0:
            count += 1
        else:
            break
    return count


def count_preceding_down_days(close: pd.Series, current_up_days: int) -> int:
    changes = close.diff().iloc[1:].tolist()
    end = len(changes) - current_up_days
    count = 0
    for value in reversed(changes[:end]):
        if value < 0:
            count += 1
        else:
            break
    return count


def cumulative_return_for_last_days(close: pd.Series, days: int) -> float:
    if days <= 0 or len(close) <= days:
        return 0.0
    return float((close.iloc[-1] / close.iloc[-days - 1] - 1) * 100)


def is_new_low(close: pd.Series, window: int) -> bool:
    if len(close) < window:
        return False
    recent = close.tail(window)
    return bool(recent.iloc[-1] <= recent.min())


def count_stable_days(close: pd.Series, stable_range_pct: float, max_days: int = 20) -> int:
    max_days = min(max_days, len(close))
    stable_count = 1
    for days in range(2, max_days + 1):
        recent = close.tail(days)
        minimum = float(recent.min())
        maximum = float(recent.max())
        if minimum <= 0:
            break
        if (maximum / minimum - 1) * 100 <= stable_range_pct:
            stable_count = days
        else:
            break
    return stable_count
