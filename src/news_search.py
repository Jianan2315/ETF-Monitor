from __future__ import annotations

"""Manual, source-linked asset news retrieval.

This module deliberately never participates in strategy evaluation.  A user
starts a search from the review page, and reports only render the most recent
saved snapshot.  It therefore cannot create hidden scheduled web requests or
change a trading state.
"""

from datetime import datetime, timedelta, timezone
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
    "query_keywords": "",
}


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


def load_news_settings(root: Path, asset_id: str) -> dict[str, Any]:
    document = _read_json(_settings_path(root, asset_id), {})
    raw = document.get("news", {}) if isinstance(document, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    try:
        lookback_days = int(raw.get("lookback_days", DEFAULT_NEWS_SETTINGS["lookback_days"]))
    except (TypeError, ValueError):
        lookback_days = DEFAULT_NEWS_SETTINGS["lookback_days"]
    return {
        "lookback_days": min(max(lookback_days, 1), 30),
        "query_keywords": str(raw.get("query_keywords", "")).strip(),
    }


def save_news_settings(root: Path, asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        lookback_days = int(payload.get("lookback_days", DEFAULT_NEWS_SETTINGS["lookback_days"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("资讯检索范围必须是 1 到 30 天之间的整数") from exc
    if not 1 <= lookback_days <= 30:
        raise ValueError("资讯检索范围必须是 1 到 30 天之间的整数")
    settings = {
        "lookback_days": lookback_days,
        "query_keywords": str(payload.get("query_keywords", "")).strip(),
    }
    key = safe_asset_id(asset_id)
    _write_json(_settings_path(root, key), {"asset_id": key, "news": settings})
    return settings


def news_snapshot_path(root: Path, asset_id: str) -> Path:
    return asset_data_dir(root, asset_id) / "research" / "news_latest.json"


def load_news_snapshot(root: Path, asset_id: str) -> dict[str, Any]:
    snapshot = _read_json(news_snapshot_path(root, asset_id), {})
    return snapshot if isinstance(snapshot, dict) else {}


def _private_key(root: Path) -> str:
    load_dotenv(root / ".env")
    key = str(os.getenv("SERPAPI_API_KEY", "")).strip()
    if key:
        return key
    document = _read_json(root / "config" / "private" / "news_search.json", {})
    provider = document.get("serpapi", {}) if isinstance(document, dict) else {}
    return str(provider.get("api_key", "")).strip() if isinstance(provider, dict) else ""


def build_news_query(asset: dict[str, Any], settings: dict[str, Any]) -> str:
    parts = [
        str(asset.get("tracking_name", "")).strip(),
        str(asset.get("name", "")).strip(),
        str(settings.get("query_keywords", "")).strip(),
    ]
    unique: list[str] = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return " ".join(unique)


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
    return {
        "provider": "SerpApi Google News",
        "generated_at": now.isoformat(timespec="seconds"),
        "asset_id": str(asset.get("id") or asset.get("asset_code") or ""),
        "query": build_news_query(asset, settings),
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
    """Execute one explicit SerpApi request and persist its human-review result."""
    current = now or datetime.now(timezone.utc)
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
    if not snapshot["query"]:
        snapshot.update({
            "error_code": "EMPTY_QUERY",
            "message": "资讯检索失败：资产名称、跟踪名称和自定义关键词均为空。",
        })
        _write_json(news_snapshot_path(root, snapshot["asset_id"]), snapshot)
        return snapshot

    params = {
        "engine": "google_news",
        "q": snapshot["query"],
        "gl": "cn",
        "hl": "zh-CN",
        "so": "1",
        "api_key": key,
    }
    try:
        response = request_get("https://serpapi.com/search.json", params=params, timeout=(5, 20))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("资讯接口返回的不是 JSON 对象")
        metadata = payload.get("search_metadata", {})
        if isinstance(metadata, dict) and str(metadata.get("status", "Success")).upper() == "ERROR":
            raise RuntimeError(str(metadata.get("error") or "资讯接口返回错误"))
        cutoff = current.astimezone(timezone.utc) - timedelta(days=settings["lookback_days"])
        articles = _normalise_articles(payload.get("news_results", []) or [], cutoff)
    except Exception as exc:
        snapshot.update({
            "error_code": type(exc).__name__.upper(),
            "message": f"资讯检索失败：{type(exc).__name__}: {exc}",
        })
        _write_json(news_snapshot_path(root, snapshot["asset_id"]), snapshot)
        return snapshot

    snapshot["articles"] = articles
    if not articles:
        snapshot.update({
            "status": "NO_RESULTS",
            "message": f"未找到过去 {settings['lookback_days']} 天内的相关资讯。",
        })
        _write_json(news_snapshot_path(root, snapshot["asset_id"]), snapshot)
        return snapshot

    snapshot.update({"status": "SUCCESS", "message": f"已获取 {len(articles)} 条近期资讯。"})
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
