import pytest

pytest.importorskip("flask")

import control_server


def test_index_route_loads():
    client = control_server.app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "Portfolio Monitor" in response.get_data(as_text=True)


def test_settings_route_loads():
    client = control_server.app.test_client()
    response = client.get("/settings")
    assert response.status_code == 200
    assert "系统设置" in response.get_data(as_text=True)


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
    assert data["version"] == "13.2.0"
    assert "pid" in data


def test_manual_report_attempts_to_start_enabled_ollama(monkeypatch):
    calls = []
    monkeypatch.setattr(control_server, "_ollama_enabled", lambda: True)
    monkeypatch.setattr(control_server, "_start_ollama", lambda: {"ok": False, "message": "Ollama unavailable"})
    monkeypatch.setattr(
        control_server,
        "_run_monitor",
        lambda mode, slot, asset_id, *, skip_llm=False: calls.append((mode, slot, asset_id, skip_llm)) or {"success": True, "message": "报告已生成"},
    )

    response = control_server.app.test_client().post(
        "/api/report/run",
        json={"mode": "manual", "asset_id": "015596"},
    )

    data = response.get_json()
    assert calls == [("manual", "", "015596", True)]
    assert data["ollama"]["ok"] is False
    assert "已继续生成报告" in data["message"]


def test_ollama_stop_api_delegates_to_safe_stop_helper(monkeypatch):
    monkeypatch.setattr(
        control_server,
        "_stop_ollama",
        lambda: {"ok": True, "message": "已关闭由控制台启动的本地模型服务。", "models": []},
    )
    response = control_server.app.test_client().post("/api/ollama/stop")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_ollama_stop_api_allows_confirmed_external_shutdown(monkeypatch):
    calls = []
    monkeypatch.setattr(
        control_server,
        "_stop_ollama",
        lambda *, force_external=False: calls.append(force_external) or {"ok": True},
    )
    response = control_server.app.test_client().post(
        "/api/ollama/stop", json={"force_external": True}
    )
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert calls == [True]


def test_eastmoney_failure_kind_distinguishes_proxy_and_direct_disconnects():
    assert control_server._eastmoney_failure_kind(
        RuntimeError("ProxyError: Unable to connect to proxy")
    ) == "代理链路连接失败"
    assert control_server._eastmoney_failure_kind(
        RuntimeError("Connection aborted; RemoteDisconnected")
    ) == "直连被远端或中间网络关闭"


def test_transaction_api_lists_records():
    client = control_server.app.test_client()
    response = client.get("/api/transactions")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert isinstance(data["records"], list)


def test_asset_manage_page_contains_transaction_manager():
    client = control_server.app.test_client()
    response = client.get("/asset/manage")
    body = response.get_data(as_text=True)
    assert 'id="save-transaction">添加交易<' in body
    assert 'id="tx-toast"' in body
    assert "交易记录" in body
    assert "交易金额（元）" in body
    assert "tx-shares" not in body

def test_new_asset_page_is_separate():
    client = control_server.app.test_client()
    body = client.get("/asset/new").get_data(as_text=True)
    assert "添加资产" in body
    assert "选择资产" not in body
