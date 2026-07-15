import pytest

pytest.importorskip("flask")

import control_server


def test_index_route_loads():
    client = control_server.app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "ETF Monitor 控制台" in response.get_data(as_text=True)


def test_settings_route_loads():
    client = control_server.app.test_client()
    response = client.get("/settings")
    assert response.status_code == 200
    assert "参数管理" in response.get_data(as_text=True)


def test_settings_api_returns_expected_sections():
    client = control_server.app.test_client()
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.get_json()
    assert "asset" in data
    assert "email" in data
    assert "llm" in data
    assert "data" in data


def test_schedules_route_loads():
    client = control_server.app.test_client()
    response = client.get("/schedules")
    assert response.status_code == 200
    assert "定时运行计划" in response.get_data(as_text=True)


def test_schedule_mode_time_validation():
    control_server._validate_plan_time_for_mode("intraday", "09:30")
    control_server._validate_plan_time_for_mode("intraday", "15:00")
    control_server._validate_plan_time_for_mode("close", "15:05")

    with pytest.raises(ValueError):
        control_server._validate_plan_time_for_mode("intraday", "09:29")

    with pytest.raises(ValueError):
        control_server._validate_plan_time_for_mode("close", "15:04")


def test_schedule_weekday_validation():
    assert control_server._normalize_weekdays(["FRI", "MON", "MON"]) == ["MON", "FRI"]
    with pytest.raises(ValueError):
        control_server._normalize_weekdays([])


def test_control_version_endpoint():
    client = control_server.app.test_client()
    response = client.get("/api/version")
    assert response.status_code == 200
    data = response.get_json()
    assert data["version"] == "10.4"
    assert "pid" in data
