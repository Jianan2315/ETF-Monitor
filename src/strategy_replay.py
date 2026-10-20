"""Replay the existing signal strategy under explicit, user-chosen assumptions.

The production strategy deliberately produces review signals rather than placing
orders.  This module is therefore a counterfactual calculator, not an execution
engine: it translates those signals into hypothetical next-day-close trades only
when the caller supplies the execution amounts and reduction percentages.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from src.indicators import calculate_indicators
from src.models import MarketSnapshot
from src.position import calculate_position
from src.strategy import evaluate_strategy


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confirmed(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item for item in records
        if str(item.get("status", "SETTLED")).upper() not in {"PENDING", "PENDING_CLOSE"}
    ]


def default_replay_settings(asset: dict[str, Any], transactions: list[dict[str, Any]], history: pd.DataFrame) -> dict[str, Any]:
    """Provide asset-specific assumptions without changing live strategy settings."""
    confirmed = _confirmed(transactions)
    buys = [item for item in confirmed if str(item.get("type", "")).upper() == "BUY" and _number(item.get("amount")) > 0]
    strategy = asset.get("strategy", {}) if isinstance(asset.get("strategy"), dict) else {}
    gross_buys = round(sum(_number(item.get("amount")) for item in buys), 2)
    first_buy = buys[0] if buys else {}
    first_buy_amount = _number(first_buy.get("amount"))
    start_date = str(first_buy.get("date") or "")[:10]
    if not start_date and not history.empty:
        start_date = pd.Timestamp(history.iloc[0]["date"]).strftime("%Y-%m-%d")
    planned = _number(strategy.get("planned_investment_reference"))
    initial_capital = gross_buys or planned
    review_buy = _number(strategy.get("normal_add_amount")) or first_buy_amount
    deep_buy = _number(strategy.get("enhanced_add_amount")) or review_buy
    return {
        "start_date": start_date,
        "initial_capital": round(initial_capital, 2),
        "initial_buy_amount": round(min(first_buy_amount or initial_capital, initial_capital), 2),
        "review_buy_amount": round(review_buy, 2),
        "deep_drawdown_buy_amount": round(deep_buy, 2),
        "first_sell_pct": round(_number(strategy.get("sell_first_stop_reduction_pct"), 50.0), 2),
        "second_sell_pct": 100.0,
        "execute_downtrend_sell": False,
    }


def normalize_replay_settings(settings: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = {**defaults, **(settings or {})}
    normalized = {
        "start_date": str(merged.get("start_date") or "")[:10],
        "initial_capital": round(max(_number(merged.get("initial_capital")), 0.0), 2),
        "initial_buy_amount": round(max(_number(merged.get("initial_buy_amount")), 0.0), 2),
        "review_buy_amount": round(max(_number(merged.get("review_buy_amount")), 0.0), 2),
        "deep_drawdown_buy_amount": round(max(_number(merged.get("deep_drawdown_buy_amount")), 0.0), 2),
        "first_sell_pct": round(min(max(_number(merged.get("first_sell_pct"), 25.0), 0.0), 100.0), 2),
        "second_sell_pct": round(min(max(_number(merged.get("second_sell_pct"), 100.0), 0.0), 100.0), 2),
        "execute_downtrend_sell": bool(merged.get("execute_downtrend_sell", False)),
    }
    if normalized["initial_buy_amount"] > normalized["initial_capital"]:
        normalized["initial_buy_amount"] = normalized["initial_capital"]
    return normalized


def _history_frame(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame.get("date"), errors="coerce")
    frame["close"] = pd.to_numeric(frame.get("close"), errors="coerce")
    if "volume" in frame:
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    return frame.dropna(subset=["date", "close"]).query("close > 0").sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def actual_account_performance(transactions: list[dict[str, Any]], latest_price: float, start_date: str = "") -> dict[str, Any]:
    """Return an all-in account view of actual records, including sale proceeds."""
    records = [item for item in _confirmed(transactions) if not start_date or str(item.get("date", ""))[:10] >= start_date]
    position = calculate_position(records, latest_price)
    buys = sum(_number(item.get("amount")) for item in records if str(item.get("type", "")).upper() == "BUY")
    proceeds = sum(_number(item.get("amount")) for item in records if str(item.get("type", "")).upper() == "SELL")
    net_asset_value = round(position["current_value"] + proceeds, 2)
    profit_amount = round(net_asset_value - buys, 2)
    return {
        "gross_buy_amount": round(buys, 2),
        "sale_proceeds": round(proceeds, 2),
        "current_holding_value": position["current_value"],
        "net_asset_value": net_asset_value,
        "profit_amount": profit_amount,
        "return_pct": round(profit_amount / buys * 100, 2) if buys else 0.0,
        "transactions_used": len(records),
        "pending_transactions_ignored": len(transactions) - len(_confirmed(transactions)),
    }


def _signal_key(decision: Any, signal_date: str) -> str:
    metadata = decision.metadata if isinstance(decision.metadata, dict) else {}
    cycle = metadata.get("cycle") if isinstance(metadata.get("cycle"), dict) else {}
    cycle_id = str(cycle.get("current_cycle_id") or cycle.get("active_phase") or signal_date)
    return f"{decision.reason_code}:{cycle_id}"


def replay_strategy(asset: dict[str, Any], history: pd.DataFrame, transactions: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    """Replay current signal logic with next-trading-day-close hypothetical orders."""
    frame = _history_frame(history)
    defaults = default_replay_settings(asset, transactions, frame)
    parameters = normalize_replay_settings(settings, defaults)
    if frame.empty or len(frame) < 21:
        raise ValueError("策略模拟至少需要 21 个有效交易日的日线数据。")
    if parameters["initial_capital"] <= 0:
        raise ValueError("请先填写模拟初始资金；默认值会使用该资产已记录的累计买入金额。")
    if not parameters["start_date"]:
        raise ValueError("请先填写模拟起始日期。")

    start_candidates = frame.index[frame["date"] >= pd.Timestamp(parameters["start_date"])]
    if len(start_candidates) == 0:
        raise ValueError("模拟起始日期晚于当前本地日线数据。")
    start_index = int(start_candidates[0])
    cash = parameters["initial_capital"]
    shares = 0.0
    last_buy_price = 0.0
    events: list[dict[str, Any]] = []
    seen_signals: set[str] = set()
    pending_order: dict[str, Any] | None = {
        "action": "BUY", "amount": parameters["initial_buy_amount"], "reason_code": "INITIAL_POSITION",
        "signal_date": frame.iloc[start_index]["date"].strftime("%Y-%m-%d"),
    } if parameters["initial_buy_amount"] > 0 else None
    skipped_signals = 0

    for index in range(start_index, len(frame)):
        row = frame.iloc[index]
        date_text = row["date"].strftime("%Y-%m-%d")
        close = float(row["close"])

        if pending_order:
            action = pending_order["action"]
            if action == "BUY":
                amount = min(float(pending_order["amount"]), cash)
                if amount > 0:
                    bought = amount / close
                    cash -= amount
                    shares += bought
                    last_buy_price = close
                    events.append({**pending_order, "execution_date": date_text, "price": round(close, 6), "amount": round(amount, 2), "shares": round(bought, 6)})
                else:
                    skipped_signals += 1
            else:
                portion = float(pending_order["sell_pct"]) / 100
                sold = shares * portion
                if sold > 0:
                    amount = sold * close
                    shares -= sold
                    cash += amount
                    events.append({**pending_order, "execution_date": date_text, "price": round(close, 6), "amount": round(amount, 2), "shares": round(sold, 6)})
                else:
                    skipped_signals += 1
            pending_order = None

        if index < 20 or index >= len(frame) - 1:
            continue
        prior_close = float(frame.iloc[index - 1]["close"])
        snapshot = MarketSnapshot(
            symbol=str(asset.get("tracking_code") or asset.get("symbol") or ""),
            name=str(asset.get("tracking_name") or asset.get("name") or ""),
            timestamp=pd.Timestamp(row["date"]).to_pydatetime(),
            price=close,
            daily_change_pct=(close / prior_close - 1) * 100 if prior_close else 0.0,
            volume=_number(row.get("volume")) if "volume" in frame else None,
        )
        replay_asset = {
            **asset,
            "position": {"shares": shares, "last_buy_price": last_buy_price or close},
        }
        # The live strategy's volume facts are auxiliary report context, not a
        # gate in ``evaluate_strategy``.  Excluding volume keeps a long replay
        # responsive while preserving every signal rule used by the evaluator.
        indicators = calculate_indicators(
            replay_asset,
            frame.loc[:index, ["date", "close"]],
            snapshot,
        )
        decision = evaluate_strategy(replay_asset, indicators)
        key = _signal_key(decision, date_text)
        if key in seen_signals:
            continue

        next_order: dict[str, Any] | None = None
        if decision.status == "REVIEW_BUY":
            amount = parameters["deep_drawdown_buy_amount"] if decision.reason_code == "DEEP_DRAWDOWN_REVERSAL" else parameters["review_buy_amount"]
            next_order = {"action": "BUY", "amount": amount, "reason_code": decision.reason_code, "signal_date": date_text}
        elif decision.status == "PARTIAL_SELL":
            sell_pct = parameters["first_sell_pct"] if decision.reason_code == "SELL_REVIEW" else parameters["second_sell_pct"]
            next_order = {"action": "SELL", "sell_pct": sell_pct, "reason_code": decision.reason_code, "signal_date": date_text}
        elif decision.status == "REVIEW_SELL" and parameters["execute_downtrend_sell"]:
            next_order = {"action": "SELL", "sell_pct": 100.0, "reason_code": decision.reason_code, "signal_date": date_text}

        if next_order:
            seen_signals.add(key)
            pending_order = next_order

    latest_price = float(frame.iloc[-1]["close"])
    net_asset_value = round(cash + shares * latest_price, 2)
    profit_amount = round(net_asset_value - parameters["initial_capital"], 2)
    actual = actual_account_performance(transactions, latest_price, parameters["start_date"])
    return {
        "settings": parameters,
        "period": {
            "start_date": frame.iloc[start_index]["date"].strftime("%Y-%m-%d"),
            "end_date": frame.iloc[-1]["date"].strftime("%Y-%m-%d"),
            "latest_price": round(latest_price, 6),
            "trading_days": len(frame) - start_index,
        },
        "strategy": {
            "initial_capital": parameters["initial_capital"],
            "cash": round(cash, 2),
            "shares": round(shares, 6),
            "current_holding_value": round(shares * latest_price, 2),
            "net_asset_value": net_asset_value,
            "profit_amount": profit_amount,
            "return_pct": round(profit_amount / parameters["initial_capital"] * 100, 2),
            "executed_order_count": len(events),
            "skipped_signal_count": skipped_signals,
        },
        "actual": actual,
        "difference": {
            "profit_amount": round(profit_amount - actual["profit_amount"], 2),
            "return_pct": round((profit_amount / parameters["initial_capital"] * 100) - actual["return_pct"], 2),
            "directly_comparable": abs(parameters["initial_capital"] - actual["gross_buy_amount"]) < 0.01,
        },
        "events": events[-100:],
        "notes": [
            "策略本身只输出人工复核信号；本页按所选假设模拟，不会创建任何真实交易记录。",
            "信号以当日收盘价计算，并在下一交易日收盘价模拟执行，避免使用当天收盘后的未来信息。",
            "下行风险 REVIEW_SELL 默认不自动卖出；仅在启用该选项时才按全额卖出模拟。",
        ],
    }
