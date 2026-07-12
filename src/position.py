from __future__ import annotations
import json
from pathlib import Path


def load_transactions(path: Path, symbol: str) -> list[dict]:
    if not path.exists():
        return []
    data=json.loads(path.read_text(encoding='utf-8'))
    return sorted(data.get(symbol,[]), key=lambda x:x.get('date',''))


def calculate_position(transactions: list[dict], latest_price: float) -> dict:
    shares=0.0
    cost_basis=0.0
    realized_profit=0.0
    last_buy_price=0.0
    last_buy_date=''

    for item in transactions:
        kind=str(item.get('type','')).upper()
        price=float(item.get('price',0) or 0)
        amount=float(item.get('amount',0) or 0)
        units=float(item.get('shares', amount/price if price>0 else 0) or 0)
        if kind=='BUY':
            shares += units
            cost_basis += amount
            last_buy_price=price
            last_buy_date=str(item.get('date',''))
        elif kind=='SELL' and shares>0:
            sell_units=min(units,shares)
            average_cost=cost_basis/shares if shares else 0
            proceeds=sell_units*price
            realized_profit += proceeds-sell_units*average_cost
            shares -= sell_units
            cost_basis -= sell_units*average_cost

    average_cost=cost_basis/shares if shares else 0.0
    current_value=shares*latest_price
    profit_amount=current_value-cost_basis
    profit_pct=(profit_amount/cost_basis*100) if cost_basis else 0.0
    return {
      'invested_amount':round(cost_basis,2),
      'average_cost':round(average_cost,6),
      'shares':round(shares,6),
      'current_value':round(current_value,2),
      'profit_amount':round(profit_amount,2),
      'profit_pct':round(profit_pct,2),
      'realized_profit':round(realized_profit,2),
      'last_buy_price':round(last_buy_price,6),
      'last_buy_date':last_buy_date,
      'transactions':transactions,
    }
