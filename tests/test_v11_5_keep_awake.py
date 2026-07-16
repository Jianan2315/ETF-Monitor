from pathlib import Path

import pytest

from src.power_management import (
    _task_xml,
    _validate_window,
    keep_awake_status,
)


def test_validate_keep_awake_window():
    assert _validate_window("09:00", "15:30") == ("09:00", "15:30")
    with pytest.raises(ValueError):
        _validate_window("15:30", "09:00")
    with pytest.raises(ValueError):
        _validate_window("9am", "15:30")


def test_task_xml_contains_wake_and_weekdays(tmp_path):
    xml = _task_xml(
        tmp_path,
        "09:00",
        "15:30",
        "S-1-5-21-123",
    )
    assert "<WakeToRun>true</WakeToRun>" in xml
    assert "<Monday />" in xml
    assert "<Friday />" in xml
    assert "--until 15:30" in xml
    assert "keep_awake.py" in xml


def test_keep_awake_status_is_safe_off_windows(tmp_path):
    status = keep_awake_status(tmp_path)
    assert status["enabled"] is False
    assert status["platform_supported"] is False
