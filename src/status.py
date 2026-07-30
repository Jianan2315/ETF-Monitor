"""Strategy status migration and presentation helpers."""
from __future__ import annotations

LEGACY_OBSERVE_STATUSES = {"WAIT", "WATCH", "HOLD"}
VALID_STATUSES = {"OBSERVE", "REVIEW_BUY", "REVIEW_SELL", "PARTIAL_SELL"}


def normalize_status(value: object) -> str:
    status = str(value or "OBSERVE").upper()
    if status in LEGACY_OBSERVE_STATUSES:
        return "OBSERVE"
    return status if status in VALID_STATUSES else "OBSERVE"


def migrate_email_triggers(triggers: object) -> dict[str, bool]:
    raw = triggers if isinstance(triggers, dict) else {}
    observe = raw.get("OBSERVE")
    if observe is None:
        observe = any(bool(raw.get(status, False)) for status in LEGACY_OBSERVE_STATUSES)
    return {
        "OBSERVE": bool(observe),
        "REVIEW_BUY": bool(raw.get("REVIEW_BUY", False)),
        "REVIEW_SELL": bool(raw.get("REVIEW_SELL", False)),
        "PARTIAL_SELL": bool(raw.get("PARTIAL_SELL", False)),
    }
