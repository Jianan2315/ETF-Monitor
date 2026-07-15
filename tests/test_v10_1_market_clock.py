from datetime import datetime

from src.market_clock import (
    build_market_context,
    classify_market_phase,
    should_request_realtime,
    validate_run_mode,
)


def test_market_phases():
    assert classify_market_phase(datetime(2026, 7, 15, 8, 30)) == "PRE_OPEN"
    assert classify_market_phase(datetime(2026, 7, 15, 10, 0)) == "AM_SESSION"
    assert classify_market_phase(datetime(2026, 7, 15, 12, 20)) == "MIDDAY_BREAK"
    assert classify_market_phase(datetime(2026, 7, 15, 14, 45)) == "PM_SESSION"
    assert classify_market_phase(datetime(2026, 7, 15, 15, 10)) == "AFTER_CLOSE"
    assert classify_market_phase(datetime(2026, 7, 18, 10, 0)) == "WEEKEND"


def test_intraday_mode_is_allowed_during_midday_break():
    result = validate_run_mode("intraday", datetime(2026, 7, 15, 12, 20))
    assert result.allowed is True
    assert result.context.phase == "MIDDAY_BREAK"


def test_intraday_mode_rejected_before_open_and_after_close():
    assert validate_run_mode("intraday", datetime(2026, 7, 15, 8, 30)).allowed is False
    assert validate_run_mode("intraday", datetime(2026, 7, 15, 15, 1)).allowed is False


def test_close_mode_rejected_before_1505():
    result = validate_run_mode("close", datetime(2026, 7, 15, 15, 4))
    assert result.allowed is False
    assert "15:05" in result.message


def test_close_mode_allowed_after_1505():
    assert validate_run_mode("close", datetime(2026, 7, 15, 15, 5)).allowed is True


def test_auto_uses_realtime_during_midday_break():
    context = build_market_context(datetime(2026, 7, 15, 12, 20))
    assert should_request_realtime("auto", context) is True


def test_auto_uses_final_snapshot_between_1500_and_1505():
    context = build_market_context(datetime(2026, 7, 15, 15, 2))
    assert context.phase == "AFTER_CLOSE"
    assert context.close_ready is False
    assert should_request_realtime("auto", context) is True
