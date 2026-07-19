from pathlib import Path

import pytest
from flask import render_template

import control_server
from src.asset_store import asset_report_path, load_assets, save_asset
from src.html_report import generate_html_report
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


def test_asset_identity_forms_use_the_shared_two_column_layout_and_action_spacing():
    client = control_server.app.test_client()
    new_asset = client.get("/asset/new").get_data(as_text=True)
    edit_asset = client.get("/asset/manage").get_data(as_text=True)

    assert new_asset.count('class="asset-identity-grid"') == 1
    assert edit_asset.count('class="asset-identity-grid"') == 1
    field_order = ("资产名称", "持有代码", "跟踪名称", "跟踪代码")
    for page in (new_asset, edit_asset):
        positions = [page.index(label) for label in field_order]
        assert positions == sorted(positions)
    identity_grid = edit_asset.split('<div class="asset-identity-grid">', 1)[1].split("</div>", 1)[0]
    assert 'name="asset_enabled"' not in identity_grid
    assert '<div class="asset-enabled-row">' in edit_asset
    assert new_asset.count('class="form-actions"') == 1
    assert edit_asset.count('class="form-actions"') == 3

    styles = (control_server.ROOT / "control_web" / "static" / "control.css").read_text(encoding="utf-8")
    assert ".asset-identity-grid" in styles
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in styles
    assert "@media (max-width: 760px)" in styles
    assert ".form-actions" in styles


def test_asset_workspace_uses_clear_asset_specific_navigation_labels():
    client = control_server.app.test_client()
    body = client.get("/asset/manage").get_data(as_text=True)
    for label in ("返回控制台", "资产报告", "报告记录", "运行计划"):
        assert label in body
    assert "返回组合" not in body

    links = (control_server.ROOT / "control_web" / "static" / "asset_manage.js").read_text(encoding="utf-8")
    assert '"asset-report-link":"/report"' in links
    assert '"asset-history-link":"/history"' in links
    assert '"asset-schedule-link":"/schedules"' in links


def test_asset_report_and_history_return_to_the_same_asset_workspace(tmp_path: Path):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "dashboard.html").write_text(
        (control_server.ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = {
        "monitor_name": "Test",
        "report": {
            "asset_id": "demo",
            "template_file": "templates/dashboard.html",
            "output_file": "output/report.html",
            "history_directory": "output/history",
            "history_index_file": "output/history.html",
            "keep_history": True,
        },
    }
    generate_html_report([], config, tmp_path, run_mode="close", run_slot="1500")

    report = (tmp_path / "output" / "report.html").read_text(encoding="utf-8")
    history = (tmp_path / "output" / "history.html").read_text(encoding="utf-8")
    assert "/history?asset_id=demo" in report
    assert "/asset/manage?asset_id=demo" in report
    assert "返回资产管理页面" in report
    assert "/report?asset_id=demo" in history
    assert "/asset/manage?asset_id=demo" in history


def test_empty_asset_report_preserves_the_asset_management_return_link():
    with control_server.app.app_context():
        body = render_template(
            "empty_report.html",
            asset={"id": "demo", "name": "Demo"},
            message="No report yet",
        )
    assert "返回资产管理页面" in body
    assert "返回组合" not in body
    assert 'href="/asset/manage?asset_id=demo"' in body


def test_schedule_page_keeps_the_asset_id_when_returning_to_asset_management():
    client = control_server.app.test_client()
    body = client.get("/schedules?asset_id=demo").get_data(as_text=True)
    assert 'href="/asset/manage?asset_id=demo"' in body
    assert "返回资产管理页面" in body


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
