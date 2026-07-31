"""Read-only technical facts for reports and human review."""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd


MA_PERIODS = (5, 10, 20, 60, 120)
VOLUME_WINDOWS = (5, 20, 120)
OUTCOME_HORIZONS = (1, 3, 5)


def _percentile_rank(value: float, reference: pd.Series) -> float | None:
    """Return the inclusive empirical percentile without looking ahead."""
    values = pd.to_numeric(reference, errors="coerce").dropna()
    if values.empty:
        return None
    return round(float((values <= value).mean() * 100), 2)


def _volume_ratios(series: pd.Series, index: int) -> dict[str, dict[str, float]]:
    ratios: dict[str, dict[str, float]] = {}
    for period in VOLUME_WINDOWS:
        if index < period:
            continue
        average = float(series.iloc[index - period:index].mean())
        if average > 0:
            ratios[str(period)] = {
                "average": round(average, 4),
                "ratio": round(float(series.iloc[index]) / average, 4),
            }
    return ratios


def _volume_state(
    ratios: dict[str, dict[str, float]],
    *,
    high_ratio: float,
    low_ratio: float,
    required_window_count: int,
) -> tuple[str, int, int]:
    high_score = sum(item["ratio"] >= high_ratio for item in ratios.values())
    low_score = sum(item["ratio"] <= low_ratio for item in ratios.values())
    # The agreed 2/3 rule is meaningful only when all 5/20/120-day
    # comparisons exist.  A short history must remain explicitly unclassified.
    if len(ratios) < len(VOLUME_WINDOWS):
        return "UNAVAILABLE", high_score, low_score
    if high_score >= required_window_count:
        return "HIGH", high_score, low_score
    if low_score >= required_window_count:
        return "LOW", high_score, low_score
    return "NORMAL", high_score, low_score


def _move_facts(
    close: pd.Series,
    index: int,
    *,
    lookback: int,
    notice_percentile: float,
    very_large_percentile: float,
) -> dict[str, Any]:
    if index < 1:
        return {"available": False}
    previous = float(close.iloc[index - 1])
    current = float(close.iloc[index])
    if previous <= 0:
        return {"available": False}
    change = (current / previous - 1) * 100
    if change == 0:
        return {
            "available": True,
            "direction": "FLAT",
            "daily_return_pct": round(change, 4),
            "percentile": None,
            "level": "NORMAL",
        }
    returns = close.pct_change().iloc[max(1, index - lookback):index] * 100
    if change > 0:
        comparable = returns[returns > 0]
        direction = "UP"
        magnitude = change
    else:
        comparable = -returns[returns < 0]
        direction = "DOWN"
        magnitude = -change
    percentile = _percentile_rank(magnitude, comparable)
    level = "NORMAL"
    if percentile is not None and percentile >= very_large_percentile:
        level = "VERY_LARGE"
    elif percentile is not None and percentile >= notice_percentile:
        level = "LARGE"
    return {
        "available": True,
        "direction": direction,
        "daily_return_pct": round(change, 4),
        "percentile": percentile,
        "comparison_sample_size": int(len(comparable)),
        "level": level,
    }


def _event_key(
    volume_state: str,
    move: dict[str, Any],
    *,
    downtrend_active: bool = False,
    stabilizing: bool = False,
) -> str | None:
    if volume_state == "LOW" and downtrend_active and stabilizing:
        return "LOW_VOLUME_STABILIZING"
    if move.get("level") not in {"LARGE", "VERY_LARGE"}:
        return None
    direction = move.get("direction")
    if volume_state == "HIGH" and direction == "DOWN":
        return "HIGH_VOLUME_DECLINE"
    if volume_state == "LOW" and direction == "DOWN":
        return "LOW_VOLUME_DECLINE"
    if volume_state == "HIGH" and direction == "UP":
        return "HIGH_VOLUME_REBOUND" if downtrend_active else "HIGH_VOLUME_RISE"
    return None


def _outcome_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"sample_size": 0, "positive_rate_pct": None, "mean_return_pct": None, "median_return_pct": None}
    series = pd.Series(values, dtype="float64")
    return {
        "sample_size": int(len(series)),
        "positive_rate_pct": round(float((series > 0).mean() * 100), 2),
        "mean_return_pct": round(float(series.mean()), 4),
        "median_return_pct": round(float(series.median()), 4),
    }


def _historical_event_studies(
    volume: pd.Series,
    close: pd.Series,
    *,
    high_ratio: float,
    low_ratio: float,
    required_window_count: int,
    move_lookback: int,
    move_notice_percentile: float,
    move_very_large_percentile: float,
    context_at_index: Callable[[int], dict[str, bool]] | None = None,
) -> dict[str, Any]:
    """Describe outcomes after historical events; this is not a forecast."""
    events: dict[str, dict[int, list[float]]] = {}
    last_event_index = len(close) - max(OUTCOME_HORIZONS)
    for index in range(120, max(120, last_event_index)):
        ratios = _volume_ratios(volume, index)
        if len(ratios) < len(VOLUME_WINDOWS):
            continue
        move = _move_facts(
            close,
            index,
            lookback=move_lookback,
            notice_percentile=move_notice_percentile,
            very_large_percentile=move_very_large_percentile,
        )
        context = context_at_index(index) if context_at_index is not None else {}
        key = _event_key(
            _volume_state(
                ratios,
                high_ratio=high_ratio,
                low_ratio=low_ratio,
                required_window_count=required_window_count,
            )[0],
            move,
            downtrend_active=bool(context.get("downtrend_active")),
            stabilizing=bool(context.get("stabilizing")),
        )
        if key is None:
            continue
        bucket = events.setdefault(key, {horizon: [] for horizon in OUTCOME_HORIZONS})
        for horizon in OUTCOME_HORIZONS:
            outcome = (float(close.iloc[index + horizon]) / float(close.iloc[index]) - 1) * 100
            bucket[horizon].append(outcome)
    return {
        key: {
            "sample_size": len(next(iter(outcomes.values()), [])),
            "sufficient_sample": len(next(iter(outcomes.values()), [])) >= 15,
            "outcomes": {str(horizon): _outcome_summary(values) for horizon, values in outcomes.items()},
        }
        for key, outcomes in events.items()
    }


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


def volume_facts(
    volume: pd.Series,
    close: pd.Series | None = None,
    *,
    downtrend_active: bool = False,
    stabilizing: bool = False,
    high_ratio: float = 1.2,
    low_ratio: float = 0.8,
    required_window_count: int = 2,
    move_notice_percentile: float = 60,
    move_very_large_percentile: float = 80,
    move_history_lookback_days: int = 120,
    historical_context_at_index: Callable[[int], dict[str, bool]] | None = None,
) -> dict[str, Any]:
    """Return asset-relative volume facts and descriptive historical outcomes.

    A volume bar is not investor identity or a trade instruction.  The current
    label is deliberately read-only; the outcome section only summarizes past
    same-asset observations and never predicts a future return.
    """
    high_ratio = max(float(high_ratio), 0.0)
    low_ratio = max(float(low_ratio), 0.0)
    required_window_count = min(max(int(required_window_count), 1), len(VOLUME_WINDOWS))
    move_notice_percentile = min(max(float(move_notice_percentile), 0.0), 100.0)
    move_very_large_percentile = min(max(float(move_very_large_percentile), move_notice_percentile), 100.0)
    move_history_lookback_days = max(int(move_history_lookback_days), 20)
    raw_volume = pd.to_numeric(volume, errors="coerce").reset_index(drop=True)
    if close is None:
        series = raw_volume.dropna().reset_index(drop=True)
        close_series = None
    else:
        raw_close = pd.to_numeric(close, errors="coerce").reset_index(drop=True)
        frame = pd.DataFrame({"volume": raw_volume, "close": raw_close}).dropna()
        series = frame["volume"].reset_index(drop=True)
        close_series = frame["close"].reset_index(drop=True)
    if series.empty or float(series.iloc[-1]) <= 0:
        return {"available": False}
    latest = float(series.iloc[-1])
    ratios = _volume_ratios(series, len(series) - 1)
    volume_state, high_score, low_score = _volume_state(
        ratios,
        high_ratio=high_ratio,
        low_ratio=low_ratio,
        required_window_count=required_window_count,
    )
    result: dict[str, Any] = {
        "available": True,
        "latest_volume": latest,
        "ratios": ratios,
        "thresholds": {
            "high_ratio": high_ratio,
            "low_ratio": low_ratio,
            "required_window_count": required_window_count,
            "move_notice_percentile": move_notice_percentile,
            "move_very_large_percentile": move_very_large_percentile,
            "history_lookback_days": move_history_lookback_days,
        },
        "classification": {
            "volume_state": volume_state,
            "high_window_count": high_score,
            "low_window_count": low_score,
        },
    }
    if close_series is None or len(close_series) != len(series):
        return result

    move = _move_facts(
        close_series,
        len(close_series) - 1,
        lookback=move_history_lookback_days,
        notice_percentile=move_notice_percentile,
        very_large_percentile=move_very_large_percentile,
    )
    current_event = _event_key(
        volume_state,
        move,
        downtrend_active=downtrend_active,
        stabilizing=stabilizing,
    )
    result["classification"].update({
        "move": move,
        "historical_event_key": current_event,
        "event": current_event or "NONE",
    })
    result["event_studies"] = _historical_event_studies(
        series,
        close_series,
        high_ratio=high_ratio,
        low_ratio=low_ratio,
        required_window_count=required_window_count,
        move_lookback=move_history_lookback_days,
        move_notice_percentile=move_notice_percentile,
        move_very_large_percentile=move_very_large_percentile,
        context_at_index=historical_context_at_index,
    )
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
