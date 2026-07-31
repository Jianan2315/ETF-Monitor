from __future__ import annotations

from types import SimpleNamespace

import control_server
from src.microsoft_graph_auth import GraphToken, MicrosoftGraphAuthError


def _enabled_config():
    return {"email": {"enabled": True}}


def test_email_token_status_reports_valid_and_reauthorization_required(monkeypatch):
    monkeypatch.setattr(control_server, "_EMAIL_AUTH_STATE", {"state": "idle"})
    monkeypatch.setattr(control_server, "load_config", lambda _path: _enabled_config())
    monkeypatch.setattr(control_server, "validate_graph_environment", lambda _email: {"client_id": "id"})
    monkeypatch.setattr(
        control_server,
        "acquire_graph_access_token",
        lambda *_args, **_kwargs: GraphToken("token", "user@example.com", True),
    )
    client = control_server.app.test_client()

    valid = client.get("/api/email/token-status").get_json()
    assert valid["valid"] is True
    assert valid["state"] == "valid"

    def no_silent_token(*_args, **_kwargs):
        raise MicrosoftGraphAuthError("Microsoft authorization requires a visible device login.")

    monkeypatch.setattr(control_server, "acquire_graph_access_token", no_silent_token)
    required = client.get("/api/email/token-status").get_json()
    assert required["valid"] is False
    assert required["state"] == "reauthorization_required"
    assert required["requires_reauthorization"] is True


def test_email_authorization_start_returns_device_code_without_blocking(monkeypatch):
    monkeypatch.setattr(control_server, "_EMAIL_AUTH_STATE", {"state": "idle"})
    monkeypatch.setattr(control_server, "load_config", lambda _path: _enabled_config())
    monkeypatch.setattr(control_server, "validate_graph_environment", lambda _email: {"client_id": "id"})
    monkeypatch.setattr(
        control_server,
        "start_graph_device_authorization",
        lambda *_args, **_kwargs: SimpleNamespace(
            verification_uri="https://microsoft.com/devicelogin",
            user_code="ABCD-EFGH",
        ),
    )

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            pass

    monkeypatch.setattr(control_server.threading, "Thread", FakeThread)
    response = control_server.app.test_client().post("/api/email/authorize/start")
    body = response.get_json()

    assert response.status_code == 200
    assert body["authorization"]["state"] == "pending"
    assert body["authorization"]["verification_uri"] == "https://microsoft.com/devicelogin"
    assert body["authorization"]["user_code"] == "ABCD-EFGH"


def test_homepage_exposes_email_token_check_and_reauthorization_modal():
    body = control_server.app.test_client().get("/").get_data(as_text=True)
    script = (control_server.ROOT / "control_web" / "static" / "control.js").read_text(encoding="utf-8")

    assert 'id="check-email-token"' in body
    assert 'id="email-authorization-modal"' in body
    assert '"/api/email/token-status"' in script
    assert '"/api/email/authorize/start"' in script
