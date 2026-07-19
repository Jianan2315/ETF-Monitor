import json
from pathlib import Path

import pytest

import src.power_management as power_management
from src.power_management import (
    _parse_task_xml,
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
    assert "<WakeToRun>false</WakeToRun>" in xml
    assert "<Monday />" in xml
    assert "<Friday />" in xml
    assert "--until 15:30" in xml
    assert "keep_awake.py" in xml


def test_keep_awake_status_is_safe_off_windows(tmp_path):
    status = keep_awake_status(tmp_path)
    assert status["enabled"] is False
    assert status["platform_supported"] is (power_management.os.name == "nt")


def test_parse_existing_windows_task_xml(tmp_path):
    xml = _task_xml(
        tmp_path,
        "08:45",
        "15:40",
        "S-1-5-21-123",
    )
    parsed = _parse_task_xml(xml)
    assert parsed["enabled"] is True
    assert parsed["start"] == "08:45"
    assert parsed["end"] == "15:40"
    assert parsed["weekdays"] == ["MON", "TUE", "WED", "THU", "FRI"]
    assert parsed["wake_to_run"] is False


def test_status_recovers_existing_windows_task_when_local_config_is_stale(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "config" / "power.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({
            "keep_awake": {
                "enabled": False,
                "start": "09:00",
                "end": "15:30",
            }
        }),
        encoding="utf-8",
    )
    task_settings = {
        "enabled": True,
        "start": "08:50",
        "end": "15:35",
        "weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
        "task_name": power_management.TASK_NAME,
        "wake_to_run": True,
    }
    monkeypatch.setattr(power_management.os, "name", "nt")
    monkeypatch.setattr(
        power_management,
        "_windows_task_settings",
        lambda: task_settings,
    )

    status = keep_awake_status(tmp_path)

    assert status["enabled"] is True
    assert status["task_exists"] is True
    assert status["detected_from_windows"] is True
    assert status["source"] == "windows_task_scheduler"
    assert status["start"] == "08:50"
    assert status["end"] == "15:35"
    assert "Windows任务计划程序" in status["message"]

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["keep_awake"]["enabled"] is True
    assert saved["keep_awake"]["start"] == "08:50"
    assert saved["keep_awake"]["end"] == "15:35"
    assert saved["keep_awake"]["detected_from_windows"] is True
