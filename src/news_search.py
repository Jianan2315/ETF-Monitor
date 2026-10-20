from __future__ import annotations

"""Source-linked asset news retrieval for human review only.

News never participates in strategy evaluation.  Reports can reuse a recent
successful snapshot, while scheduled runs may refresh an expired snapshot
within a small local request budget.  Neither path can change a trading state.
"""

from datetime import datetime, timedelta, timezone
from src.schedule_clock import as_beijing
import json
import logging
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from src.asset_store import (
    NEWS_CONFIG_SUFFIX,
    asset_data_dir,
    asset_config_dir,
    safe_asset_id,
)


DEFAULT_NEWS_SETTINGS = {
    "lookback_days": 7,
    "queries": [],
}
NEWS_MONTHLY_REQUEST_BUDGET = 250
NEWS_DAILY_REQUEST_BUDGET = 12
NEWS_REUSE_DAYS = 3


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _settings_path(root: Path, asset_id: str) -> Path:
    return asset_config_dir(root) / f"{safe_asset_id(asset_id)}{NEWS_CONFIG_SUFFIX}"


def _normalise_queries(value: Any) -> list[str]:
    """Normalise one-query-per-line input and cap paid provider requests."""
    values = value if isinstance(value, list) else str(value or "").splitlines()
    queries: list[str] = []
    seen: set[str] = set()
    for value in values:
        query = str(value or "").strip()
        identity = query.casefold()
        if not query or identity in seen:
            continue
        queries.append(query)
        seen.add(identity)
    return queries


def _asset_default_queries(root: Path, asset_id: str) -> list[str]:
    """Expose a new asset's tracking name as an editable first search term."""
    document = _read_json(asset_config_dir(root) / f"{safe_asset_id(asset_id)}.json", {})
    asset = document.get("asset", {}) if isinstance(document, dict) else {}
    return _normalise_queries(asset.get("tracking_name", "") if isinstance(asset, dict) else "")


def load_news_settings(root: Path, asset_id: str) -> dict[str, Any]:
    document = _read_json(_settings_path(root, asset_id), {})
    raw = document.get("news", {}) if isinstance(document, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    try:
        lookback_days = int(raw.get("lookback_days", DEFAULT_NEWS_SETTINGS["lookback_days"]))
    except (TypeError, ValueError):
        lookback_days = DEFAULT_NEWS_SETTINGS["lookback_days"]
    queries = _normalise_queries(raw.get("queries", DEFAULT_NEWS_SETTINGS["queries"]))
    return {
        "lookback_days": min(max(lookback_days, 1), 30),
        "queries": queries or _asset_default_queries(root, asset_id),
    }


def save_news_settings(root: Path, asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        lookback_days = int(payload.get("lookback_days", DEFAULT_NEWS_SETTINGS["lookback_days"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("资讯检索范围必须是 1 到 30 天之间的整数") from exc
    if not 1 <= lookback_days <= 30:
        raise ValueError("资讯检索范围必须是 1 到 30 天之间的整数")
    queries = _normalise_queries(payload.get("queries", []))
    if len(queries) > 8:
        raise ValueError("每个资产最多保存 8 个资讯检索词，以避免一次检索消耗过多额度")
    settings = {
        "lookback_days": lookback_days,
        "queries": queries,
    }
    key = safe_asset_id(asset_id)
    _write_json(_settings_path(root, key), {"asset_id": key, "news": settings})
    return settings


def news_snapshot_path(root: Path, asset_id: str) -> Path:
    return asset_data_dir(root, asset_id) / "research" / "news_latest.json"


def _usage_path(root: Path) -> Path:
    return root / "data" / "global" / "news_search_usage.json"


def _usage_keys(current: datetime) -> tuple[str, str]:
    local = current.astimezone().date()
    return local.strftime("%Y-%m"), local.isoformat()


def _usage_document(root: Path) -> dict[str, Any]:
    document = _read_json(_usage_path(root), {})
    return document if isinstance(document, dict) else {}


def _usage_summary(root: Path, current: datetime) -> dict[str, Any]:
    document = _usage_document(root)
    monthly = document.get("monthly", {}) if isinstance(document.get("monthly"), dict) else {}
    daily = document.get("daily", {}) if isinstance(document.get("daily"), dict) else {}
    month_key, day_key = _usage_keys(current)
    month_used = int(monthly.get(month_key, 0) or 0)
    day_used = int(daily.get(day_key, 0) or 0)
    return {
        "tracking": "本地记录估算（不包含启用本功能前或其他工具产生的 SerpApi 用量）",
        "monthly_budget": NEWS_MONTHLY_REQUEST_BUDGET,
        "monthly_used": month_used,
        "monthly_remaining": max(NEWS_MONTHLY_REQUEST_BUDGET - month_used, 0),
        "daily_budget": NEWS_DAILY_REQUEST_BUDGET,
        "daily_used": day_used,
        "daily_remaining": max(NEWS_DAILY_REQUEST_BUDGET - day_used, 0),
    }


def _record_usage(root: Path, request_count: int, current: datetime) -> dict[str, Any]:
    document = _usage_document(root)
    monthly = document.setdefault("monthly", {})
    daily = document.setdefault("daily", {})
    month_key, day_key = _usage_keys(current)
    monthly[month_key] = int(monthly.get(month_key, 0) or 0) + request_count
    daily[day_key] = int(daily.get(day_key, 0) or 0) + request_count
    document["updated_at"] = current.isoformat(timespec="seconds")
    _write_json(_usage_path(root), document)
    return _usage_summary(root, current)


def load_news_snapshot(root: Path, asset_id: str) -> dict[str, Any]:
    snapshot = _read_json(news_snapshot_path(root, asset_id), {})
    return snapshot if isinstance(snapshot, dict) else {}


def _snapshot_generated_at(snapshot: dict[str, Any]) -> datetime | None:
    value = str(snapshot.get("generated_at", "")).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return as_beijing(parsed)


def news_search_plan(
    root: Path,
    asset: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Describe paid calls, local budget and whether the snapshot is reusable."""
    current = as_beijing(now) if now is not None else datetime.now(timezone.utc)
    asset_id = str(asset.get("id") or asset.get("asset_code") or "")
    settings = load_news_settings(root, asset_id)
    queries = build_news_queries(asset, settings)
    snapshot = load_news_snapshot(root, asset_id)
    generated_at = _snapshot_generated_at(snapshot)
    age_hours = None
    if generated_at:
        age_hours = max((current - generated_at.astimezone(current.tzinfo or timezone.utc)).total_seconds() / 3600, 0)
    successful_snapshot = snapshot.get("status") == "SUCCESS" and generated_at is not None
    reusable = bool(successful_snapshot and age_hours is not None and age_hours < NEWS_REUSE_DAYS * 24)
    usage = _usage_summary(root, current)
    request_count = len(queries)
    return {
        "asset_id": asset_id,
        "queries": queries,
        "request_count": request_count,
        "can_refresh": bool(request_count) and request_count <= usage["daily_remaining"] and request_count <= usage["monthly_remaining"],
        "reuse_window_days": NEWS_REUSE_DAYS,
        "snapshot": {
            "status": snapshot.get("status") or "NONE",
            "generated_at": snapshot.get("generated_at"),
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
            "is_reusable": reusable,
        },
        "usage": usage,
    }


def _private_key(root: Path) -> str:
    load_dotenv(root / ".env")
    key = str(os.getenv("SERPAPI_API_KEY", "")).strip()
    if key:
        return key
    document = _read_json(root / "config" / "private" / "news_search.json", {})
    provider = document.get("serpapi", {}) if isinstance(document, dict) else {}
    return str(provider.get("api_key", "")).strip() if isinstance(provider, dict) else ""


def build_news_queries(asset: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    """Return independent searches instead of combining unrelated terms."""
    queries = _normalise_queries(settings.get("queries", []))
    if queries:
        return queries
    fallback = str(asset.get("tracking_name", "")).strip()
    return [fallback] if fallback else []


def _parse_article_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalise_articles(results: list[Any], cutoff: datetime) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        title = str(result.get("title", "")).strip()
        url = str(result.get("link") or result.get("url") or "").strip()
        identity = (title.casefold(), url)
        if not title or not url or identity in seen:
            continue
        published = _parse_article_time(result.get("iso_date"))
        if published is not None and published.astimezone(timezone.utc) < cutoff:
            continue
        source = result.get("source", {})
        source_name = source.get("name", "") if isinstance(source, dict) else str(source or "")
        articles.append({
            "title": title,
            "url": url,
            "source": str(source_name).strip() or "未提供来源",
            "published_at": str(result.get("iso_date") or result.get("date") or "").strip(),
            "snippet": str(result.get("snippet") or result.get("description") or "").strip(),
        })
        seen.add(identity)
    return articles[:20]


def _base_snapshot(asset: dict[str, Any], settings: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    queries = build_news_queries(asset, settings)
    return {
        "provider": "SerpApi Google News",
        "generated_at": now.isoformat(timespec="seconds"),
        "asset_id": str(asset.get("id") or asset.get("asset_code") or ""),
        "queries": queries,
        "query": "；".join(queries),
        "lookback_days": settings["lookback_days"],
        "status": "FAILED",
        "articles": [],
        "summary": None,
        "summary_status": "SKIPPED",
    }


def search_asset_news(
    root: Path,
    asset: dict[str, Any],
    *,
    llm_config: dict[str, Any] | None = None,
    request_get=requests.get,
    now: datetime | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Execute paid searches within the local quota and persist review data."""
    current = as_beijing(now) if now is not None else datetime.now(timezone.utc)
    settings = load_news_settings(root, str(asset.get("id") or asset.get("asset_code") or ""))
    snapshot = _base_snapshot(asset, settings, now=current)
    key = _private_key(root)
    if not key:
        snapshot.update({
            "error_code": "MISSING_API_KEY",
            "message": "资讯检索失败：未配置 SERPAPI_API_KEY。",
        })
        _write_json(news_snapshot_path(root, snapshot["asset_id"]), snapshot)
        return snapshot
    if not snapshot["queries"]:
        snapshot.update({
            "error_code": "EMPTY_QUERY",
            "message": "资讯检索失败：请至少填写一个资讯检索词，或填写资产的跟踪名称。",
        })
        _write_json(news_snapshot_path(root, snapshot["asset_id"]), snapshot)
        return snapshot

    plan = news_search_plan(root, asset, now=current)
    if not plan["can_refresh"]:
        return {
            "status": "SKIPPED_BUDGET",
            "message": (
                f"本次资讯刷新需要 {plan['request_count']} 次接口调用，"
                f"但今日本地预算剩余 {plan['usage']['daily_remaining']} 次、"
                f"本月本地预算剩余 {plan['usage']['monthly_remaining']} 次；已保留最近一次资讯快照。"
            ),
            "plan": plan,
        }

    snapshot["request_count"] = plan["request_count"]
    snapshot["usage"] = _record_usage(root, plan["request_count"], current)

    results: list[Any] = []
    failed_queries: list[str] = []
    for query in snapshot["queries"]:
        params = {
            "engine": "google_news",
            "q": query,
            "gl": "cn",
            # SerpApi's Google News engine is case-sensitive for this locale:
            # simplified Chinese must be the supported value ``zh-cn``.
            "hl": "zh-cn",
            "api_key": key,
        }
        try:
            response = request_get("https://serpapi.com/search.json", params=params, timeout=(5, 20))
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("资讯接口返回的不是 JSON 对象")
            status_code = getattr(response, "status_code", None)
            provider_error = str(
                payload.get("error")
                or (payload.get("search_metadata") or {}).get("error")
                or ""
            ).strip()
            if status_code is not None and int(status_code) >= 400:
                detail = provider_error or f"HTTP {status_code}"
                raise RuntimeError(f"SerpApi 拒绝了本次请求：{detail}")
            response.raise_for_status()
            metadata = payload.get("search_metadata", {})
            if isinstance(metadata, dict) and str(metadata.get("status", "Success")).upper() == "ERROR":
                raise RuntimeError(str(metadata.get("error") or "资讯接口返回错误"))
            query_results = payload.get("news_results", []) or []
            if not isinstance(query_results, list):
                raise ValueError("资讯接口的 news_results 不是列表")
            results.extend(query_results)
        except requests.RequestException:
            failed_queries.append(f"{query}（网络请求未完成）")
        except Exception as exc:
            failed_queries.append(f"{query}（{type(exc).__name__}: {exc}）")

    if not results and failed_queries:
        snapshot.update({
            "error_code": "QUERY_FAILED",
            "message": "资讯检索失败：" + "；".join(failed_queries),
        })
        _write_json(news_snapshot_path(root, snapshot["asset_id"]), snapshot)
        return snapshot

    cutoff = current.astimezone(timezone.utc) - timedelta(days=settings["lookback_days"])
    articles = _normalise_articles(results, cutoff)

    snapshot["articles"] = articles
    if not articles:
        snapshot.update({
            "status": "NO_RESULTS",
            "message": f"未找到过去 {settings['lookback_days']} 天内的相关资讯。",
        })
        _write_json(news_snapshot_path(root, snapshot["asset_id"]), snapshot)
        return snapshot

    message = (
        f"已完成 {len(snapshot['queries'])} 个检索词（消耗 {snapshot['request_count']} 次接口调用），"
        f"合并去重后获取 {len(articles)} 条近期资讯。"
    )
    if failed_queries:
        message += " 部分检索词失败：" + "；".join(failed_queries)
    snapshot.update({"status": "SUCCESS", "message": message})
    if llm_config and llm_config.get("enabled", False):
        try:
            from src.llm_client import generate_news_summary
            snapshot["summary"] = generate_news_summary(asset, articles, llm_config, logger=logger)
            snapshot["summary_status"] = "SUCCESS"
        except Exception as exc:
            snapshot["summary_status"] = "FAILED"
            snapshot["summary_error"] = f"{type(exc).__name__}: {exc}"
            snapshot["message"] += " 本地摘要失败，已保留原始来源。"
    _write_json(news_snapshot_path(root, snapshot["asset_id"]), snapshot)
    return snapshot
