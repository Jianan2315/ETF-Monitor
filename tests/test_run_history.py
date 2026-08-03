from pathlib import Path

import pandas as pd

import control_server
from src.asset_store import save_asset
from src.run_history import delete_run_records, list_run_records, save_run_record


def test_run_history_records_can_be_created_edited_and_deleted(tmp_path: Path):
    created = save_run_record(
        tmp_path,
        "demo",
        "decisions",
        {
            "generated_at": "2026-08-04T14:45:00",
            "symbol": "demo",
            "status": "OBSERVE",
            "reasons": "test",
        },
    )
    assert created["records"][0]["values"]["status"] == "OBSERVE"

    edited = save_run_record(
        tmp_path,
        "demo",
        "decisions",
        {
            "generated_at": "2026-08-04T14:45:00",
            "symbol": "demo",
            "status": "REVIEW_BUY",
            "reasons": "edited",
        },
        row_index=0,
    )
    assert edited["records"][0]["values"]["status"] == "REVIEW_BUY"

    deleted = delete_run_records(tmp_path, "demo", "decisions", [0])
    assert deleted["records"] == []


def test_existing_history_xlsx_is_exposed_without_changing_column_order(tmp_path: Path):
    path = tmp_path / "data" / "assets" / "demo" / "history.xlsx"
    path.parent.mkdir(parents=True)
    pd.DataFrame([{"generated_at": "2026-08-04", "price": 12.3}]).to_excel(
        path,
        index=False,
        sheet_name="history",
    )

    payload = list_run_records(tmp_path, "demo", "history")

    assert payload["columns"] == ["generated_at", "price"]
    assert payload["records"] == [{
        "row_index": 0,
        "values": {"generated_at": "2026-08-04", "price": 12.3},
    }]


def test_run_history_api_is_asset_scoped_and_page_is_available(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(control_server, "ROOT", tmp_path)
    save_asset(tmp_path, {
        "name": "Demo",
        "asset_code": "demo",
        "tracking_name": "Demo Index",
        "tracking_code": "999999",
    })
    client = control_server.app.test_client()

    created = client.post(
        "/api/run-history/save?asset_id=demo",
        json={
            "dataset": "history",
            "values": {"generated_at": "2026-08-04", "price": "12.3"},
        },
    ).get_json()
    fetched = client.get("/api/run-history?asset_id=demo&dataset=history").get_json()

    assert created["success"] is True
    assert fetched["records"][0]["values"]["price"] == 12.3
    assert client.get("/run-history").status_code == 200
    page = client.get("/asset/manage").get_data(as_text=True)
    assert 'id="asset-run-history-link"' in page
