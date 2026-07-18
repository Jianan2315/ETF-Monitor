import pytest
pytest.importorskip('flask')

from datetime import datetime

import control_server


def test_schedule_view_has_runtime_fields(monkeypatch, tmp_path):
    plan = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "test",
        "mode": "auto",
        "time": "14:45",
        "weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
        "enabled": True,
        "task_name": "ETFMonitor_test",
        "created_at": "2026-07-16T10:00:00",
        "updated_at": "2026-07-16T10:00:00",
    }
    monkeypatch.setattr(control_server.os, "name", "nt")
    monkeypatch.setattr(control_server, "_task_exists", lambda name: True)
    monkeypatch.setattr(
        control_server,
        "_schedule_runtime_status",
        lambda plan_id: {
            "status": "SUCCESS",
            "started_at": "2026-07-16T14:45:00",
            "finished_at": "2026-07-16T14:46:00",
            "return_code": 0,
            "message": "报告已生成",
            "report_generated": True,
            "email_status": "SENT",
        },
    )
    view = control_server._schedule_plan_view(plan)
    assert view["system_task_exists"] is True
    assert view["last_result"] == "SUCCESS"
    assert view["last_report_generated"] is True
    assert "next_run" in view
