import pandas as pd

from src.technical_analysis import moving_average_facts, relative_strength_facts, volume_facts


def test_moving_average_facts_report_price_relation_slope_and_alignment():
    close = pd.Series(range(1, 131), dtype="float64")
    facts = moving_average_facts(close)

    assert facts["periods"]["5"]["price_relation"] == "ABOVE"
    assert facts["periods"]["20"]["slope"] == "UP"
    assert facts["relationships"]["5_10"] == "ABOVE"
    assert facts["alignment"] == "BULLISH"


def test_volume_facts_compare_latest_volume_to_prior_windows():
    facts = volume_facts(pd.Series([10] * 20 + [30], dtype="float64"))

    assert facts["available"] is True
    assert facts["ratios"]["5"]["ratio"] == 3
    assert facts["ratios"]["20"]["ratio"] == 3
    assert facts["classification"]["volume_state"] == "UNAVAILABLE"


def test_volume_facts_use_asset_relative_volume_and_directional_return_percentiles():
    returns = [0.4 if index % 2 else -0.4 for index in range(149)] + [-5.0]
    close = [100.0]
    for value in returns:
        close.append(close[-1] * (1 + value / 100))
    facts = volume_facts(
        pd.Series([100.0] * 150 + [130.0]),
        pd.Series(close),
    )

    assert facts["ratios"]["5"]["ratio"] == 1.3
    assert facts["ratios"]["120"]["ratio"] == 1.3
    assert facts["classification"]["volume_state"] == "HIGH"
    assert facts["classification"]["move"]["direction"] == "DOWN"
    assert facts["classification"]["move"]["level"] == "VERY_LARGE"
    assert facts["classification"]["event"] == "HIGH_VOLUME_DECLINE"


def test_volume_facts_marks_rebound_only_in_confirmed_downtrend_and_stabilizing_low_volume():
    returns = [0.4 if index % 2 else -0.4 for index in range(149)] + [5.0]
    close = [100.0]
    for value in returns:
        close.append(close[-1] * (1 + value / 100))

    rebound = volume_facts(
        pd.Series([100.0] * 150 + [130.0]),
        pd.Series(close),
        downtrend_active=True,
    )
    stabilizing = volume_facts(
        pd.Series([100.0] * 150 + [70.0]),
        pd.Series(close),
        downtrend_active=True,
        stabilizing=True,
    )

    assert rebound["classification"]["event"] == "HIGH_VOLUME_REBOUND"
    assert stabilizing["classification"]["event"] == "LOW_VOLUME_STABILIZING"


def test_volume_event_study_reports_following_one_three_and_five_day_outcomes():
    event_days = {125, 135, 145, 155, 165}
    close = [100.0]
    volume = [100.0]
    for day in range(1, 181):
        if day in event_days:
            change, day_volume = -3.0, 150.0
        elif day - 1 in event_days:
            change, day_volume = 1.0, 100.0
        else:
            change, day_volume = (-0.3 if day % 2 else 0.3), 100.0
        close.append(close[-1] * (1 + change / 100))
        volume.append(day_volume)

    facts = volume_facts(pd.Series(volume), pd.Series(close))
    study = facts["event_studies"]["HIGH_VOLUME_DECLINE"]

    assert study["sample_size"] == 5
    assert study["outcomes"]["1"]["positive_rate_pct"] == 100
    assert study["outcomes"]["3"]["sample_size"] == 5
    assert study["outcomes"]["5"]["sample_size"] == 5


def test_historical_rebound_study_replays_the_supplied_downtrend_context():
    event_days = {125, 135, 145, 155, 165}
    close = [100.0]
    volume = [100.0]
    for day in range(1, 181):
        if day in event_days:
            change, day_volume = 3.0, 150.0
        else:
            change, day_volume = (-0.3 if day % 2 else 0.3), 100.0
        close.append(close[-1] * (1 + change / 100))
        volume.append(day_volume)

    facts = volume_facts(
        pd.Series(volume),
        pd.Series(close),
        historical_context_at_index=lambda _index: {"downtrend_active": True, "stabilizing": False},
    )

    assert facts["event_studies"]["HIGH_VOLUME_REBOUND"]["sample_size"] == 5


def test_relative_strength_uses_overlapping_return_history_only():
    dates = pd.date_range("2026-01-01", periods=8, freq="D")
    history = pd.DataFrame({"date": dates, "close": [100, 101, 102, 103, 104, 105, 106, 108]})
    benchmark = pd.DataFrame({"date": dates, "close": [100, 100, 101, 102, 102, 103, 104, 105]})

    facts = relative_strength_facts(history, benchmark)

    assert facts["available"] is True
    assert facts["periods"]["5"]["relation"] == "STRONGER"
