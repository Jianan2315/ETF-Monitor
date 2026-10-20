from __future__ import annotations

from pathlib import Path

from src.transactions import load_transactions


def calculate_position(transactions: list[dict], latest_price: float) -> dict:
    shares = 0.0
    cost_basis = 0.0
    realized_profit = 0.0
    last_buy_price = 0.0
    last_buy_date = ""

    for item in transactions:
        status = str(item.get("status", "SETTLED")).upper()
        if status in {"PENDING", "PENDING_CLOSE"}:
            continue

        kind = str(item.get("type", "")).upper()
        price = float(item.get("price", 0) or 0)
        amount = float(item.get("amount", 0) or 0)
        units = float(item.get("shares", 0) or 0)
        if units <= 0 and amount > 0 and price > 0:
            units = amount / price
        if price <= 0 or units <= 0:
            continue

        if kind == "BUY":
            shares += units
            cost_basis += amount
            last_buy_price = price
            last_buy_date = str(item.get("date", ""))
        elif kind == "SELL" and shares > 0:
            sell_units = min(units, shares)
            average_cost = cost_basis / shares if shares else 0
            proceeds = amount if amount > 0 else sell_units * price
            realized_profit += proceeds - sell_units * average_cost
            shares -= sell_units
            cost_basis -= sell_units * average_cost

    average_cost = cost_basis / shares if shares else 0.0
    current_value = shares * latest_price
    profit_amount = current_value - cost_basis
    profit_pct = (
        profit_amount / cost_basis * 100
        if cost_basis
        else 0.0
    )
    cumulative_profit_amount = realized_profit + profit_amount
    return {
        "invested_amount": round(cost_basis, 2),
        "average_cost": round(average_cost, 6),
        "shares": round(shares, 6),
        "current_value": round(current_value, 2),
        "profit_amount": round(profit_amount, 2),
        "profit_pct": round(profit_pct, 2),
        "realized_profit": round(realized_profit, 2),
        "cumulative_profit_amount": round(cumulative_profit_amount, 2),
        "last_buy_price": round(last_buy_price, 6),
        "last_buy_date": last_buy_date,
        "transactions": transactions,
    }
