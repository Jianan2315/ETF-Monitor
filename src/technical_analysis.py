"""Read-only technical facts for reports and human review."""
from __future__ import annotations

from typing import Any

import pandas as pd


MA_PERIODS = (5, 10, 20, 60, 120)


def moving_average_facts(close: pd.Series) -> dict[str, Any]:
    series = pd.to_numeric(close, errors="coerce").dropna().reset_index(drop=True)
    if series.empty:
        return {"available": False, "periods": {}, "relationships": {}, "alignment": "UNAVAILABLE"}
    current_price = float(series.iloc[-1])
    periods: dict[str, Any] = {}
    values: dict[int, float] = {}
    for period in MA_PERIODS:
        if len(series) < period:
            periods[str(period)] = {"available": False}
            continue
        average = float(series.iloc[-period:].mean())
        values[period] = average
        previous_average = float(series.iloc[-period - 1:-1].mean()) if len(series) > period else None
        prior_price = float(series.iloc[-2]) if len(series) > 1 else None
        relation = "ABOVE" if current_price > average else ("BELOW" if current_price < average else "AT")
        slope = "UP" if previous_average is not None and average > previous_average else ("DOWN" if previous_average is not None and average < previous_average else "FLAT")
        crossed = "NONE"
        if previous_average is not None and prior_price is not None:
            if prior_price >= previous_average and current_price < average:
                crossed = "CROSSED_BELOW"
            elif prior_price <= previous_average and current_price > average:
                crossed = "CROSSED_ABOVE"
        periods[str(period)] = {
            "available": True,
            "value": round(average, 6),
            "price_relation": relation,
            "slope": slope,
            "price_cross": crossed,
        }

    relationships: dict[str, str] = {}
    for short, long in zip(MA_PERIODS, MA_PERIODS[1:]):
        if short in values and long in values:
            relationships[f"{short}_{long}"] = "ABOVE" if values[short] > values[long] else ("BELOW" if values[short] < values[long] else "AT")
    alignment = "MIXED"
    if len(values) == len(MA_PERIODS):
        ordered = [values[period] for period in MA_PERIODS]
        if all(left > right for left, right in zip(ordered, ordered[1:])):
            alignment = "BULLISH"
        elif all(left < right for left, right in zip(ordered, ordered[1:])):
            alignment = "BEARISH"
    return {
        "available": True,
        "periods": periods,
        "relationships": relationships,
        "alignment": alignment,
    }


def volume_facts(volume: pd.Series) -> dict[str, Any]:
    series = pd.to_numeric(volume, errors="coerce").dropna().reset_index(drop=True)
    if series.empty or float(series.iloc[-1]) <= 0:
        return {"available": False}
    latest = float(series.iloc[-1])
    result: dict[str, Any] = {"available": True, "latest_volume": latest, "ratios": {}}
    for period in (5, 20):
        if len(series) <= period:
            continue
        average = float(series.iloc[-period - 1:-1].mean())
        if average > 0:
            result["ratios"][str(period)] = {
                "average": round(average, 4),
                "ratio": round(latest / average, 4),
            }
    return result


def relative_strength_facts(history: pd.DataFrame, benchmark: pd.DataFrame) -> dict[str, Any]:
    """Compare returns on overlapping dates; it never produces a trade state."""
    left = history[["date", "close"]].copy().rename(columns={"close": "asset_close"})
    right = benchmark[["date", "close"]].copy().rename(columns={"close": "benchmark_close"})
    left["date"] = pd.to_datetime(left["date"], errors="coerce")
    right["date"] = pd.to_datetime(right["date"], errors="coerce")
    merged = left.merge(right, on="date", how="inner").dropna().sort_values("date").reset_index(drop=True)
    facts: dict[str, Any] = {"available": len(merged) >= 6, "periods": {}, "overlap_points": len(merged)}
    if not facts["available"]:
        return facts
    for period in MA_PERIODS:
        if len(merged) <= period:
            continue
        asset_return = (float(merged.iloc[-1]["asset_close"]) / float(merged.iloc[-period - 1]["asset_close"]) - 1) * 100
        benchmark_return = (float(merged.iloc[-1]["benchmark_close"]) / float(merged.iloc[-period - 1]["benchmark_close"]) - 1) * 100
        difference = asset_return - benchmark_return
        facts["periods"][str(period)] = {
            "asset_return_pct": round(asset_return, 4),
            "benchmark_return_pct": round(benchmark_return, 4),
            "excess_return_pct": round(difference, 4),
            "relation": "STRONGER" if difference > 0 else ("WEAKER" if difference < 0 else "EQUAL"),
        }
    return facts
