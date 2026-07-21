"""Causal, confirmation-based market-cycle analysis.

The scanner only confirms a turning point after the configured move away from
it has actually occurred.  It therefore never labels a historical high or low
using prices that were unavailable on its confirmation date.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def percentile_rank(samples: list[float], value: float | None) -> float | None:
    if value is None or not samples:
        return None
    return round(sum(item <= value for item in samples) / len(samples) * 100, 2)


def percentile_value(samples: list[float], percentile: float) -> float | None:
    if not samples:
        return None
    return float(pd.Series(samples, dtype="float64").quantile(percentile / 100))


def analyze_market_cycles(
    history: pd.DataFrame,
    current_price: float,
    confirmation_pct: float = 8.0,
) -> dict[str, Any]:
    """Return confirmed turns, historical distributions and live-cycle facts."""
    frame = history[["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna().sort_values("date").reset_index(drop=True)
    empty = {
        "confirmation_pct": confirmation_pct,
        "events": [], "down_cycles": [], "up_cycles": [],
        "drawdowns": [], "rises": [], "sample_size": 0,
        "drawdown_sample_size": 0, "rise_sample_size": 0,
        "current_cycle_id": None, "current_drawdown_pct": None,
        "current_drawdown_percentile": None, "current_rise_pct": None,
        "current_rise_percentile": None, "recovery_pct": None,
        "last_confirmed_high": None, "last_confirmed_low": None,
        "sell_first_threshold_pct": None, "sell_second_threshold_pct": None,
    }
    if frame.empty or current_price <= 0:
        return empty

    confirm = max(float(confirmation_pct), 0.01) / 100
    first = frame.iloc[0]
    phase = "UP"
    high = low = {"date": first["date"], "price": float(first["close"])}
    events: list[dict[str, Any]] = []

    for _, row in frame.iloc[1:].iterrows():
        point = {"date": row["date"], "price": float(row["close"])}
        if phase == "UP":
            if point["price"] >= high["price"]:
                high = point
            elif point["price"] <= high["price"] * (1 - confirm):
                events.append({
                    "kind": "HIGH", "date": high["date"].strftime("%Y-%m-%d"),
                    "price": high["price"], "confirmed_date": point["date"].strftime("%Y-%m-%d"),
                })
                phase, low = "DOWN", point
        else:
            if point["price"] <= low["price"]:
                low = point
            elif point["price"] >= low["price"] * (1 + confirm):
                events.append({
                    "kind": "LOW", "date": low["date"].strftime("%Y-%m-%d"),
                    "price": low["price"], "confirmed_date": point["date"].strftime("%Y-%m-%d"),
                })
                phase, high = "UP", point

    down_cycles: list[dict[str, Any]] = []
    up_cycles: list[dict[str, Any]] = []
    for left, right in zip(events, events[1:]):
        if left["kind"] == "HIGH" and right["kind"] == "LOW":
            down_cycles.append({
                "high": left, "low": right,
                "move_pct": round((right["price"] / left["price"] - 1) * 100, 4),
                "cycle_id": f"DOWN:{left['date']}:{right['date']}",
            })
        elif left["kind"] == "LOW" and right["kind"] == "HIGH":
            up_cycles.append({
                "low": left, "high": right,
                "move_pct": round((right["price"] / left["price"] - 1) * 100, 4),
                "cycle_id": f"UP:{left['date']}:{right['date']}",
            })

    drawdowns = [abs(item["move_pct"]) for item in down_cycles]
    rises = [item["move_pct"] for item in up_cycles]
    last_high = next((item for item in reversed(events) if item["kind"] == "HIGH"), None)
    last_low = next((item for item in reversed(events) if item["kind"] == "LOW"), None)
    active_high = high if phase == "UP" else high
    active_low = low if phase == "DOWN" else low
    current_drawdown = None
    if active_high and current_price <= active_high["price"]:
        current_drawdown = round(abs((current_price / active_high["price"] - 1) * 100), 4)
    current_rise = None
    if last_low and current_price >= last_low["price"]:
        current_rise = round((current_price / last_low["price"] - 1) * 100, 4)
    recovery = None
    if down_cycles and last_low:
        latest_down = down_cycles[-1]
        if latest_down["low"]["date"] == last_low["date"]:
            high_price, low_price = latest_down["high"]["price"], latest_down["low"]["price"]
            if high_price > low_price:
                recovery = round((current_price - low_price) / (high_price - low_price) * 100, 4)

    current_cycle_id = (
        f"UP:{last_low['date']}" if last_low and current_rise is not None
        else (f"DOWN:{last_high['date']}" if last_high else None)
    )
    return {
        **empty,
        "events": events, "down_cycles": down_cycles, "up_cycles": up_cycles,
        "drawdowns": drawdowns, "rises": rises,
        # A completed downward cycle is already a valid drawdown observation,
        # even if the following upward cycle has not yet completed.  Keep the
        # two sample counts explicit so callers never discard usable evidence.
        "sample_size": max(len(drawdowns), len(rises)),
        "drawdown_sample_size": len(drawdowns),
        "rise_sample_size": len(rises),
        "current_cycle_id": current_cycle_id,
        "current_drawdown_pct": current_drawdown,
        "current_drawdown_percentile": percentile_rank(drawdowns, current_drawdown),
        "current_rise_pct": current_rise,
        "current_rise_percentile": percentile_rank(rises, current_rise),
        "recovery_pct": recovery,
        "last_confirmed_high": last_high,
        "last_confirmed_low": last_low,
        "sell_first_threshold_pct": percentile_value(rises, 50),
        "sell_second_threshold_pct": percentile_value(rises, 90),
        "active_phase": phase,
        "active_high": {"date": active_high["date"].strftime("%Y-%m-%d"), "price": active_high["price"]},
        "active_low": {"date": active_low["date"].strftime("%Y-%m-%d"), "price": active_low["price"]},
    }
