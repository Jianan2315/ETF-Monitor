from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import control_server
from src.asset_store import asset_cache_dir, asset_transactions_path, load_assets, save_asset
from src.strategy_replay import replay_strategy


def _history() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=30)
    closes = [10.0] * 29 + [12.0]
    return pd.DataFrame({"date": dates, "close": closes})


def _asset() -> dict:
    return {
        "id": "demo", "name": "Demo", "asset_code": "demo", "tracking_code": "999999",
        "tracking_name": "Demo index", "strategy": {"strategy_type": "CYCLICAL"},
    }


def test_strategy_replay_uses_cash_and_initial_position_without_creating_real_trades():
    result = replay_strategy(
        _asset(),
        _history(),
        [{"date": "2026-01-02", "type": "BUY", "amount": 1000, "price": 10, "shares": 100, "status": "SETTLED"}],
        {
            "start_date": "2026-01-02", "initial_capital": 1000, "initial_buy_amount": 400,
            "review_buy_amount": 100, "deep_drawdown_buy_amount": 100,
            "first_sell_pct": 25, "second_sell_pct": 100,
        },
    )

    assert result["strategy"]["net_asset_value"] == 1080.0
    assert result["strategy"]["return_pct"] == 8.0
    assert result["events"][0]["reason_code"] == "INITIAL_POSITION"
    assert result["actual"]["return_pct"] == 20.0


def test_strategy_replay_api_saves_asset_scoped_parameters_without_loading_them_as_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    asset = save_asset(tmp_path, _asset())
    history = _history()
    cache = asset_cache_dir(tmp_path, "demo") / "999999_daily.json"
    cache.write_text(json.dumps({"rows": [
        {"date": row.date.strftime("%Y-%m-%d"), "close": row.close}
        for row in history.itertuples()
    ]}), encoding="utf-8")
    asset_transactions_path(tmp_path, "demo").write_text(json.dumps({"demo": [
        {"date": "2026-01-02", "type": "BUY", "amount": 1000, "price": 10, "shares": 100, "status": "SETTLED"},
    ]}), encoding="utf-8")
    client = control_server.app.test_client()

    response = client.get("/api/strategy-replay?asset_id=demo")
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert client.get("/strategy-replay").status_code == 200
    page = client.get("/strategy-replay").get_data(as_text=True)
    assert '<form class="form-grid" id="replay-settings">' in page
    assert 'class="replay-comparison-stack"' in page
    assert page.count('class="status-item replay-comparison-item"') == 2
    assert 'id="strategy-details"' in page
    assert 'id="actual-details"' in page
    script = Path(control_server.__file__).parent / "web" / "control" / "static" / "strategy_replay.js"
    assert "settingsForm.elements" in script.read_text(encoding="utf-8")
    assert 'getElementById("strategy-details")' in script.read_text(encoding="utf-8")

    saved = client.post("/api/strategy-replay/settings?asset_id=demo", json={
        "settings": {"start_date": "2026-01-02", "initial_capital": 2000, "initial_buy_amount": 500},
    }).get_json()
    assert saved["success"] is True
    assert saved["settings"]["initial_capital"] == 2000.0
    assert (tmp_path / "config" / "assets" / "demo_simulation.json").exists()
    assert [item["id"] for item in load_assets(tmp_path)] == [asset["id"]]
