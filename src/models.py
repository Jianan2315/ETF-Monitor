from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass
class MarketSnapshot:
    symbol: str
    name: str
    timestamp: datetime
    price: float
    daily_change_pct: float
    volume: float | None = None
    amount: float | None = None
    main_fund_flow: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass
class MarketBundle:
    history: pd.DataFrame
    latest: MarketSnapshot
    data_source: str
    generated_at: datetime
    intraday: pd.DataFrame | None = None
    data_phase: str = "CLOSED"
    market_timestamp: datetime | None = None


@dataclass
class IndicatorResult:
    daily_change_pct: float
    five_day_change_pct: float
    ten_day_change_pct: float
    twenty_day_change_pct: float
    one_month_drawdown_pct: float
    three_month_drawdown_pct: float
    six_month_drawdown_pct: float
    one_month_high_date: str
    three_month_high_date: str
    six_month_high_date: str
    drawdown_from_last_buy_pct: float
    consecutive_down_days: int
    consecutive_up_days: int
    consecutive_trend_pct: float
    preceding_down_days: int
    rebound_pct: float
    is_new_5d_low: bool
    is_new_10d_low: bool
    stable_days: int
    latest_price: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyDecision:
    status: str
    should_notify: bool
    suggested_amount: float
    reasons: list[str]
    action_type: str = "NONE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
