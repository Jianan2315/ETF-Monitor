from datetime import datetime, timedelta

from src.notification_guard import (
    data_error_email_decision,
    record_data_error_email_sent,
)


def test_same_data_error_is_deduplicated(tmp_path):
    config = {
        "email": {
            "data_error_dedup_minutes": 15,
            "data_error_state_file": "data/notifications/state.json",
        }
    }
    asset = {"symbol": "561380"}
    errors = ["historical attempt 1: ConnectionError"]
    now = datetime(2026, 7, 15, 12, 20)

    allowed, _, fingerprint = data_error_email_decision(
        project_root=tmp_path,
        config=config,
        asset=asset,
        errors=errors,
        last_available_date="2026-07-14",
        now=now,
    )
    assert allowed is True

    record_data_error_email_sent(
        project_root=tmp_path,
        config=config,
        fingerprint=fingerprint,
        now=now,
    )

    allowed, reason, _ = data_error_email_decision(
        project_root=tmp_path,
        config=config,
        asset=asset,
        errors=errors,
        last_available_date="2026-07-14",
        now=now + timedelta(minutes=5),
    )
    assert allowed is False
    assert "去重" in reason
