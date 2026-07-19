from pathlib import Path

import pytest

import control_server
from src.asset_store import asset_report_path, load_assets, save_asset
from src.power_management import _window_context


def test_v13_2_system_tools_route_loads():
    client = control_server.app.test_client()
    response = client.get("/system-tools")
    assert response.status_code == 200
    assert "系统工具" in response.get_data(as_text=True)


def test_homepage_places_system_navigation_in_dedicated_cards():
    client = control_server.app.test_client()
    body = client.get("/").get_data(as_text=True)
    assert body.count('href="/system-tools"') == 1
    assert body.count('href="/system-settings"') == 1
    assert "打开系统工具" in body
    assert "打开系统设置" in body
    assert "页面顶部的“系统工具”入口" not in body


def test_system_tools_header_only_returns_to_dashboard():
    client = control_server.app.test_client()
    body = client.get("/system-tools").get_data(as_text=True)
    assert 'href="/"' in body
    assert "返回控制台" in body
    assert "返回组合" not in body
    assert 'href="/system-settings"' not in body


def test_system_settings_returns_to_dashboard():
    client = control_server.app.test_client()
    body = client.get("/system-settings").get_data(as_text=True)
    assert 'href="/"' in body
    assert "返回控制台" in body


def test_homepage_uses_consistent_disabled_asset_action_markup():
    client = control_server.app.test_client()
    body = client.get("/").get_data(as_text=True)
    assert 'id="edit-asset-link"' in body
    assert 'aria-disabled="true"' in body
    script = (control_server.ROOT / "control_web" / "static" / "control.js").read_text(encoding="utf-8")
    assert "请先选择或创建一个资产。" in script
    assert "button.disabled = !enabled" in script


def test_asset_forms_expose_only_the_four_identity_fields():
    client = control_server.app.test_client()
    new_asset = client.get("/asset/new").get_data(as_text=True)
    edit_asset = client.get("/asset/manage").get_data(as_text=True)
    for page in (new_asset, edit_asset):
        assert "资产类型" not in page
        assert "跟踪对象类型" not in page
        assert "目标仓位" not in page
        assert "进入当前资产" not in page
    assert "资产名称" in new_asset
    assert "持有代码" in new_asset
    assert "跟踪名称" in new_asset
    assert "跟踪代码" in new_asset


def test_asset_basic_configuration_and_strategy_are_stored_separately(tmp_path: Path):
    saved = save_asset(tmp_path, {
        "id": "grid-fund",
        "name": "Grid Fund",
        "asset_code": "012345",
        "tracking_name": "Grid ETF",
        "tracking_code": "159326",
        "strategy": {"planned_capital": 100000},
    })
    assert saved["strategy"]["planned_capital"] == 100000
    assert (tmp_path / "config" / "assets" / "grid-fund.json").exists()
    assert (tmp_path / "config" / "assets" / "grid-fund.strategy.json").exists()
    loaded = load_assets(tmp_path)[0]
    assert loaded["tracking_code"] == "159326"
    assert loaded["strategy"]["planned_capital"] == 100000
    assert asset_report_path(tmp_path, "grid-fund") == tmp_path / "output" / "assets" / "grid-fund" / "latest" / "report.html"


def test_keep_awake_status_contains_timezone_context():
    context = _window_context()
    assert context["system_timezone"]
    assert context["beijing_time"]
    assert "–" in context["local_window"]


def test_runtime_layout_has_no_legacy_private_or_scheduler_directory(tmp_path: Path):
    save_asset(tmp_path, {
        "id": "demo",
        "name": "Demo",
        "asset_code": "001",
        "tracking_name": "Demo index",
        "tracking_code": "002",
    })
    assert (tmp_path / "config" / "private").is_dir()
    assert (tmp_path / "data" / "global" / "scheduled_tasks").is_dir()
    assert not (tmp_path / "data" / "global" / "private").exists()
    assert not (tmp_path / "data" / "scheduled_tasks").exists()
