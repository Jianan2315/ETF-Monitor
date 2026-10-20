from datetime import datetime

from src.sell_reminders import evaluate_sell_reminder, record_sell_reminder_sent, stop_sell_reminders


def result(shares=100, tier="SELL_REVIEW"):
    return {
        "asset": {"strategy": {"sell_first_stop_reduction_pct": 50, "sell_residual_position_pct": 10, "sell_residual_max_reminder_days": 5}},
        "decision": {"status": "PARTIAL_SELL", "reason_code": tier},
        "position_metrics": {"shares": shares},
        "indicators": {"cycle_analysis": {"current_cycle_id": "UP:2026-07-01"}},
    }


def test_first_tier_has_no_per_day_or_total_reminder_limit(tmp_path):
    first = evaluate_sell_reminder(tmp_path, "fund", result(), datetime(2026, 7, 1, 10))
    assert first["allowed"] is True
    record_sell_reminder_sent(tmp_path, "fund", first["state"], 1, datetime(2026, 7, 1, 10))
    assert evaluate_sell_reminder(tmp_path, "fund", result(), datetime(2026, 7, 1, 14))["allowed"]

    for day in range(2, 8):
        next_reminder = evaluate_sell_reminder(tmp_path, "fund", result(), datetime(2026, 7, day, 10))
        assert next_reminder["allowed"]
        record_sell_reminder_sent(tmp_path, "fund", next_reminder["state"], 1, datetime(2026, 7, day, 10))


def test_first_tier_stops_after_half_of_reference_shares_are_sold(tmp_path):
    first = evaluate_sell_reminder(tmp_path, "fund", result(100), datetime(2026, 7, 1))
    assert first["allowed"]
    assert evaluate_sell_reminder(tmp_path, "fund", result(51), datetime(2026, 7, 2))["allowed"]
    assert not evaluate_sell_reminder(tmp_path, "fund", result(50), datetime(2026, 7, 3))["allowed"]


def test_manual_stop_applies_only_to_the_current_cycle(tmp_path):
    evaluate_sell_reminder(tmp_path, "fund", result(), datetime(2026, 7, 1))
    state = stop_sell_reminders(tmp_path, "fund", "keep the remaining position")
    assert state["manual_stop"]
    assert not evaluate_sell_reminder(tmp_path, "fund", result(), datetime(2026, 7, 2))["allowed"]

    next_cycle = result()
    next_cycle["indicators"]["cycle_analysis"]["current_cycle_id"] = "UP:2026-08-01"
    assert evaluate_sell_reminder(tmp_path, "fund", next_cycle, datetime(2026, 8, 1))["allowed"]


def test_residual_second_tier_reminders_stop_after_five_trading_days(tmp_path):
    # The reference position belongs to the market cycle, not to the date or
    # the tier.  Record it before the holding falls to the residual 10%.
    evaluate_sell_reminder(tmp_path, "fund", result(100), datetime(2026, 7, 1))
    for day in range(2, 7):
        reminder = evaluate_sell_reminder(tmp_path, "fund", result(10, "EXIT_REVIEW"), datetime(2026, 7, day))
        assert reminder["allowed"]
        record_sell_reminder_sent(tmp_path, "fund", reminder["state"], 2, datetime(2026, 7, day))
    assert not evaluate_sell_reminder(tmp_path, "fund", result(10, "EXIT_REVIEW"), datetime(2026, 7, 7))["allowed"]
