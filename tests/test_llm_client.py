from __future__ import annotations

import logging

import pytest

import src.llm_client as llm_client
from src.config_loader import validate_llm_config


def sample_result() -> dict:
    return {
        "asset": {"name": "电网设备ETF", "symbol": "561380"},
        "decision": {
            "status": "WAIT",
            "reasons": ["仍处于连续下跌阶段", "尚未出现止跌确认"],
        },
        "indicators": {
            "daily_change_pct": -1.2,
            "five_day_change_pct": -5.0,
            "ten_day_change_pct": -9.0,
            "six_month_drawdown_pct": -12.0,
            "drawdown_from_last_buy_pct": -3.2,
            "consecutive_down_days": 4,
            "is_new_10d_low": True,
        },
    }


def ollama_config() -> dict:
    return {
        "enabled": True,
        "provider": "ollama",
        "model": "qwen3:8b",
        "fallback_models": ["qwen3:4b"],
        "base_url": "http://127.0.0.1:11434",
        "chat_endpoint": "/api/chat",
        "tags_endpoint": "/api/tags",
        "connect_timeout_seconds": 5,
        "timeout_seconds": 180,
        "temperature": 0.1,
        "num_ctx": 4096,
        "num_predict": 320,
        "think": False,
        "keep_alive": "0",
        "max_summary_chars": 220,
    }


def null_logger() -> logging.Logger:
    logger = logging.getLogger("test_ollama")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


def test_valid_ollama_config():
    validate_llm_config(ollama_config())


def test_invalid_ollama_provider():
    config = ollama_config()
    config["provider"] = "unknown"
    with pytest.raises(ValueError):
        validate_llm_config(config)


def test_ollama_payload_disables_thinking():
    payload = llm_client._ollama_payload(sample_result(), ollama_config(), "qwen3:8b")
    assert payload["model"] == "qwen3:8b"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["keep_alive"] == "0"
    assert payload["options"]["num_ctx"] == 4096


def test_generate_ollama_summary(monkeypatch):
    config = ollama_config()

    class TagsResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"models": [{"name": "qwen3:8b"}, {"name": "qwen3:4b"}]}

    class ChatResponse:
        status_code = 200
        text = ""
        def raise_for_status(self):
            return None
        def json(self):
            return {
                "message": {"content": "当前状态为WAIT，价格仍在回落，且尚未出现止跌确认，因此维持等待。"},
                "total_duration": 2_000_000_000,
                "load_duration": 500_000_000,
            }

    monkeypatch.setattr(llm_client.requests, "get", lambda *args, **kwargs: TagsResponse())
    monkeypatch.setattr(llm_client.requests, "post", lambda *args, **kwargs: ChatResponse())

    summary = llm_client.generate_summary(sample_result(), config, logger=null_logger())
    assert summary.startswith("当前状态为WAIT")


def test_falls_back_to_4b_when_8b_not_installed(monkeypatch):
    config = ollama_config()
    captured = {}

    class TagsResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"models": [{"name": "qwen3:4b"}]}

    class ChatResponse:
        status_code = 200
        text = ""
        def raise_for_status(self):
            return None
        def json(self):
            return {"message": {"content": "维持WAIT，当前仍缺少止跌确认。"}}

    def fake_post(url, json, timeout):
        captured["model"] = json["model"]
        return ChatResponse()

    monkeypatch.setattr(llm_client.requests, "get", lambda *args, **kwargs: TagsResponse())
    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    llm_client.generate_summary(sample_result(), config, logger=null_logger())
    assert captured["model"] == "qwen3:4b"


def test_clean_summary_removes_think_block_and_limits_length():
    text = "<think>内部推理</think>摘要：" + "测试内容" * 100
    result = llm_client._clean_summary(text, 40)
    assert "内部推理" not in result
    assert len(result) <= 42
