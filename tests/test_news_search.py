from datetime import datetime, timezone

from src.news_search import (
    load_news_settings,
    load_news_snapshot,
    save_news_settings,
    search_asset_news,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_news_settings_default_to_seven_days_and_are_asset_specific(tmp_path):
    assert load_news_settings(tmp_path, "015596") == {"lookback_days": 7, "query_keywords": ""}
    saved = save_news_settings(tmp_path, "015596", {"lookback_days": 14, "query_keywords": "有色金属 铜"})
    assert saved == {"lookback_days": 14, "query_keywords": "有色金属 铜"}
    assert load_news_settings(tmp_path, "015596") == saved
    assert load_news_settings(tmp_path, "023639") == {"lookback_days": 7, "query_keywords": ""}


def test_news_search_keeps_source_links_and_does_not_require_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    asset = {"id": "015596", "name": "国泰国证有色金属行业指数C", "tracking_name": "国证有色"}
    captured = {}

    def fake_get(url, params, timeout):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return FakeResponse({"news_results": [
            {
                "title": "铜价与有色行业资讯",
                "link": "https://example.test/one",
                "source": {"name": "示例财经"},
                "iso_date": "2026-08-08T08:00:00Z",
                "snippet": "近期行业动态。",
            },
            {
                "title": "过期资讯",
                "link": "https://example.test/old",
                "source": {"name": "示例财经"},
                "iso_date": "2026-07-01T08:00:00Z",
            },
        ]})

    snapshot = search_asset_news(
        tmp_path, asset, llm_config={"enabled": False}, request_get=fake_get, now=now,
    )

    assert snapshot["status"] == "SUCCESS"
    assert snapshot["summary"] is None
    assert len(snapshot["articles"]) == 1
    assert snapshot["articles"][0]["url"] == "https://example.test/one"
    assert captured["params"]["engine"] == "google_news"
    assert captured["params"]["api_key"] == "test-key"
    assert load_news_snapshot(tmp_path, "015596")["status"] == "SUCCESS"


def test_news_search_records_clear_failure_without_blocking(tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    snapshot = search_asset_news(
        tmp_path,
        {"id": "015596", "name": "示例资产", "tracking_name": "示例跟踪"},
        llm_config={"enabled": False},
        now=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    assert snapshot["status"] == "FAILED"
    assert snapshot["error_code"] == "MISSING_API_KEY"
    assert "SERPAPI_API_KEY" in snapshot["message"]
