import json
from pathlib import Path


def test_force_send_is_off_by_default():
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "config" / "email.json").read_text(encoding="utf-8")
    )
    debug = payload["email"]["debug"]
    assert debug["enabled"] is False
    assert debug["force_send_email"] is False
