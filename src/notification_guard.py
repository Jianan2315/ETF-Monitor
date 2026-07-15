from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path


def _state_path(project_root: Path, config: dict) -> Path:
    relative = config.get("email", {}).get(
        "data_error_state_file",
        "data/notifications/data_error_state.json",
    )
    return project_root / relative


def _categories(errors: list[str]) -> list[str]:
    categories: set[str] = set()
    for item in errors:
        lowered = item.lower()
        if "historical" in lowered:
            categories.add("historical")
        elif "realtime" in lowered:
            categories.add("realtime")
        elif "intraday" in lowered:
            categories.add("intraday")
        else:
            categories.add("other")
    return sorted(categories)


def build_data_error_fingerprint(
    asset: dict,
    errors: list[str],
    last_available_date: str | None,
) -> str:
    raw = "|".join(
        [
            str(asset.get("symbol", "")),
            ",".join(_categories(errors)),
            str(last_available_date or "none"),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def data_error_email_decision(
    *,
    project_root: Path,
    config: dict,
    asset: dict,
    errors: list[str],
    last_available_date: str | None,
    now: datetime,
) -> tuple[bool, str, str]:
    minutes = int(config.get("email", {}).get("data_error_dedup_minutes", 15))
    fingerprint = build_data_error_fingerprint(
        asset,
        errors,
        last_available_date,
    )
    path = _state_path(project_root, config)

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    record = state.get(fingerprint)
    if record:
        try:
            sent_at = datetime.fromisoformat(record["sent_at"])
            if now - sent_at < timedelta(minutes=minutes):
                remaining = timedelta(minutes=minutes) - (now - sent_at)
                seconds = max(0, int(remaining.total_seconds()))
                return (
                    False,
                    f"相同DATA_ERROR已在去重窗口内发送，剩余约{seconds // 60 + 1}分钟。",
                    fingerprint,
                )
        except Exception:
            pass

    return True, "允许发送DATA_ERROR邮件。", fingerprint


def record_data_error_email_sent(
    *,
    project_root: Path,
    config: dict,
    fingerprint: str,
    now: datetime,
) -> None:
    path = _state_path(project_root, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    state[fingerprint] = {"sent_at": now.isoformat(timespec="seconds")}

    # Keep the file small by retaining only the newest 100 records.
    ordered = sorted(
        state.items(),
        key=lambda item: item[1].get("sent_at", ""),
        reverse=True,
    )[:100]
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(dict(ordered), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)
