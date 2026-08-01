import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

import pytest
from flask import render_template

import control_server
from src.asset_store import asset_cache_dir, asset_latest_path, asset_report_path, load_assets, save_asset
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


def test_homepage_parameter_onboarding_marks_required_and_optional_items(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    save_asset(tmp_path, {
        "id": "demo", "name": "演示资产", "asset_code": "demo",
        "tracking_code": "123456", "tracking_name": "演示跟踪标的",
        "strategy": {"strategy_type": "CYCLICAL"},
    })
    monkeypatch.setattr(control_server, "REQUIRED_PARAMETER_DEFINITIONS", (
        {"key": "new_required_setting", "label": "新的必填参数", "description": "测试用。"},
    ))

    client = control_server.app.test_client()
    first = client.get("/api/parameter-onboarding").get_json()

    assert first["required"] == [{
        "asset_id": "demo", "asset_name": "演示资产", "key": "new_required_setting",
        "label": "新的必填参数", "description": "测试用。",
        "settings_url": "/trigger-conditions?asset_id=demo",
    }]
    assert {item["id"] for item in first["optional"]} == {
        "planned_investment_reference", "normal_add_amount", "enhanced_add_amount", "relative_strength_benchmark",
    }

    response = client.post("/api/parameter-onboarding/dismiss-optionals", json={"items": first["optional"]})
    assert response.status_code == 200
    assert response.get_json()["dismissed_count"] == 4
    assert client.get("/api/parameter-onboarding").get_json()["optional"] == []
    state = json.loads((tmp_path / "data" / "global" / "parameter_prompt_state.json").read_text(encoding="utf-8"))
    assert len(state["optional_dismissals"]) == 4


def test_homepage_includes_parameter_onboarding_modal_and_client_check():
    body = control_server.app.test_client().get("/").get_data(as_text=True)
    script = (control_server.ROOT / "control_web" / "static" / "control.js").read_text(encoding="utf-8")

    assert 'id="parameter-onboarding-modal"' in body
    assert 'id="parameter-onboarding-dismiss-optionals"' in body
    assert 'fetch("/api/parameter-onboarding"' in script
    assert 'dismiss-optionals' in script


def test_system_settings_explains_each_email_trigger_before_its_checkboxes():
    body = control_server.app.test_client().get("/system-settings").get_data(as_text=True)

    assert 'class="trigger-explainer"' in body
    assert body.index('class="trigger-explainer"') < body.index('class="checkbox-grid"')
    for status in (
        "OBSERVE（观察）",
        "REVIEW_BUY（人工复核加仓）",
        "REVIEW_SELL（人工复核下行风险）",
        "PARTIAL_SELL（部分卖出）",
    ):
        assert status in body


def test_system_settings_save_is_global_and_has_no_default_report_time_fields(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    response = control_server.app.test_client().post("/api/system-settings/save", json={
        "llm": {"enabled": True, "model": "test-model", "timeout_seconds": "120"},
        "email": {"enabled": True, "recipient": "test@example.com", "triggers": {"WAIT": True}},
        "data": {"history_days": "300", "retry_max_attempts": "2"},
        "report": {"retention_days": "90", "max_history_files": "50"},
    })
    body = response.get_json()
    page = control_server.app.test_client().get("/system-settings").get_data(as_text=True)

    assert response.status_code == 200
    assert body["success"] is True
    assert json.loads((tmp_path / "config" / "global" / "email.json").read_text(encoding="utf-8"))["email"]["triggers"]["OBSERVE"] is True
    assert "trigger-conditions" in page
    assert "intraday_slots" not in page
    assert "close_slot" not in page


def test_trigger_conditions_page_edits_asset_specific_strategy_settings():
    client = control_server.app.test_client()
    response = client.get("/trigger-conditions")
    body = response.get_data(as_text=True)
    script = (control_server.ROOT / "control_web" / "static" / "trigger_conditions.js").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'name="consecutive_down_warning_days"' not in body
    assert 'name="rebound_recovery_threshold_pct"' in body
    assert 'name="downtrend_watch_percentile"' in body
    assert 'name="downtrend_risk_percentile"' in body
    assert 'name="same_price_band_pct"' in body
    assert 'name="same_price_band_max_buys"' in body
    assert 'name="planned_investment_reference"' in body
    assert 'name="relative_strength_benchmark_code"' in body
    assert 'name="volume_high_ratio"' in body
    assert 'name="volume_move_very_large_percentile"' in body
    assert 'name="sell_first_percentile"' in body
    assert 'fetch(api("/api/settings/save")' in script


def test_dashboard_schedule_action_uses_the_explicit_plan_label():
    client = control_server.app.test_client()
    body = client.get("/").get_data(as_text=True)
    assert 'id="schedule-link"' in body
    assert "定时运行计划" in body
    assert '>资产计划</a>' not in body

    script = (control_server.ROOT / "control_web" / "static" / "control.js").read_text(encoding="utf-8")
    assert 'setLink("schedule-link", `/schedules?asset_id=${id}`, !!selected)' in script
    assert 'fetch(`/api/environment${assetQuery}`, {cache: "no-store"})' in script


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
    assert '>编辑</a><a class="secondary-button" href="/report?asset_id=${encodeURIComponent(selected.id)}">查看报告</a>' in script
    assert ">报告</a>" not in script
    # Normal and graceful-degradation cards expose the same scoped actions.
    assert script.count('href="/asset/manage?asset_id=${encodeURIComponent(selected.id)}"') == 2
    assert script.count('href="/report?asset_id=${encodeURIComponent(selected.id)}"') == 2
    assert 'fetch(`/api/assets/summary?asset_id=${encodeURIComponent(selected.id)}`' in script
    assert 'if (!response.ok) throw new Error(`HTTP ${response.status}`);' in script
    assert "资产摘要暂时无法读取；运行状态仍可正常查看。" in script


def test_homepage_summary_uses_previous_close_and_marks_today_pending_buy(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    monkeypatch.setattr(control_server, "market_now", lambda _config=None: datetime(2026, 7, 23, 14, 0))
    asset = save_asset(tmp_path, {
        "id": "123456", "name": "Demo", "asset_code": "123456",
        "tracking_code": "399001", "tracking_name": "Demo index",
    })
    transactions_path = tmp_path / "data" / "assets" / "123456" / "transactions.json"
    transactions_path.write_text(json.dumps({"123456": [
        {"date": "2026-07-22", "type": "BUY", "amount": 100, "shares": 10, "price": 10, "status": "CONFIRMED"},
        {"date": "2026-07-23", "type": "BUY", "amount": 50, "shares": 0, "price": 0, "status": "PENDING_CLOSE"},
    ]}), encoding="utf-8")
    cache_path = asset_cache_dir(tmp_path, "123456") / "399001_daily.json"
    cache_path.write_text(json.dumps({"rows": [{"date": "2026-07-22", "close": 12}]}), encoding="utf-8")

    response = control_server.app.test_client().get("/api/assets/summary?asset_id=123456")
    body = response.get_json()

    assert response.status_code == 200
    assert asset["id"] == "123456"
    assert body["position"]["invested_amount"] == 100
    assert body["position"]["profit_amount"] == 20
    assert body["uses_previous_close"] is True
    assert body["today_buy_pending"] is True
    assert body["today_buy_amount"] == 50


def test_homepage_reconciles_prior_day_pending_buy_from_asset_cache(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    monkeypatch.setattr(control_server, "market_now", lambda _config=None: datetime(2026, 7, 24, 10, 0))
    save_asset(tmp_path, {
        "id": "123456", "name": "Demo", "asset_code": "123456",
        "tracking_code": "399001", "tracking_name": "Demo index",
    })
    transactions_path = tmp_path / "data" / "assets" / "123456" / "transactions.json"
    transactions_path.write_text(json.dumps({"123456": [{
        "id": "pending-buy", "date": "2026-07-23", "type": "BUY",
        "amount": 1000, "shares": 0, "price": 0,
        "pricing_mode": "CLOSE_ESTIMATE", "status": "PENDING_CLOSE",
    }]}), encoding="utf-8")
    cache_path = asset_cache_dir(tmp_path, "123456") / "399001_daily.json"
    cache_path.write_text(json.dumps({"rows": [
        {"date": "2026-07-23", "close": 20},
    ]}), encoding="utf-8")

    body = control_server.app.test_client().get("/api/assets/summary?asset_id=123456").get_json()

    assert body["position"]["invested_amount"] == 1000
    assert body["position"]["shares"] == 50
    assert body["pending_buy_count"] == 0
    stored = json.loads(transactions_path.read_text(encoding="utf-8"))["123456"][0]
    assert stored["status"] == "ESTIMATED"
    assert stored["price"] == 20


def test_homepage_refreshes_the_asset_scoped_cache_for_an_eligible_pending_buy(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    monkeypatch.setattr(control_server, "market_now", lambda _config=None: datetime(2026, 7, 24, 10, 0))
    asset = save_asset(tmp_path, {
        "id": "987654", "name": "Demo", "asset_code": "987654",
        "tracking_code": "399001", "tracking_name": "Demo index",
    })
    transactions_path = tmp_path / "data" / "assets" / "987654" / "transactions.json"
    transactions_path.write_text(json.dumps({"987654": [{
        "id": "pending-buy", "date": "2026-07-23", "type": "BUY",
        "amount": 1000, "pricing_mode": "CLOSE_ESTIMATE", "status": "PENDING_CLOSE",
    }]}), encoding="utf-8")
    cache_path = asset_cache_dir(tmp_path, "987654") / "399001_daily.json"
    cache_path.write_text(json.dumps({"rows": [{"date": "2026-07-22", "close": 19}]}), encoding="utf-8")
    captured = {}

    def fake_refresh(received_asset, config, root, logger, now):
        captured["asset"] = received_asset
        captured["cache_directory"] = config["data"]["cache_directory"]
        refreshed_path = root / config["data"]["cache_directory"] / "399001_daily.json"
        refreshed_path.write_text(json.dumps({"rows": [{"date": "2026-07-23", "close": 20}]}), encoding="utf-8")
        return {"success": True, "message": "refreshed"}

    monkeypatch.setattr(control_server, "refresh_history_cache_now", fake_refresh)
    body = control_server.app.test_client().get("/api/assets/summary?asset_id=987654").get_json()

    assert captured["asset"]["id"] == asset["id"]
    assert captured["cache_directory"] == "data/assets/987654/cache"
    assert body["settlement"]["refreshed"] is True
    assert body["position"]["invested_amount"] == 1000


def test_asset_strategy_files_contain_only_current_rule_parameters():
    allowed = {
        "new_low_window_days", "stable_days_required", "stable_range_pct",
        "downtrend_watch_percentile", "downtrend_risk_percentile",
        "same_price_band_pct", "same_price_band_max_buys", "planned_investment_reference",
        "relative_strength_benchmark_code", "relative_strength_benchmark_name",
        "volume_high_ratio", "volume_low_ratio", "volume_required_window_count",
        "volume_move_notice_percentile", "volume_move_very_large_percentile", "volume_move_history_lookback_days",
        "normal_add_amount", "enhanced_add_amount",
        "strategy_type", "cycle_confirmation_pct", "rebound_recovery_threshold_pct",
        "deep_drawdown_percentile", "sell_first_percentile", "sell_second_percentile",
        "sell_early_alert_points", "sell_first_max_reminder_days",
        "sell_first_stop_reduction_pct", "sell_residual_position_pct",
        "sell_residual_max_reminder_days",
    }
    strategy_files = list((control_server.ROOT / "config" / "assets").glob("*_strategy.json"))

    assert strategy_files
    for path in strategy_files:
        strategy = json.loads(path.read_text(encoding="utf-8")).get("strategy", {})
        assert set(strategy).issubset(allowed)


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


def test_transaction_history_is_collapsed_and_supports_batch_delete_and_chart():
    client = control_server.app.test_client()
    body = client.get("/asset/manage").get_data(as_text=True)
    script = (control_server.ROOT / "control_web" / "static" / "asset_manage.js").read_text(encoding="utf-8")

    assert '<details id="transaction-history"' in body
    assert 'id="delete-selected-transactions"' in body
    assert 'id="transaction-chart"' in body
    assert 'data-chart-range="3M"' in body
    assert 'data-chart-range="15D"' in body
    assert "/api/transactions/delete-batch" in script
    assert "/api/transactions/chart" in script
    assert "chartRangeStart" in script
    assert "此操作不可恢复" in script


def test_transaction_chart_and_batch_delete_are_scoped_to_the_selected_asset(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    save_asset(tmp_path, {
        "id": "123456", "name": "Demo", "asset_code": "123456",
        "tracking_code": "399001", "tracking_name": "Demo index",
    })
    transactions_path = tmp_path / "data" / "assets" / "123456" / "transactions.json"
    transactions_path.write_text(json.dumps({"123456": [
        {"id": "buy", "date": "2026-07-21", "type": "BUY", "amount": 100, "status": "ESTIMATED"},
        {"id": "sell", "date": "2026-07-22", "type": "SELL", "amount": 80, "status": "ESTIMATED"},
    ]}), encoding="utf-8")
    cache_path = asset_cache_dir(tmp_path, "123456") / "399001_daily.json"
    cache_path.write_text(json.dumps({"rows": [
        {"date": "2026-07-21", "close": 10.5},
        {"date": "2026-07-22", "close": 10.8},
    ]}), encoding="utf-8")
    client = control_server.app.test_client()

    chart = client.get("/api/transactions/chart?asset_id=123456").get_json()
    deleted = client.post("/api/transactions/delete-batch", json={"asset_id": "123456", "ids": ["buy", "sell"]}).get_json()

    assert chart["tracking_code"] == "399001"
    assert chart["history"] == [{"date": "2026-07-21", "price": 10.5}, {"date": "2026-07-22", "price": 10.8}]
    assert {record["id"] for record in chart["transactions"]} == {"buy", "sell"}
    assert deleted["success"] is True
    assert deleted["deleted_count"] == 2
    assert client.get("/api/transactions?asset_id=123456").get_json()["transactions"] == []


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
        email_delivery={"state": "FAILED"},
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
    assert "邮件发送失败，请验证" in report
    assert "portal.azure.com/#view/Microsoft_AAD_IAM/ActiveDirectoryMenuBlade/~/RegisteredApps" in report
    assert "定时：收盘后" in report
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
    assert "gap: 24px" in report_styles
    assert ".asset-heading + .report-rule-grid" in report_styles
    assert ".report-header-actions .secondary-button" in report_styles
    assert "background: #e7edf1" in report_styles


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


def test_manual_review_page_and_api_save_asset_scoped_records(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    save_asset(tmp_path, {
        "id": "review-fund", "name": "Review Fund", "asset_code": "review-fund",
        "tracking_code": "561380", "tracking_name": "Benchmark", "enabled": True,
        "strategy": {},
    })
    asset_latest_path(tmp_path, "review-fund").write_text(json.dumps({"review-fund": {
        "generated_at": "2026-07-30 10:00:00",
        "status_label": "人工复核",
        "decision": {"status": "REVIEW_BUY", "reason_code": "TEST"},
        "research": {"manual_review_items": ["检查行业信息"]},
    }}), encoding="utf-8")
    client = control_server.app.test_client()

    assert client.get("/reviews").status_code == 200
    context = client.get("/api/reviews?asset_id=review-fund").get_json()
    assert context["latest"]["status"] == "REVIEW_BUY"
    assert any(item["id"] == "research_0" for item in context["checklist"])
    saved = client.post("/api/reviews/save", json={
        "asset_id": "review-fund",
        "checks": {item["id"]: True for item in context["checklist"]},
        "conclusion": "WAIT",
        "notes": "等待更多确认",
    }).get_json()
    assert saved["success"] is True
    reviews = client.get("/api/reviews?asset_id=review-fund").get_json()["reviews"]
    assert reviews[0]["conclusion"] == "WAIT"
    assert all(reviews[0]["checks"].values())
    script = (Path(control_server.__file__).parent / "control_web" / "static" / "manual_reviews.js").read_text(encoding="utf-8")
    assert "关联报告" in script
    assert "复核项目" in script


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
    assert (tmp_path / "config" / "assets" / "grid-fund_strategy.json").exists()
    loaded = load_assets(tmp_path)[0]
    assert loaded["tracking_code"] == "159326"
    assert loaded["strategy"]["planned_capital"] == 100000
    assert asset_report_path(tmp_path, "grid-fund") == tmp_path / "output" / "assets" / "grid-fund" / "latest" / "report.html"


def test_latest_status_uses_holding_scope_and_tracking_named_cache(monkeypatch, tmp_path: Path):
    save_asset(tmp_path, {
        "id": "023639",
        "name": "Holding Fund",
        "asset_code": "023639",
        "tracking_name": "Tracking ETF",
        "tracking_code": "561380",
    })
    latest_path = asset_latest_path(tmp_path, "023639")
    latest_path.write_text(json.dumps({
        "023639": {
            "generated_at": "2026-07-20T12:00:00",
            "market_timestamp": "2026-07-20 11:30:00",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(control_server, "ROOT", tmp_path)

    asset = control_server._find_asset("561380")
    status = control_server._latest_status(asset)

    assert asset["asset_code"] == "023639"
    assert status["ok"] is True
    assert "2026-07-20T12:00:00" in status["message"]
    assert asset_cache_dir(tmp_path, "023639") / "561380_daily.json" == (
        tmp_path / "data" / "assets" / "023639" / "cache" / "561380_daily.json"
    )


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
    assert not (tmp_path / "data" / "global" / "notifications").exists()
    assert not (tmp_path / "data" / "global" / "schedule_status").exists()
    assert not (tmp_path / "output" / "portfolio").exists()
    assert not (tmp_path / "logs" / "assets").exists()
    assert not (tmp_path / "data" / "assets" / "demo" / "diagnostics").exists()
    assert not (tmp_path / "data" / "assets" / "demo" / "run_status").exists()
    assert (tmp_path / "data" / "assets" / "demo" / "notifications").is_dir()
