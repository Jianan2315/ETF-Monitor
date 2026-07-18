import pandas as pd

from src.transactions import (
    delete_transaction,
    load_transactions,
    reconcile_pending_transactions,
    save_transaction,
)


def test_close_estimate_transaction_requires_only_amount(tmp_path):
    path = tmp_path / "transactions.json"
    record = save_transaction(
        path,
        "561380",
        {
            "type": "BUY",
            "date": "2026-07-16",
            "pricing_mode": "CLOSE_ESTIMATE",
            "amount": 5000,
            "note": "test",
        },
    )
    assert record["price"] == 0
    assert record["shares"] == 0
    assert record["status"] == "PENDING_CLOSE"


def test_pending_close_is_reconciled_from_history(tmp_path):
    path = tmp_path / "transactions.json"
    record = save_transaction(
        path,
        "561380",
        {
            "type": "BUY",
            "date": "2026-07-16",
            "pricing_mode": "CLOSE_ESTIMATE",
            "amount": 5000,
        },
    )
    assert record["status"] == "PENDING_CLOSE"

    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-07-16"]),
        "close": [0.8],
    })
    records = reconcile_pending_transactions(
        path,
        "561380",
        history,
    )
    assert records[0]["status"] == "ESTIMATED"
    assert records[0]["price"] == 0.8
    assert records[0]["shares"] == 6250


def test_sell_amount_is_converted_to_shares_from_close(tmp_path):
    path = tmp_path / "transactions.json"
    save_transaction(
        path,
        "561380",
        {
            "type": "SELL",
            "date": "2026-07-16",
            "pricing_mode": "CLOSE_ESTIMATE",
            "amount": 4000,
        },
    )
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-07-16"]),
        "close": [0.8],
    })
    records = reconcile_pending_transactions(
        path,
        "561380",
        history,
    )
    assert records[0]["shares"] == 5000
    assert records[0]["price"] == 0.8


def test_transaction_update_and_delete(tmp_path):
    path = tmp_path / "transactions.json"
    original = save_transaction(
        path,
        "561380",
        {
            "type": "BUY",
            "date": "2026-07-16",
            "pricing_mode": "PENDING",
            "amount": 5000,
        },
    )
    updated = save_transaction(
        path,
        "561380",
        {
            "id": original["id"],
            "type": "BUY",
            "date": "2026-07-16",
            "pricing_mode": "CLOSE_ESTIMATE",
            "amount": 5000,
        },
    )
    assert updated["id"] == original["id"]
    assert updated["status"] == "PENDING_CLOSE"
    assert len(load_transactions(path, "561380")) == 1

    assert delete_transaction(
        path,
        "561380",
        original["id"],
    ) is True
    assert load_transactions(path, "561380") == []
