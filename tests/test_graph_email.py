from __future__ import annotations

import pytest

from src.email_sender import build_graph_message_payload
from src.microsoft_graph_auth import (
    MicrosoftGraphAuthError,
    validate_graph_environment,
)


def sample_result() -> dict:
    return {
        "asset": {
            "name": "电网设备ETF",
            "symbol": "561380",
            "strategy": {"name": "Staged Grid", "version": "1.1"},
            "strategy": {"planned_capital": 100000},
        },
        "decision": {
            "status": "WAIT",
            "suggested_amount": 0,
            "reasons": ["仍处于连续下跌阶段"],
        },
        "indicators": {
            "daily_change_pct": -1.2,
            "five_day_change_pct": -5.0,
            "ten_day_change_pct": -9.0,
            "twenty_day_change_pct": -8.5,
            "drawdown_from_last_buy_pct": -3.2,
        },
        "position_metrics": {
            "profit_pct": -3.2,
            "profit_amount": -320.0,
            "invested_amount": 10000.0,
        },
        "data_source": "Mock data",
        "generated_at": "2026-07-13T10:00:00",
        "llm_summary": None,
    }


def graph_config() -> dict:
    return {
        "recipient": "one@example.com, two@example.com",
        "subject_prefix": "ETF Monitor",
        "html": True,
        "include_raw_metrics": True,
        "graph": {"save_to_sent_items": True},
    }


def test_graph_payload_supports_multiple_recipients():
    payload = build_graph_message_payload(sample_result(), graph_config(), forced=True)

    assert payload["saveToSentItems"] is True
    assert payload["message"]["body"]["contentType"] == "HTML"
    assert payload["message"]["subject"].find("[TEST]") >= 0
    assert [
        item["emailAddress"]["address"]
        for item in payload["message"]["toRecipients"]
    ] == ["one@example.com", "two@example.com"]


def test_graph_environment_reads_client_id(monkeypatch):
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "client-id-123")
    settings = validate_graph_environment(
        {
            "recipient": "one@example.com",
            "expected_sender": "sender@outlook.com",
            "graph": {
                "authority": "https://login.microsoftonline.com/consumers",
                "scopes": ["Mail.Send"],
                "endpoint": "https://graph.microsoft.com/v1.0/me/sendMail",
                "token_cache_file": "config/private/cache.json",
                "timeout_seconds": 20,
                "save_to_sent_items": True,
            },
        }
    )
    assert settings["client_id"] == "client-id-123"
    assert settings["expected_sender"] == "sender@outlook.com"
    assert settings["timeout_seconds"] == 20


def test_graph_environment_requires_client_id(monkeypatch):
    monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
    with pytest.raises(MicrosoftGraphAuthError):
        validate_graph_environment(
            {
                "recipient": "one@example.com",
                "graph": {
                    "authority": "https://login.microsoftonline.com/consumers",
                    "scopes": ["Mail.Send"],
                    "endpoint": "https://graph.microsoft.com/v1.0/me/sendMail",
                    "token_cache_file": "config/private/cache.json",
                },
            }
        )


def test_send_notification_email_calls_graph(monkeypatch, tmp_path):
    import logging

    import src.email_sender as email_sender
    from src.microsoft_graph_auth import GraphToken

    captured = {}

    monkeypatch.setattr(
        email_sender,
        "validate_graph_environment",
        lambda _config: {
            "recipient": "one@example.com",
            "expected_sender": None,
            "authority": "https://login.microsoftonline.com/consumers",
            "scopes": ["Mail.Send"],
            "endpoint": "https://graph.microsoft.com/v1.0/me/sendMail",
            "cache_file": "config/private/cache.json",
            "timeout_seconds": 30,
            "save_to_sent_items": True,
            "client_id": "client-id",
        },
    )
    monkeypatch.setattr(
        email_sender,
        "acquire_graph_access_token",
        lambda settings, root, logger: GraphToken(
            access_token="token-value",
            account_username="sender@outlook.com",
            acquired_silently=True,
        ),
    )

    class FakeResponse:
        status_code = 202
        headers = {"request-id": "request-123"}
        text = ""

        def json(self):
            return {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(email_sender.requests, "post", fake_post)

    config = {
        "email": {
            **graph_config(),
            "provider": "microsoft_graph",
            "graph": {
                "save_to_sent_items": True,
            },
        }
    }
    logger = logging.getLogger("test_graph_email")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())

    email_sender.send_notification_email(
        sample_result(),
        config,
        project_root=tmp_path,
        forced=False,
        logger=logger,
    )

    assert captured["url"].endswith("/me/sendMail")
    assert captured["headers"]["Authorization"] == "Bearer token-value"
    assert captured["json"]["message"]["toRecipients"][0]["emailAddress"]["address"] == "one@example.com"
