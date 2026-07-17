from pathlib import Path

import pytest
pytest.importorskip("flask")

import control_server


PLAN = {
    "id": "bf655ea1-81ba-4abc-9fc6-cd01d669e473",
    "name": "14:45 report",
    "mode": "auto",
    "time": "14:45",
    "weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
    "enabled": True,
    "task_name": "ETFMonitor_bf655ea181ba4abc9fc6cd01d669e473",
    "created_at": "2026-07-17T10:00:00",
    "updated_at": "2026-07-17T10:00:00",
}


def _task_xml(command: str, arguments: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>test</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>2026-07-17T14:45:00</StartBoundary></CalendarTrigger></Triggers>
  <Settings><Enabled>true</Enabled></Settings>
  <Actions><Exec><Command>{command}</Command><Arguments>{arguments}</Arguments></Exec></Actions>
</Task>'''


def test_legacy_cmd_task_is_migrated_when_schedule_page_loads(monkeypatch):
    legacy_xml = _task_xml(
        r"C:\Windows\System32\cmd.exe",
        rf'/c "C:\ETF-Monitor\data\scheduled_tasks\{PLAN["id"]}.cmd"',
    )
    synced = []

    monkeypatch.setattr(control_server.os, "name", "nt")
    monkeypatch.setattr(control_server, "_load_schedule_plans_raw", lambda: [dict(PLAN)])
    monkeypatch.setattr(control_server, "_system_task_names", lambda: [PLAN["task_name"]])
    monkeypatch.setattr(control_server, "_query_windows_task_xml", lambda name: legacy_xml)
    monkeypatch.setattr(control_server, "_task_exists", lambda name: True)
    monkeypatch.setattr(control_server, "_task_uses_hidden_runner", lambda plan, xml=None: False)
    monkeypatch.setattr(
        control_server,
        "_sync_plan_to_windows",
        lambda plan: synced.append(plan["id"]) or {"ok": True, "message": "ok"},
    )
    monkeypatch.setattr(control_server, "_save_schedule_plans", lambda plans: None)

    result = control_server._import_existing_windows_plans()

    assert result["migrated"] == 1
    assert synced == [PLAN["id"]]
    assert "后台隐藏运行器" in result["message"]


def test_current_pythonw_task_is_not_rewritten(monkeypatch):
    current_xml = _task_xml("pythonw.exe", "scheduled_runner.py")
    synced = []

    monkeypatch.setattr(control_server.os, "name", "nt")
    monkeypatch.setattr(control_server, "_load_schedule_plans_raw", lambda: [dict(PLAN)])
    monkeypatch.setattr(control_server, "_system_task_names", lambda: [PLAN["task_name"]])
    monkeypatch.setattr(control_server, "_query_windows_task_xml", lambda name: current_xml)
    monkeypatch.setattr(control_server, "_task_exists", lambda name: True)
    monkeypatch.setattr(control_server, "_task_uses_hidden_runner", lambda plan, xml=None: True)
    monkeypatch.setattr(
        control_server,
        "_sync_plan_to_windows",
        lambda plan: synced.append(plan["id"]) or {"ok": True, "message": "ok"},
    )
    monkeypatch.setattr(control_server, "_save_schedule_plans", lambda plans: None)

    result = control_server._import_existing_windows_plans()

    assert result["migrated"] == 0
    assert synced == []


def test_hidden_runner_xml_detection(monkeypatch):
    pythonw = Path("/tmp/ETF-Monitor/.venv/Scripts/pythonw.exe")
    runner = control_server.ROOT / "scheduled_runner.py"
    current_xml = _task_xml(
        str(pythonw),
        f'"{runner}" --plan-id "{PLAN["id"]}" --mode "auto" --slot "14:45"',
    )
    monkeypatch.setattr(control_server, "_schedule_pythonw", lambda: pythonw)

    assert control_server._task_uses_hidden_runner(PLAN, current_xml) is True


def test_legacy_wrapper_has_no_utf8_bom(monkeypatch, tmp_path):
    wrapper_dir = tmp_path / "scheduled_tasks"
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    monkeypatch.setattr(control_server, "SCHEDULE_WRAPPER_DIR", wrapper_dir)

    wrapper = control_server._write_plan_wrapper(dict(PLAN))
    raw = wrapper.read_bytes()

    assert raw.startswith(b"@echo off\r\n")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"chcp 65001 >nul\r\n" in raw
