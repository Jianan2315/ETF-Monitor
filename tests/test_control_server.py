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
