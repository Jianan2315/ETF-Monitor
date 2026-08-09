from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests


class LLMError(RuntimeError):
    """Base error for optional summary generation."""


class OllamaUnavailableError(LLMError):
    """Raised when the local Ollama service cannot be reached."""


class OllamaModelError(LLMError):
    """Raised when no configured local model can generate a response."""


def _build_prompt(result: dict, max_chars: int) -> str:
    indicators = result["indicators"]
    decision = result["decision"]
    asset = result["asset"]

    return (
        f"请根据以下结构化结果生成一段不超过{max_chars}个中文字符的摘要。\n"
        "要求：\n"
        "1. 只输出最终摘要，不展示思考过程，不使用Markdown标题或列表；\n"
        "2. 不得改变Python给出的状态和判断原因；\n"
        "3. 不得添加新的买卖建议、价格预测或确定性结论；\n"
        "4. 用自然、克制、易读的中文说明当前状态及主要原因。\n\n"
        f"基金：{asset['name']}（{asset['symbol']}）\n"
        f"今日涨跌：{indicators['daily_change_pct']:.2f}%\n"
        f"近5日涨跌：{indicators['five_day_change_pct']:.2f}%\n"
        f"近10日涨跌：{indicators['ten_day_change_pct']:.2f}%\n"
        f"近6个月高点回撤：{indicators['six_month_drawdown_pct']:.2f}%\n"
        f"相对上次买入：{indicators['drawdown_from_last_buy_pct']:.2f}%\n"
        f"连续下跌：{indicators['consecutive_down_days']}日\n"
        f"10日新低：{indicators['is_new_10d_low']}\n"
        f"正式状态：{decision['status']}\n"
        f"正式原因：{'；'.join(decision['reasons'])}"
    )


def _clean_summary(text: str, max_chars: int) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"^\s*(摘要|最终摘要)\s*[：:]\s*", "", cleaned)
    cleaned = cleaned.strip().strip('"“”')

    if not cleaned:
        raise LLMError("模型返回了空摘要")

    if len(cleaned) <= max_chars:
        return cleaned

    candidate = cleaned[:max_chars]
    punctuation_positions = [candidate.rfind(mark) for mark in "。！？；"]
    cut = max(punctuation_positions)
    if cut >= max_chars - 50:
        return candidate[: cut + 1]
    return candidate.rstrip("，、；： ") + "……"


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _configured_models(config: dict) -> list[str]:
    models: list[str] = []
    primary = str(config.get("model", "qwen3:8b")).strip()
    if primary:
        models.append(primary)

    fallback = config.get("fallback_models", []) or []
    if isinstance(fallback, str):
        fallback = [fallback]
    for model in fallback:
        name = str(model).strip()
        if name and name not in models:
            models.append(name)
    return models


def _get_installed_models(config: dict, logger: logging.Logger) -> set[str] | None:
    url = _join_url(
        str(config.get("base_url", "http://127.0.0.1:11434")),
        str(config.get("tags_endpoint", "/api/tags")),
    )
    connect_timeout = float(config.get("connect_timeout_seconds", 5))

    try:
        response = requests.get(url, timeout=(connect_timeout, connect_timeout))
        response.raise_for_status()
        payload = response.json()
        names = {
            str(item.get("name", "")).strip()
            for item in payload.get("models", [])
            if str(item.get("name", "")).strip()
        }
        logger.info("Ollama installed models detected: %s", ", ".join(sorted(names)) or "none")
        return names
    except requests.ConnectionError as exc:
        raise OllamaUnavailableError(
            "无法连接本机 Ollama。请确认 Ollama 应用或 `ollama serve` 已启动，"
            "并检查 llm.base_url。"
        ) from exc
    except requests.RequestException as exc:
        logger.warning("无法读取 Ollama 模型列表，将直接尝试配置模型: %s", exc)
        return None
    except (ValueError, TypeError) as exc:
        logger.warning("Ollama /api/tags 返回格式异常，将直接尝试配置模型: %s", exc)
        return None


def _model_is_installed(model: str, installed: set[str]) -> bool:
    if model in installed:
        return True
    # Ollama may expose an explicit :latest tag for an untagged model name.
    return f"{model}:latest" in installed


def _ollama_payload(result: dict, config: dict, model: str) -> dict[str, Any]:
    max_chars = int(config.get("max_summary_chars", 220))
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是ETF监控工具的文字整理模块。Python策略结果是唯一正式结论。"
                    "你只能压缩和解释既有信息，不得改变状态，不得补充新的投资指令。"
                ),
            },
            {
                "role": "user",
                "content": _build_prompt(result, max_chars),
            },
        ],
        "stream": False,
        "think": bool(config.get("think", False)),
        "keep_alive": config.get("keep_alive", "0"),
        "options": {
            "temperature": float(config.get("temperature", 0.1)),
            "num_ctx": int(config.get("num_ctx", 4096)),
            "num_predict": int(config.get("num_predict", 320)),
        },
    }


def _generate_with_ollama(
    result: dict,
    config: dict,
    logger: logging.Logger,
) -> str:
    base_url = str(config.get("base_url", "http://127.0.0.1:11434"))
    endpoint = str(config.get("chat_endpoint", "/api/chat"))
    url = _join_url(base_url, endpoint)
    connect_timeout = float(config.get("connect_timeout_seconds", 5))
    timeout = float(config.get("timeout_seconds", 180))
    max_chars = int(config.get("max_summary_chars", 220))

    models = _configured_models(config)
    if not models:
        raise OllamaModelError("llm.model 和 llm.fallback_models 均为空")

    installed = _get_installed_models(config, logger)
    if installed is not None:
        available = [model for model in models if _model_is_installed(model, installed)]
        missing = [model for model in models if model not in available]
        if missing:
            logger.warning("Configured Ollama models not installed: %s", ", ".join(missing))
        if not available:
            raise OllamaModelError(
                "没有找到已配置的本地模型。请运行："
                + " 或 ".join(f"ollama pull {model}" for model in models)
            )
        models = available

    failures: list[str] = []
    for index, model in enumerate(models):
        is_fallback = index > 0
        logger.info(
            "Calling local Ollama | Model=%s | Fallback=%s | URL=%s",
            model,
            is_fallback,
            url,
        )

        try:
            response = requests.post(
                url,
                json=_ollama_payload(result, config, model),
                timeout=(connect_timeout, timeout),
            )

            if response.status_code == 404:
                failures.append(f"{model}: model not found")
                logger.warning("Ollama model unavailable: %s", model)
                continue

            response.raise_for_status()
            payload = response.json()
            content = str(payload.get("message", {}).get("content", ""))
            summary = _clean_summary(content, max_chars)

            total_seconds = float(payload.get("total_duration", 0) or 0) / 1_000_000_000
            load_seconds = float(payload.get("load_duration", 0) or 0) / 1_000_000_000
            logger.info(
                "Local summary generated | Model=%s | Characters=%s | Total=%.2fs | Load=%.2fs",
                model,
                len(summary),
                total_seconds,
                load_seconds,
            )
            return summary

        except requests.ConnectionError as exc:
            raise OllamaUnavailableError(
                "无法连接本机 Ollama。请确认 Ollama 已启动并监听 127.0.0.1:11434。"
            ) from exc
        except requests.Timeout:
            failures.append(f"{model}: timeout after {timeout:g}s")
            logger.warning("Ollama model timed out: %s", model)
        except requests.HTTPError as exc:
            body = response.text[:500] if "response" in locals() else ""
            failures.append(f"{model}: HTTP {getattr(response, 'status_code', '?')} {body}")
            logger.warning("Ollama request failed for %s: %s", model, exc)
        except (ValueError, TypeError, KeyError, LLMError) as exc:
            failures.append(f"{model}: invalid response: {exc}")
            logger.warning("Ollama response unusable for %s: %s", model, exc)

    raise OllamaModelError("所有本地模型均生成失败：" + " | ".join(failures))


def _generate_with_deepseek(result: dict, config: dict, logger: logging.Logger) -> str:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise LLMError("未配置 DEEPSEEK_API_KEY")

    max_chars = int(config.get("max_summary_chars", 220))
    response = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.get("model", "deepseek-chat"),
            "messages": [
                {
                    "role": "system",
                    "content": "你只负责解释Python的确定性判断，不得修改结论。",
                },
                {
                    "role": "user",
                    "content": _build_prompt(result, max_chars),
                },
            ],
            "temperature": float(config.get("temperature", 0.2)),
        },
        timeout=float(config.get("timeout_seconds", 30)),
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    summary = _clean_summary(content, max_chars)
    logger.info("DeepSeek summary generated | Characters=%s", len(summary))
    return summary


def generate_summary(
    result: dict,
    config: dict,
    logger: logging.Logger | None = None,
) -> str:
    active_logger = logger or logging.getLogger("etf_monitor")
    provider = str(config.get("provider", "ollama")).lower().strip()

    if provider == "ollama":
        return _generate_with_ollama(result, config, active_logger)
    if provider == "deepseek":
        return _generate_with_deepseek(result, config, active_logger)

    raise ValueError(f"不支持的 llm.provider: {provider}")


def _build_news_prompt(asset: dict, articles: list[dict], max_chars: int) -> str:
    source_lines = []
    for index, article in enumerate(articles[:12], start=1):
        source_lines.append(
            f"{index}. 标题：{article.get('title', '')}\n"
            f"来源：{article.get('source', '')}；时间：{article.get('published_at', '')}\n"
            f"摘要：{article.get('snippet', '')}\n"
            f"链接：{article.get('url', '')}"
        )
    return (
        f"请根据下列有链接的资讯，为 {asset.get('name') or asset.get('tracking_name') or '当前资产'} "
        f"整理一段不超过 {max_chars} 个中文字符的‘资讯与研究摘要’。\n"
        "严格要求：\n"
        "1. 只能概括标题和摘要明确支持的事实，不得补充外部知识或猜测；\n"
        "2. 不得给出买入、卖出、持有、价格预测或交易状态；\n"
        "3. 若资讯不足、相互矛盾或只是重复报道，须直接说明；\n"
        "4. 只输出一段摘要正文，不使用标题、列表或来源编号。\n\n"
        + "\n\n".join(source_lines)
    )


def generate_news_summary(
    asset: dict,
    articles: list[dict],
    config: dict,
    logger: logging.Logger | None = None,
) -> str:
    """Summarise explicitly retrieved news without letting the model search or trade."""
    active_logger = logger or logging.getLogger("etf_monitor")
    if str(config.get("provider", "ollama")).lower().strip() != "ollama":
        raise LLMError("资讯摘要首版仅使用本地 Ollama 模型")

    base_url = str(config.get("base_url", "http://127.0.0.1:11434"))
    endpoint = str(config.get("chat_endpoint", "/api/chat"))
    url = _join_url(base_url, endpoint)
    connect_timeout = float(config.get("connect_timeout_seconds", 5))
    timeout = float(config.get("timeout_seconds", 180))
    max_chars = int(config.get("max_summary_chars", 220))
    models = _configured_models(config)
    installed = _get_installed_models(config, active_logger)
    if installed is not None:
        models = [model for model in models if _model_is_installed(model, installed)]
    if not models:
        raise OllamaModelError("未找到可用的本地 Ollama 模型")

    prompt = _build_news_prompt(asset, articles, max_chars)
    failures: list[str] = []
    for model in models:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是资讯整理模块。只根据用户提供的来源片段归纳事实，"
                        "不搜索、不推断、不输出交易建议。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "keep_alive": config.get("keep_alive", "0"),
            "options": {
                "temperature": float(config.get("temperature", 0.1)),
                "num_ctx": int(config.get("num_ctx", 4096)),
                "num_predict": int(config.get("num_predict", 320)),
            },
        }
        try:
            response = requests.post(url, json=payload, timeout=(connect_timeout, timeout))
            response.raise_for_status()
            content = str(response.json().get("message", {}).get("content", ""))
            summary = _clean_summary(content, max_chars)
            active_logger.info("Local news summary generated | Model=%s | Characters=%s", model, len(summary))
            return summary
        except Exception as exc:
            failures.append(f"{model}: {type(exc).__name__}: {exc}")
            active_logger.warning("Local news summary failed for %s: %s", model, exc)
    raise OllamaModelError("本地资讯摘要生成失败：" + " | ".join(failures))
