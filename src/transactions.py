from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


VALID_TYPES = {"BUY", "SELL"}
VALID_PRICING_MODES = {"CONFIRMED", "PENDING", "CLOSE_ESTIMATE"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_transaction(record: dict[str, Any]) -> dict[str, Any]:
    tx_type = str(record.get("type", "")).upper()
    amount = _as_float(record.get("amount"))
    price = _as_float(record.get("price"))
    shares = _as_float(record.get("shares"))

    # Legacy records only contained amount + price.
    if shares <= 0 and amount > 0 and price > 0:
        shares = amount / price

    pricing_mode = str(
        record.get("pricing_mode")
        or (
            "CONFIRMED"
            if price > 0 and shares > 0
            else "PENDING"
        )
    ).upper()
    if pricing_mode not in VALID_PRICING_MODES:
        pricing_mode = "CONFIRMED" if price > 0 else "PENDING"

    status = str(record.get("status") or "").upper()
    if not status:
        if pricing_mode == "PENDING":
            status = "PENDING"
        elif pricing_mode == "CLOSE_ESTIMATE" or record.get("estimated"):
            status = "ESTIMATED"
        else:
            status = "CONFIRMED"

    price_source = str(
        record.get("price_source")
        or (
            "legacy_manual"
            if price > 0 and "pricing_mode" not in record
            else (
                "confirmed_amount_div_shares"
                if status == "CONFIRMED"
                else (
                    "close_estimate"
                    if status == "ESTIMATED"
                    else "pending"
                )
            )
        )
    )

    normalized = {
        "id": str(record.get("id") or uuid.uuid4()),
        "date": str(record.get("date", "")),
        "type": tx_type,
        "amount": round(amount, 8),
        "shares": round(shares, 8),
        "price": round(price, 8),
        "pricing_mode": pricing_mode,
        "status": status,
        "price_source": price_source,
        "estimated": bool(
            record.get("estimated")
            or status == "ESTIMATED"
        ),
        "note": str(record.get("note", "")),
        "created_at": str(
            record.get("created_at")
            or datetime.now().isoformat(timespec="seconds")
        ),
        "updated_at": str(
            record.get("updated_at")
            or datetime.now().isoformat(timespec="seconds")
        ),
    }
    return normalized


def load_transactions(path: Path, symbol: str) -> list[dict[str, Any]]:
    doc = _read_json(path, {})
    raw = doc.get(str(symbol), [])
    normalized = [
        normalize_transaction(item)
        for item in raw
        if isinstance(item, dict)
    ]
    normalized.sort(
        key=lambda item: (
            item.get("date", ""),
            item.get("created_at", ""),
        )
    )

    # Persist migration only when the normalized form differs.
    if normalized != raw:
        doc[str(symbol)] = normalized
        _atomic_write_json(path, doc)

    return normalized



def save_transaction(
    path: Path,
    symbol: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    doc = _read_json(path, {})
    records = [
        normalize_transaction(item)
        for item in doc.get(str(symbol), [])
        if isinstance(item, dict)
    ]

    tx_id = str(payload.get("id") or uuid.uuid4())
    tx_type = str(payload.get("type", "")).upper()
    if tx_type not in VALID_TYPES:
        raise ValueError("交易类型必须是买入或卖出")

    date = str(payload.get("date", "")).strip()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("交易日期必须使用YYYY-MM-DD格式") from exc

    pricing_mode = str(
        payload.get("pricing_mode", "CLOSE_ESTIMATE")
    ).upper()
    if pricing_mode not in {"PENDING", "CLOSE_ESTIMATE"}:
        pricing_mode = "CLOSE_ESTIMATE"

    amount = _as_float(payload.get("amount"))
    if amount <= 0:
        raise ValueError("交易金额必须大于0")

    existing = next(
        (item for item in records if item.get("id") == tx_id),
        None,
    )
    now_text = datetime.now().isoformat(timespec="seconds")

    record = {
        "id": tx_id,
        "date": date,
        "type": tx_type,
        "amount": round(amount, 8),
        "shares": 0.0,
        "price": 0.0,
        "pricing_mode": pricing_mode,
        "status": (
            "PENDING"
            if pricing_mode == "PENDING"
            else "PENDING_CLOSE"
        ),
        "price_source": (
            "pending"
            if pricing_mode == "PENDING"
            else "waiting_close"
        ),
        "estimated": pricing_mode == "CLOSE_ESTIMATE",
        "note": str(payload.get("note", "")).strip(),
        "created_at": (
            existing.get("created_at", now_text)
            if existing
            else now_text
        ),
        "updated_at": now_text,
    }

    if existing:
        records = [
            record if item.get("id") == tx_id else item
            for item in records
        ]
    else:
        records.append(record)

    records.sort(
        key=lambda item: (
            item.get("date", ""),
            item.get("created_at", ""),
        )
    )
    doc[str(symbol)] = records
    _atomic_write_json(path, doc)
    return record

def delete_transaction(
    path: Path,
    symbol: str,
    transaction_id: str,
) -> bool:
    doc = _read_json(path, {})
    records = doc.get(str(symbol), [])
    remaining = [
        item for item in records
        if str(item.get("id")) != str(transaction_id)
    ]
    if len(remaining) == len(records):
        return False
    doc[str(symbol)] = remaining
    _atomic_write_json(path, doc)
    return True


def delete_transactions(
    path: Path,
    symbol: str,
    transaction_ids: list[str] | set[str] | tuple[str, ...],
) -> int:
    """Delete selected transactions together, with one atomic file write."""
    ids = {str(transaction_id) for transaction_id in transaction_ids if str(transaction_id)}
    if not ids:
        return 0

    doc = _read_json(path, {})
    records = doc.get(str(symbol), [])
    remaining = [item for item in records if str(item.get("id")) not in ids]
    deleted_count = len(records) - len(remaining)
    if not deleted_count:
        return 0

    doc[str(symbol)] = remaining
    _atomic_write_json(path, doc)
    return deleted_count


def _close_for_date(
    history: pd.DataFrame,
    date_text: str,
) -> float | None:
    if history is None or history.empty:
        return None
    target = pd.Timestamp(date_text).normalize()
    dates = pd.to_datetime(history["date"], errors="coerce").dt.normalize()
    matches = history.loc[dates == target]
    if matches.empty:
        return None
    value = pd.to_numeric(
        matches.iloc[-1].get("close"),
        errors="coerce",
    )
    if pd.isna(value) or float(value) <= 0:
        return None
    return float(value)


def reconcile_pending_transactions(
    path: Path,
    symbol: str,
    history: pd.DataFrame,
) -> list[dict[str, Any]]:
    doc = _read_json(path, {})
    records = [
        normalize_transaction(item)
        for item in doc.get(str(symbol), [])
        if isinstance(item, dict)
    ]
    changed = False

    for record in records:
        if record.get("pricing_mode") != "CLOSE_ESTIMATE":
            continue
        if record.get("status") not in {
            "PENDING_CLOSE",
            "PENDING",
        }:
            continue

        close_price = _close_for_date(
            history,
            str(record.get("date", "")),
        )
        if close_price is None:
            continue

        amount = _as_float(record.get("amount"))
        shares = _as_float(record.get("shares"))
        shares = amount / close_price if amount > 0 else 0

        record.update({
            "amount": round(amount, 8),
            "shares": round(shares, 8),
            "price": round(close_price, 8),
            "status": "ESTIMATED",
            "price_source": "daily_close_estimate",
            "estimated": True,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        changed = True

    if changed:
        records.sort(
            key=lambda item: (
                item.get("date", ""),
                item.get("created_at", ""),
            )
        )
        doc[str(symbol)] = records
        _atomic_write_json(path, doc)

    return records
