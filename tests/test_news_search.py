from datetime import datetime, timedelta, timezone

from src.news_search import (
    load_news_settings,
    load_news_snapshot,
    news_search_plan,
    save_news_settings,
    search_asset_news,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_news_settings_default_to_seven_days_and_are_asset_specific(tmp_path):
    assert load_news_settings(tmp_path, "015596") == {"lookback_days": 7, "queries": []}
    saved = save_news_settings(tmp_path, "015596", {"lookback_days": 14, "queries": ["有色金属", "铜供需", "有色金属"]})
    assert saved == {"lookback_days": 14, "queries": ["有色金属", "铜供需"]}
    assert load_news_settings(tmp_path, "015596") == saved
    assert load_news_settings(tmp_path, "023639") == {"lookback_days": 7, "queries": []}


def test_news_settings_exposes_tracking_name_as_an_editable_default(tmp_path):
    config_dir = tmp_path / "config" / "assets"
    config_dir.mkdir(parents=True)
    (config_dir / "015596.json").write_text(
        '{"asset": {"tracking_name": "国证有色"}}',
        encoding="utf-8",
    )

    assert load_news_settings(tmp_path, "015596") == {
        "lookback_days": 7,
        "queries": ["国证有色"],
    }


def test_news_search_keeps_source_links_and_does_not_require_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    asset = {"id": "015596", "name": "国泰国证有色金属行业指数C", "tracking_name": "国证有色"}
    captured = []

    def fake_get(url, params, timeout):
        captured.append({"url": url, "params": params, "timeout": timeout})
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
    assert captured[0]["params"]["engine"] == "google_news"
    assert captured[0]["params"]["hl"] == "zh-cn"
    assert "so" not in captured[0]["params"]
    assert captured[0]["params"]["api_key"] == "test-key"
    assert load_news_snapshot(tmp_path, "015596")["status"] == "SUCCESS"


def test_news_search_runs_each_query_then_merges_duplicate_articles(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    save_news_settings(tmp_path, "015596", {"lookback_days": 7, "queries": ["国证有色", "有色金属"]})
    calls = []

    def fake_get(_url, params, timeout):
        calls.append(params["q"])
        return FakeResponse({"news_results": [
            {"title": "共同资讯", "link": "https://example.test/shared", "source": {"name": "示例"}, "iso_date": "2026-08-08T08:00:00Z"},
            {"title": f"{params['q']} 独有资讯", "link": f"https://example.test/{len(calls)}", "source": {"name": "示例"}, "iso_date": "2026-08-08T08:00:00Z"},
        ]})

    snapshot = search_asset_news(
        tmp_path,
        {"id": "015596", "name": "示例资产", "tracking_name": "国证有色"},
        llm_config={"enabled": False},
        request_get=fake_get,
        now=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert calls == ["国证有色", "有色金属"]
    assert snapshot["queries"] == calls
    assert len(snapshot["articles"]) == 3
    assert "合并去重后获取 3 条" in snapshot["message"]
    assert snapshot["request_count"] == 2
    plan = news_search_plan(
        tmp_path,
        {"id": "015596", "name": "示例资产", "tracking_name": "国证有色"},
        now=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    assert plan["usage"]["daily_used"] == 2
    assert plan["usage"]["monthly_used"] == 2


def test_news_plan_reuses_recent_successful_snapshot(tmp_path):
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    snapshot_path = tmp_path / "data" / "assets" / "015596" / "research" / "news_latest.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        '{"status":"SUCCESS","generated_at":"2026-08-10T00:00:00+00:00"}',
        encoding="utf-8",
    )
    asset = {"id": "015596", "name": "示例资产", "tracking_name": "国证有色"}

    plan = news_search_plan(tmp_path, asset, now=now)
    assert plan["request_count"] == 1
    assert plan["snapshot"]["is_reusable"] is True

    expired = news_search_plan(tmp_path, asset, now=now + timedelta(days=2))
    assert expired["snapshot"]["is_reusable"] is False


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


def test_news_search_surfaces_provider_error_without_request_url(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    def fake_get(*_args, **_kwargs):
        return FakeResponse({"error": "Invalid parameter: hl"}, status_code=400)

    snapshot = search_asset_news(
        tmp_path,
        {"id": "015596", "name": "示例资产", "tracking_name": "示例跟踪"},
        llm_config={"enabled": False},
        request_get=fake_get,
        now=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert snapshot["status"] == "FAILED"
    assert "Invalid parameter: hl" in snapshot["message"]
    assert "search.json" not in snapshot["message"]
    assert "test-key" not in snapshot["message"]
