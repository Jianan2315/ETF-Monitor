from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

import pytest
from flask import render_template

import control_server
from src.asset_store import asset_report_path, load_assets, save_asset
from src.html_report import generate_history_index, generate_html_report
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
    assert 'id="schedule-link"' in body
    assert 'id="edit-asset-link"' not in body
    assert 'id="latest-report-link"' not in body
    assert 'aria-disabled="true"' in body
    script = (control_server.ROOT / "control_web" / "static" / "control.js").read_text(encoding="utf-8")
    assert "请先选择或创建一个资产。" in script
    assert "button.disabled = !enabled" in script


def test_dashboard_schedule_action_uses_the_explicit_plan_label():
    client = control_server.app.test_client()
    body = client.get("/").get_data(as_text=True)
    assert 'id="schedule-link"' in body
    assert "定时运行计划" in body
    assert '>资产计划</a>' not in body

    script = (control_server.ROOT / "control_web" / "static" / "control.js").read_text(encoding="utf-8")
    assert 'setLink("schedule-link", `/schedules?asset_id=${id}`, !!selected)' in script


def test_homepage_has_one_add_asset_entry_and_a_button_free_empty_state():
    client = control_server.app.test_client()
    body = client.get("/").get_data(as_text=True)
    script = (control_server.ROOT / "control_web" / "static" / "control.js").read_text(encoding="utf-8")

    assert body.count('href="/asset/new"') == 1
    assert "添加第一个资产" not in script
    assert "添加资产后即可开始监控。" in script
    assert "添加第一个资产后即可开始监控。" not in script
    assert '<article class="empty-state"><h3>暂无资产</h3><p>添加资产后即可开始监控。</p></article>' in script


def test_homepage_keeps_asset_actions_only_inside_asset_cards():
    client = control_server.app.test_client()
    body = client.get("/").get_data(as_text=True)
    script = (control_server.ROOT / "control_web" / "static" / "control.js").read_text(encoding="utf-8")

    assert 'href="/asset/new"' in body
    assert "编辑当前资产" not in body
    assert "latest-report-link" not in body
    assert "edit-asset-link" not in body
    assert '>编辑</a><a class="secondary-button" href="/report?asset_id=${encodeURIComponent(a.id)}">查看报告</a>' in script
    assert ">报告</a>" not in script
    assert script.count('href="/asset/manage?asset_id=${encodeURIComponent(a.id)}"') == 1
    assert script.count('href="/report?asset_id=${encodeURIComponent(a.id)}"') == 1


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


def test_add_asset_returns_to_dashboard_with_consistent_wording():
    client = control_server.app.test_client()
    body = client.get("/asset/new").get_data(as_text=True)
    assert 'href="/"' in body
    assert "返回控制台" in body
    assert "返回组合" not in body


def test_asset_workspace_uses_clear_asset_specific_navigation_labels():
    client = control_server.app.test_client()
    body = client.get("/asset/manage").get_data(as_text=True)
    for label in ("返回控制台", "最新资产报告", "过往报告记录", "定时运行计划"):
        assert label in body
    assert "返回组合" not in body
    assert '>资产报告</a>' not in body
    assert '>报告记录</a>' not in body
    assert '>运行计划</a>' not in body

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
    result = SimpleNamespace(
        asset=SimpleNamespace(
            asset_code="510300",
            symbol="510300",
            is_estimated_tracking=False,
            tracking_code="510300",
            name="Demo ETF",
            strategy=SimpleNamespace(name="Demo strategy", version="1"),
            strategy_document=None,
        ),
        data_source="Test provider",
        using_local_cache=False,
        cache_date=None,
        data_phase_label="收盘后",
        data_phase="AFTER_CLOSE",
        market_timestamp="2026-07-20 15:00",
        run_time="2026-07-20 15:01",
        data_warnings=[],
        data_status={
            "history": {},
            "realtime": {},
            "intraday": {"status": "SUCCESS", "message": "今日分时获取成功", "source": "Test provider"},
            "previous_intraday": {
                "status": "SUCCESS",
                "date": "2026-07-17",
                "period": "1",
                "points": 241,
                "source": "AKShare fund_etf_hist_min_em(1m)",
            },
        },
        decision=SimpleNamespace(status="HOLD", suggested_amount=0, reasons=["测试规则"]),
        status_label="持有",
        position_metrics=SimpleNamespace(
            invested_amount=1000,
            shares=100,
            average_cost=10,
            profit_pct=1,
            profit_amount=10,
        ),
        llm_summary=None,
        indicators=SimpleNamespace(
            daily_change_pct=1,
            five_day_change_pct=1,
            ten_day_change_pct=1,
            twenty_day_change_pct=1,
            one_month_high_date="2026-07-01",
            one_month_drawdown_pct=1,
            three_month_high_date="2026-06-01",
            three_month_drawdown_pct=1,
            six_month_high_date="2026-01-01",
            six_month_drawdown_pct=1,
            consecutive_down_days=0,
            consecutive_up_days=0,
            consecutive_trend_pct=0,
            drawdown_from_last_buy_pct=0,
        ),
        operation_history=[],
        intraday_chart_data=[],
        previous_intraday_chart_data=[],
        chart_data=[],
    )
    generate_html_report([result], config, tmp_path, run_mode="close", run_slot="1500")

    report = (tmp_path / "output" / "report.html").read_text(encoding="utf-8")
    history = (tmp_path / "output" / "history.html").read_text(encoding="utf-8")
    assert "/history?asset_id=demo" in report
    assert "/asset/manage?asset_id=demo" in report
    assert "返回资产管理页面" in report
    assert 'href="/">返回控制台</a>' in report
    assert 'class="secondary-button" href="/asset/manage?asset_id=demo"' in report
    assert 'class="secondary-button" href="/"' in report
    assert "返回组合" not in report
    assert "上一交易日走势（2026-07-17）" in report
    assert "昨日走势" not in report
    assert "1 分钟走势获取成功，共 241 个点；来源：AKShare fund_etf_hist_min_em(1m)。" in report
    assert "2026-07-171分钟走势" not in report
    assert 'class="report-chart-sections"' in report
    assert "/report?asset_id=demo" in history
    assert "/asset/manage?asset_id=demo" in history
    assert 'href="/">返回控制台</a>' in history
    assert "过往报告记录 — Demo ETF" in history
    assert 'class="shell"' in history
    assert 'class="hero compact"' in history
    assert 'class="card"' in history
    assert 'class="table-wrapper"' in history
    assert "生成时间" in history

    report_styles = (control_server.ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ".report-chart-sections" in report_styles
    assert "gap:24px" in report_styles
    assert ".asset-heading + .report-rule-grid{margin-top:20px}" in report_styles
    assert ".report-header-actions .secondary-button" in report_styles
    assert "background:#e7edf1" in report_styles


def test_empty_asset_report_preserves_the_asset_management_return_link():
    with control_server.app.app_context():
        body = render_template(
            "empty_report.html",
            asset={"id": "demo", "name": "Demo"},
            message="No report yet",
        )
    assert "返回资产管理页面" in body
    assert "返回控制台" in body
    assert "返回组合" not in body
    assert 'href="/asset/manage?asset_id=demo"' in body
    assert 'href="/">返回控制台</a>' in body


def test_history_snapshots_are_collision_safe_and_keep_second_precision(tmp_path: Path):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "dashboard.html").write_text(
        (control_server.ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = {
        "report": {
            "asset_id": "demo",
            "template_file": "templates/dashboard.html",
            "output_file": "output/report.html",
            "history_directory": "output/history",
            "history_index_file": "output/history.html",
            "keep_history": True,
        },
    }
    timestamps = [
        datetime(2026, 7, 20, 10, 31, 10),
        datetime(2026, 7, 20, 10, 31, 20),
        datetime(2026, 7, 20, 10, 31, 20, 500000),
    ]
    snapshots = [
        generate_html_report([], config, tmp_path, run_mode="auto", run_slot="1031", generated_at=timestamp)
        for timestamp in timestamps
    ]
    history_dir = tmp_path / "output" / "history"
    assert len(set(snapshots)) == 3
    assert len(list(history_dir.glob("*.html"))) == 3
    assert all("_1031_auto" not in path.name for path in snapshots)

    history_path = generate_history_index(
        tmp_path,
        asset_id="demo",
        asset_name="Demo ETF",
        asset_code="510300",
        history_dir=history_dir,
        index_path=tmp_path / "output" / "history.html",
    )
    history = history_path.read_text(encoding="utf-8")
    assert "过往报告记录 — Demo ETF" in history
    assert "10:31:10" in history
    assert history.count("10:31:20") == 2
    assert history.index(snapshots[2].name) < history.index(snapshots[1].name) < history.index(snapshots[0].name)


def test_history_route_rebuilds_a_styled_asset_specific_empty_state(monkeypatch, tmp_path: Path):
    save_asset(tmp_path, {
        "id": "demo",
        "name": "Demo ETF",
        "asset_code": "510300",
        "tracking_name": "Demo index",
        "tracking_code": "510300",
    })
    monkeypatch.setattr(control_server, "ROOT", tmp_path)

    response = control_server.app.test_client().get("/history?asset_id=demo")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "过往报告记录 — Demo ETF" in body
    assert "暂无过往报告" in body
    assert 'class="shell"' in body
    assert 'class="card"' in body
    assert 'href="/report?asset_id=demo"' in body
    assert 'href="/asset/manage?asset_id=demo"' in body
    assert 'href="/">返回控制台</a>' in body


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
