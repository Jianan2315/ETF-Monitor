from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
}


def redact_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if key.lower() in SENSITIVE_HEADERS:
            result[key] = "<redacted>"
        else:
            result[key] = str(value)
    return result


def browser_candidates() -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = [
        {"channel": "chrome", "label": "Google Chrome"},
        {"channel": "msedge", "label": "Microsoft Edge"},
    ]

    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        program_files = Path(os.environ.get("ProgramFiles", ""))
        program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", ""))

        paths = [
            (local / "Google" / "Chrome" / "Application" / "chrome.exe", "Google Chrome"),
            (program_files / "Google" / "Chrome" / "Application" / "chrome.exe", "Google Chrome"),
            (program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe", "Google Chrome"),
            (program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe", "Microsoft Edge"),
            (program_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe", "Microsoft Edge"),
        ]
        for path, label in paths:
            if path.exists():
                candidates.append({
                    "executable_path": str(path),
                    "label": label,
                })

    # Deduplicate candidates while preserving order.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in candidates:
        key = (
            item.get("channel", ""),
            item.get("executable_path", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def quote_page_url(symbol: str) -> str:
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"https://quote.eastmoney.com/{prefix}{symbol}.html"


def historical_api_url(symbol: str, now: datetime) -> str:
    secid = f"{'1' if symbol.startswith(('5', '6', '9')) else '0'}.{symbol}"
    params = {
        "secid": secid,
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": "20210101",
        "end": now.strftime("%Y%m%d"),
        "lmt": "1000",
    }
    return (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        + urlencode(params)
    )


def _is_interesting_url(url: str) -> bool:
    lower = url.lower()
    return (
        "eastmoney.com" in lower
        and (
            "kline" in lower
            or "push2his" in lower
            or "/api/qt/" in lower
        )
    )


def _safe_body_preview(response, limit: int = 800) -> str | None:
    try:
        text = response.text()
        return text[:limit].replace("\r", " ").replace("\n", " ")
    except Exception:
        return None


def inspect_browser_requests(
    *,
    symbol: str,
    project_root: Path,
    logger: logging.Logger | None = None,
    visible_seconds: int = 12,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright尚未安装。请重新运行Start ETF Monitor.bat，"
            "启动器会根据requirements.txt安装缺失依赖。"
        ) from exc

    diagnostics_dir = project_root / "data" / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    har_path = diagnostics_dir / "browser_network.har"
    summary_path = diagnostics_dir / "latest_browser_inspector.json"

    quote_url = quote_page_url(symbol)
    api_url = historical_api_url(symbol, datetime.now())

    network_events: list[dict[str, Any]] = []
    failed_requests: list[dict[str, Any]] = []
    browser_fetch_result: dict[str, Any] = {}
    launch_errors: list[str] = []
    selected_browser: str | None = None
    started_at = datetime.now()

    with sync_playwright() as playwright:
        browser = None
        for candidate in browser_candidates():
            try:
                launch_kwargs: dict[str, Any] = {
                    "headless": False,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--start-maximized",
                    ],
                }
                if candidate.get("channel"):
                    launch_kwargs["channel"] = candidate["channel"]
                if candidate.get("executable_path"):
                    launch_kwargs["executable_path"] = candidate["executable_path"]

                browser = playwright.chromium.launch(**launch_kwargs)
                selected_browser = candidate["label"]
                break
            except Exception as exc:
                launch_errors.append(
                    f'{candidate["label"]}: {type(exc).__name__}: {exc}'
                )

        if browser is None:
            raise RuntimeError(
                "无法启动Chrome或Edge。"
                + ("；".join(launch_errors) if launch_errors else "")
            )

        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            record_har_path=str(har_path),
            record_har_mode="minimal",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        def on_response(response) -> None:
            if not _is_interesting_url(response.url):
                return
            request = response.request
            event = {
                "kind": "response",
                "url": response.url,
                "status": response.status,
                "status_text": response.status_text,
                "resource_type": request.resource_type,
                "method": request.method,
                "request_headers": redact_headers(request.headers),
                "response_headers": redact_headers(response.headers),
                "body_preview": _safe_body_preview(response),
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            }
            network_events.append(event)
            if logger:
                logger.info(
                    "Browser inspector response | Status=%s | Resource=%s | URL=%s",
                    response.status,
                    request.resource_type,
                    response.url,
                )

        def on_request_failed(request) -> None:
            if not _is_interesting_url(request.url):
                return
            event = {
                "kind": "request_failed",
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "request_headers": redact_headers(request.headers),
                "failure": request.failure,
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            }
            failed_requests.append(event)
            if logger:
                logger.warning(
                    "Browser inspector request failed | Resource=%s | URL=%s | Failure=%s",
                    request.resource_type,
                    request.url,
                    request.failure,
                )

        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)

        navigation_error = None
        try:
            page.goto(
                quote_url,
                wait_until="domcontentloaded",
                timeout=45_000,
            )
        except Exception as exc:
            navigation_error = f"{type(exc).__name__}: {exc}"
            if logger:
                logger.exception("Browser inspector page navigation failed")

        try:
            page.wait_for_timeout(5_000)
            browser_fetch_result = page.evaluate(
                """
                async (url) => {
                  const started = performance.now();
                  try {
                    const response = await fetch(url, {
                      method: "GET",
                      credentials: "include",
                      cache: "no-store"
                    });
                    const body = await response.text();
                    return {
                      ok: response.ok,
                      status: response.status,
                      statusText: response.statusText,
                      elapsedMs: Math.round(performance.now() - started),
                      bodyLength: body.length,
                      bodyPreview: body.slice(0, 800),
                      responseHeaders: Object.fromEntries(response.headers.entries())
                    };
                  } catch (error) {
                    return {
                      ok: false,
                      elapsedMs: Math.round(performance.now() - started),
                      errorName: error?.name || "Error",
                      errorMessage: String(error)
                    };
                  }
                }
                """,
                api_url,
            )
        except Exception as exc:
            browser_fetch_result = {
                "ok": False,
                "errorName": type(exc).__name__,
                "errorMessage": str(exc),
            }
            if logger:
                logger.exception("Browser inspector in-page fetch failed")

        page.wait_for_timeout(max(0, int(visible_seconds)) * 1000)
        final_title = page.title()
        final_url = page.url
        cookies = context.cookies()
        cookie_summary = [
            {
                "name": item.get("name"),
                "domain": item.get("domain"),
                "path": item.get("path"),
                "secure": item.get("secure"),
                "sameSite": item.get("sameSite"),
            }
            for item in cookies
            if "eastmoney.com" in str(item.get("domain", ""))
        ]

        context.close()
        browser.close()

    kline_events = [
        item for item in network_events
        if "kline" in str(item.get("url", "")).lower()
    ]
    successful_kline_events = [
        item for item in kline_events
        if int(item.get("status", 0)) == 200
    ]

    result = {
        "symbol": symbol,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "browser": selected_browser,
        "quote_page_url": quote_url,
        "final_page_url": final_url,
        "page_title": final_title,
        "navigation_error": navigation_error,
        "browser_fetch_url": api_url,
        "browser_fetch_result": browser_fetch_result,
        "interesting_response_count": len(network_events),
        "failed_request_count": len(failed_requests),
        "kline_response_count": len(kline_events),
        "successful_kline_response_count": len(successful_kline_events),
        "network_events": network_events,
        "failed_requests": failed_requests,
        "eastmoney_cookie_summary": cookie_summary,
        "har_file": str(har_path.relative_to(project_root)),
        "launch_errors": launch_errors,
        "ok": bool(
            successful_kline_events
            or browser_fetch_result.get("status") == 200
        ),
        "interpretation": (
            "浏览器上下文成功取得历史K线响应，普通requests失败更可能与请求指纹、"
            "Cookie、TLS或反自动化策略有关。"
            if successful_kline_events or browser_fetch_result.get("status") == 200
            else
            "浏览器上下文也没有取得历史K线响应，问题更可能是接口、网络线路、"
            "区域访问策略或东方财富服务端限制，而不仅是普通requests请求头。"
        ),
    }

    temp_path = summary_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(summary_path)

    if logger:
        logger.info(
            "Browser inspector completed | Browser=%s | Responses=%s | "
            "KlineResponses=%s | SuccessfulKlineResponses=%s | BrowserFetchStatus=%s",
            selected_browser,
            len(network_events),
            len(kline_events),
            len(successful_kline_events),
            browser_fetch_result.get("status"),
        )

    return result
