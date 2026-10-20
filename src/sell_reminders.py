"""Persistent, cycle-scoped guardrails for PARTIAL_SELL emails."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _path(root: Path, asset_id: str) -> Path:
    return root / "data" / "assets" / str(asset_id) / "notifications" / "sell_reminder_state.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def evaluate_sell_reminder(
    root: Path, asset_id: str, result: dict[str, Any], now: datetime | None = None,
) -> dict[str, Any]:
    """Return whether a sell notification may be sent and persist cycle state."""
    now = now or datetime.now()
    decision = result.get("decision", {})
    if decision.get("status") != "PARTIAL_SELL":
        return {"allowed": True, "reason": "not a sell reminder", "state": {}}
    cycle = (result.get("indicators", {}).get("cycle_analysis") or {})
    cycle_id = cycle.get("current_cycle_id")
    if not cycle_id:
        return {"allowed": False, "reason": "no confirmed market cycle", "state": {}}
    tier = 2 if decision.get("reason_code") == "EXIT_REVIEW" else 1
    shares = float(result.get("position_metrics", {}).get("shares", 0) or 0)
    if shares <= 0:
        return {"allowed": False, "reason": "no remaining shares", "state": {}}

    state_path = _path(root, asset_id)
    state = _read(state_path)
    if state.get("cycle_id") != cycle_id:
        state = {
            "cycle_id": cycle_id,
            "created_at": now.isoformat(timespec="seconds"),
            "reference_shares": shares,
            "first": {"dates": [], "stopped": False},
            "second": {"dates": [], "residual_dates": [], "stopped": False},
            "manual_stop": None,
        }
    state.setdefault("reference_shares", shares)
    state.setdefault("first", {"dates": [], "stopped": False})
    state.setdefault("second", {"dates": [], "residual_dates": [], "stopped": False})
    day = now.strftime("%Y-%m-%d")
    reference = max(float(state["reference_shares"] or shares), 1e-12)
    reduced_pct = max((reference - shares) / reference * 100, 0.0)
    remaining_pct = shares / reference * 100
    strategy = result.get("asset", {}).get("strategy", {})

    if state.get("manual_stop"):
        _write(state_path, state)
        return {"allowed": False, "reason": "manually stopped for this cycle", "state": state}
    bucket = state["first"] if tier == 1 else state["second"]
    if bucket.get("stopped"):
        _write(state_path, state)
        return {"allowed": False, "reason": "tier reminder already completed", "state": state}
    # The first-tier schedule is intentionally not deduplicated: each enabled
    # scheduled run may notify while its 50% reduction target remains unmet.
    # Residual second-tier reminders retain daily deduplication.
    if tier == 2 and day in bucket.get("dates", []):
        _write(state_path, state)
        return {"allowed": False, "reason": "already reminded today", "state": state}

    if tier == 1:
        if reduced_pct >= float(strategy.get("sell_first_stop_reduction_pct", 50.0)):
            bucket["stopped"] = True
            _write(state_path, state)
            return {"allowed": False, "reason": "reduction target reached", "state": state}
    else:
        if remaining_pct <= float(strategy.get("sell_residual_position_pct", 10.0)):
            residual_dates = bucket.setdefault("residual_dates", [])
            if len(residual_dates) >= int(strategy.get("sell_residual_max_reminder_days", 5)):
                bucket["stopped"] = True
                _write(state_path, state)
                return {"allowed": False, "reason": "residual-position reminder day limit reached", "state": state}

    state["current_shares"] = shares
    state["reduced_pct"] = round(reduced_pct, 4)
    state["remaining_pct"] = round(remaining_pct, 4)
    _write(state_path, state)
    return {
        "allowed": True,
        "reason": "sell reminder allowed",
        "state": state,
        "tier": tier,
        "residual_position_pct": float(strategy.get("sell_residual_position_pct", 10.0)),
    }


def record_sell_reminder_sent(
    root: Path,
    asset_id: str,
    state: dict[str, Any],
    tier: int,
    now: datetime | None = None,
    residual_position_pct: float = 10.0,
) -> None:
    now = now or datetime.now()
    bucket = state["first"] if tier == 1 else state["second"]
    day = now.strftime("%Y-%m-%d")
    if day not in bucket["dates"]:
        bucket["dates"].append(day)
    if tier == 2 and state.get("remaining_pct", 100.0) <= residual_position_pct and day not in bucket.setdefault("residual_dates", []):
        bucket["residual_dates"].append(day)
    _write(_path(root, asset_id), state)


def stop_sell_reminders(root: Path, asset_id: str, reason: str = "") -> dict[str, Any]:
    path = _path(root, asset_id)
    state = _read(path)
    if not state.get("cycle_id"):
        raise ValueError("当前没有可停止的减仓提醒周期")
    state["manual_stop"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason or "用户保留剩余仓位",
        "remaining_shares": state.get("current_shares", 0),
    }
    _write(path, state)
    return state
