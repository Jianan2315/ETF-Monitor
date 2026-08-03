from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


DATASETS = {
    "history": {
        "label": "指标历史",
        "filename": "history.xlsx",
        "sheet": "history",
        "columns": [
            "generated_at", "symbol", "tracking_code", "name", "price",
            "daily_change_pct", "five_day_change_pct", "ten_day_change_pct",
            "twenty_day_change_pct", "one_month_drawdown_pct",
            "three_month_drawdown_pct", "six_month_drawdown_pct",
            "drawdown_from_last_buy_pct", "consecutive_down_days",
            "consecutive_up_days", "rebound_pct", "is_new_10d_low",
            "stable_days", "cycle_sample_size", "current_drawdown_pct",
            "current_drawdown_percentile", "current_rise_pct",
            "current_rise_percentile", "recovery_pct", "fund_flow_status",
            "invested_amount", "average_cost", "profit_pct", "profit_amount",
            "data_source",
        ],
    },
    "decisions": {
        "label": "决策历史",
        "filename": "decisions.xlsx",
        "sheet": "decisions",
        "columns": [
            "generated_at", "symbol", "tracking_code", "name", "status",
            "action_type", "reason_code", "suggested_amount", "reasons",
            "should_notify",
        ],
    },
}


def _dataset(name: object) -> dict[str, Any]:
    dataset = DATASETS.get(str(name or "").strip().lower())
    if not dataset:
        raise ValueError("数据类型只能是 history 或 decisions。")
    return dataset


def _path(root: Path, asset_id: str, dataset: dict[str, Any]) -> Path:
    return root / "data" / "assets" / str(asset_id) / str(dataset["filename"])


def _value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _value(value.item())
        except ValueError:
            pass
    return value


def _read(path: Path, dataset: dict[str, Any]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=list(dataset["columns"]))
    try:
        frame = pd.read_excel(path, sheet_name=str(dataset["sheet"]))
    except Exception as exc:
        raise ValueError(f"无法读取运行数据文件：{exc}") from exc
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(columns=list(dataset["columns"]))


def list_run_records(root: Path, asset_id: str, dataset_name: object) -> dict[str, Any]:
    dataset = _dataset(dataset_name)
    path = _path(root, asset_id, dataset)
    frame = _read(path, dataset)
    columns = [str(column) for column in frame.columns] or list(dataset["columns"])
    records = []
    for index, row in frame.iterrows():
        records.append({
            "row_index": int(index),
            "values": {column: _value(row.get(column)) for column in columns},
        })
    return {
        "success": True,
        "dataset": str(dataset_name).lower(),
        "label": dataset["label"],
        "columns": columns,
        "records": records,
        "file_exists": path.exists(),
    }


def _write(path: Path, dataset: dict[str, Any], frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.stem}.tmp{path.suffix}")
    with pd.ExcelWriter(temp_path, engine="openpyxl", mode="w") as writer:
        frame.to_excel(writer, index=False, sheet_name=str(dataset["sheet"]))
    temp_path.replace(path)


def save_run_record(
    root: Path,
    asset_id: str,
    dataset_name: object,
    values: object,
    row_index: object = None,
) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("记录内容必须是对象。")
    dataset = _dataset(dataset_name)
    path = _path(root, asset_id, dataset)
    frame = _read(path, dataset)
    columns = [str(column) for column in frame.columns] or list(dataset["columns"])
    cleaned = {column: values.get(column, None) for column in columns}

    if row_index in (None, ""):
        frame = pd.concat([frame, pd.DataFrame([cleaned])], ignore_index=True)
        message = "已新增记录。"
    else:
        try:
            index = int(row_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("记录序号无效。") from exc
        if index < 0 or index >= len(frame):
            raise ValueError("要编辑的记录不存在。")
        for column, value in cleaned.items():
            frame.at[index, column] = value
        message = "已更新记录。"

    _write(path, dataset, frame)
    return {"success": True, "message": message, **list_run_records(root, asset_id, dataset_name)}


def delete_run_records(
    root: Path,
    asset_id: str,
    dataset_name: object,
    row_indices: object,
) -> dict[str, Any]:
    if not isinstance(row_indices, list):
        raise ValueError("要删除的记录必须是列表。")
    dataset = _dataset(dataset_name)
    path = _path(root, asset_id, dataset)
    frame = _read(path, dataset)
    try:
        indices = sorted({int(value) for value in row_indices})
    except (TypeError, ValueError) as exc:
        raise ValueError("记录序号无效。") from exc
    valid = [index for index in indices if 0 <= index < len(frame)]
    if not valid:
        raise ValueError("没有找到要删除的记录。")

    _write(path, dataset, frame.drop(index=valid).reset_index(drop=True))
    return {
        "success": True,
        "message": f"已删除 {len(valid)} 条记录。",
        **list_run_records(root, asset_id, dataset_name),
    }
