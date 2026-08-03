"""Strategy status validation helpers."""
from __future__ import annotations

VALID_STATUSES = {"OBSERVE", "REVIEW_BUY", "REVIEW_SELL", "PARTIAL_SELL"}


def normalize_status(value: object) -> str:
    status = str(value or "OBSERVE").upper()
    return status if status in VALID_STATUSES else "OBSERVE"
