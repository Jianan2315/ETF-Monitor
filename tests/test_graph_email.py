from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

import pytest

from src.email_sender import _compact_error_for_email, build_graph_message_payload
from src.microsoft_graph_auth import (
    MicrosoftGraphAuthError,
    acquire_graph_access_token,
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
    result = sample_result()
    result["llm_summary"] = "测试摘要"
    result["data_status"] = {"history": {}, "realtime": {}, "intraday": {}}
    payload = build_graph_message_payload(result, graph_config(), forced=True)
    body = payload["message"]["body"]["content"]

    assert payload["saveToSentItems"] is True
    assert payload["message"]["body"]["contentType"] == "HTML"
    assert payload["message"]["subject"].find("[TEST]") >= 0
    assert "OBSERVE" in body
    assert body.index("电网设备ETF") < body.index("模型摘要") < body.index("判断原因")
    assert body.index("判断原因") < body.index("数据源") < body.index("数据状态")
    assert [
        item["emailAddress"]["address"]
        for item in payload["message"]["toRecipients"]
    ] == ["one@example.com", "two@example.com"]


def test_graph_payload_includes_only_successful_news_summary_for_review():
    result = sample_result()
    result["data_status"] = {"history": {}, "realtime": {}, "intraday": {}}
    result["news"] = {
        "status": "SUCCESS",
        "generated_at": "2026-08-15T14:45:00+08:00",
        "summary": "行业资讯摘要，仅供人工复核。",
        "articles": [{"title": "不应出现在邮件中的原始链接"}],
    }

    body = build_graph_message_payload(result, graph_config())["message"]["body"]["content"]

    assert "近期资讯摘要（仅供人工复核）" in body
    assert "行业资讯摘要，仅供人工复核。" in body
    assert "不应出现在邮件中的原始链接" not in body


def test_data_error_email_keeps_error_head_and_tail_but_removes_bulky_middle():
    error = "INVALID_RESPONSE: " + "1234567890" * 80 + " final dtype=float64"
    compact = _compact_error_for_email(error)

    assert compact.startswith("INVALID_RESPONSE:")
    assert compact.endswith("final dtype=float64")
    assert "中间已省略" in compact
    assert len(compact) < len(error)


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


def test_unattended_graph_auth_never_starts_device_login(monkeypatch, tmp_path):
    class FakeCache:
        has_state_changed = False

        def deserialize(self, _value):
            pass

    class FakeApp:
        device_flow_started = False

        def get_accounts(self, **_kwargs):
            return []

        def initiate_device_flow(self, **_kwargs):
            self.device_flow_started = True
            return {"user_code": "should-not-be-used"}

    fake_app = FakeApp()
    monkeypatch.setitem(sys.modules, "msal", SimpleNamespace(
        SerializableTokenCache=FakeCache,
        PublicClientApplication=lambda *_args, **_kwargs: fake_app,
    ))
    settings = {
        "client_id": "client-id", "recipient": "one@example.com", "expected_sender": None,
        "authority": "https://login.microsoftonline.com/consumers", "scopes": ["Mail.Send"],
        "endpoint": "https://graph.microsoft.com/v1.0/me/sendMail", "cache_file": "cache.json",
        "timeout_seconds": 30, "save_to_sent_items": True,
    }

    with pytest.raises(MicrosoftGraphAuthError, match="local token cache cannot be refreshed"):
        acquire_graph_access_token(settings, tmp_path, logging.getLogger("test_graph_auth"))

    assert fake_app.device_flow_started is False


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
