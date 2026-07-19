from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import logging
import random
import time
import traceback
import requests
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.market_clock import (
    SESSION_PHASES,
    build_market_context,
    expected_history_date,
    market_now,
    should_request_realtime,
)
from src.models import MarketBundle, MarketSnapshot


class DataFetchError(RuntimeError):
    def __init__(
        self,
        message: str,
        attempts: list[str] | None = None,
        *,
        last_available_date: str | None = None,
        expected_date: str | None = None,
        cache_source: str | None = None,
    ):
        super().__init__(message)
        self.attempts = attempts or []
        self.last_available_date = last_available_date
        self.expected_date = expected_date
        self.cache_source = cache_source


def fetch_asset_data(
    asset: dict,
    config: dict,
    mode: str = "auto",
    project_root: Path | None = None,
    logger: logging.Logger | None = None,
    now: datetime | None = None,
) -> MarketBundle:
    if config.get("mock_mode", True):
        return _mock(asset, config)

    root = project_root or Path.cwd()
    current = now or market_now(config)
    return _ak(
        asset,
        config,
        mode=mode,
        project_root=root,
        logger=logger or logging.getLogger("etf_monitor"),
        now=current,
    )


def classify_data_exception(exc: Exception) -> str:
    name = type(exc).__name__
    message = str(exc).lower()
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in message:
        return "TIMEOUT"
    if isinstance(exc, requests.exceptions.SSLError) or "ssl" in message:
        return "SSL_ERROR"
    if isinstance(exc, requests.exceptions.ConnectionError):
        if "remote end closed connection" in message or "remotedisconnected" in message:
            return "REMOTE_DISCONNECTED"
        return "CONNECTION_ERROR"
    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        return f"HTTP_{response.status_code}" if response is not None else "HTTP_ERROR"
    if isinstance(exc, (json.JSONDecodeError, ValueError)) and "json" in message:
        return "JSON_PARSE_ERROR"
    if "no data" in message or "empty" in message:
        return "EMPTY_RESPONSE"
    if "unexpected columns" in message:
        return "SCHEMA_CHANGED"
    if "not found" in message or "invalid" in message:
        return "INVALID_SYMBOL_OR_RESPONSE"
    return name.upper()


def _redacted_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if key.lower() in {"authorization", "cookie", "set-cookie"}:
            safe[key] = "<redacted>"
        else:
            safe[key] = str(value)
    return safe


def _log_request_start(
    logger: logging.Logger | None,
    *,
    provider: str,
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
) -> None:
    if not logger:
        return
    logger.info(
        "HTTP request start | Provider=%s | Method=%s | URL=%s | Params=%s | Headers=%s",
        provider,
        method,
        url,
        params or {},
        _redacted_headers(headers),
    )


def _log_response(
    logger: logging.Logger | None,
    *,
    provider: str,
    response: requests.Response,
    elapsed_seconds: float,
) -> None:
    if not logger:
        return
    response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {
            "content-type",
            "content-length",
            "server",
            "date",
            "via",
            "x-cache",
        }
    }
    preview = response.text[:300].replace("\r", " ").replace("\n", " ")
    logger.info(
        "HTTP response | Provider=%s | Status=%s | FinalURL=%s | Elapsed=%.3fs | "
        "Bytes=%s | Headers=%s | BodyPreview=%r",
        provider,
        response.status_code,
        response.url,
        elapsed_seconds,
        len(response.content),
        response_headers,
        preview,
    )


def _eastmoney_secid(symbol: str) -> str:
    return f"{'1' if str(symbol).startswith(('5', '6', '9')) else '0'}.{symbol}"


def _fetch_history_direct_eastmoney(
    asset: dict,
    config: dict,
    now: datetime,
    logger: logging.Logger | None = None,
) -> pd.DataFrame:
    data_cfg = config["data"]
    history_days = int(data_cfg.get("history_days", 1000))
    start = now - timedelta(days=max(history_days * 2, 240))
    adjust = str(data_cfg.get("adjust", "qfq")).lower()
    fqt = {"": "0", "none": "0", "qfq": "1", "hfq": "2"}.get(adjust, "1")
    timeout = float(data_cfg.get("request_timeout_seconds", 20))

    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": _eastmoney_secid(str(asset["symbol"])),
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": fqt,
        "beg": start.strftime("%Y%m%d"),
        "end": now.strftime("%Y%m%d"),
        "lmt": str(max(history_days, 200)),
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://quote.eastmoney.com/",
        "Connection": "close",
    }

    _log_request_start(
        logger,
        provider="EastMoney direct historical",
        method="GET",
        url=url,
        params=params,
        headers=headers,
    )
    started = time.perf_counter()
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    elapsed = time.perf_counter() - started
    _log_response(
        logger,
        provider="EastMoney direct historical",
        response=response,
        elapsed_seconds=elapsed,
    )
    response.raise_for_status()

    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    klines = data.get("klines", []) if isinstance(data, dict) else []
    if not klines:
        raise RuntimeError("EastMoney direct historical endpoint returned empty response")

    rows = []
    for line in klines:
        fields = str(line).split(",")
        if len(fields) < 11:
            continue
        rows.append({
            "日期": fields[0],
            "开盘": fields[1],
            "收盘": fields[2],
            "最高": fields[3],
            "最低": fields[4],
            "成交量": fields[5],
            "成交额": fields[6],
            "涨跌幅": fields[8],
        })

    if not rows:
        raise RuntimeError("EastMoney direct historical endpoint returned unusable rows")
    return _normalize_history(pd.DataFrame(rows), history_days)


def explore_eastmoney_history_endpoints(
    asset: dict,
    config: dict,
    logger: logging.Logger | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now()
    symbol = str(asset.get("symbol", "")).strip()
    if not symbol:
        raise ValueError("Asset symbol is missing")

    data_cfg = config["data"]
    timeout = float(data_cfg.get("request_timeout_seconds", 20))
    history_days = int(data_cfg.get("history_days", 1000))
    start = current - timedelta(days=max(history_days * 2, 240))

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    )
    common_headers = {
        "User-Agent": user_agent,
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://quote.eastmoney.com/",
    }
    common_params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": start.strftime("%Y%m%d"),
        "end": current.strftime("%Y%m%d"),
        "lmt": str(max(history_days, 200)),
    }

    market_prefixes = ["1", "0"]
    endpoint_paths = [
        ("push2his-stock-kline", "https://push2his.eastmoney.com/api/qt/stock/kline/get"),
        ("push2his-fund-kline", "https://push2his.eastmoney.com/api/qt/fund/kline/get"),
        ("push2his-quote-kline", "https://push2his.eastmoney.com/api/qt/quote/kline/get"),
        ("push2-stock-kline", "https://push2.eastmoney.com/api/qt/stock/kline/get"),
    ]
    connection_modes = [
        ("keep-alive", {}),
        ("connection-close", {"Connection": "close"}),
    ]

    attempts: list[dict[str, Any]] = []

    for endpoint_name, url in endpoint_paths:
        for market_prefix in market_prefixes:
            for connection_name, extra_headers in connection_modes:
                headers = {**common_headers, **extra_headers}
                params = {**common_params, "secid": f"{market_prefix}.{symbol}"}
                started = time.perf_counter()
                attempt: dict[str, Any] = {
                    "endpoint_name": endpoint_name,
                    "url": url,
                    "secid": params["secid"],
                    "connection_mode": connection_name,
                    "method": "GET",
                    "params": params,
                    "headers": _redacted_headers(headers),
                }

                _log_request_start(
                    logger,
                    provider=f"API Explorer: {endpoint_name}",
                    method="GET",
                    url=url,
                    params=params,
                    headers=headers,
                )

                try:
                    response = requests.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=timeout,
                    )
                    elapsed = time.perf_counter() - started
                    _log_response(
                        logger,
                        provider=f"API Explorer: {endpoint_name}",
                        response=response,
                        elapsed_seconds=elapsed,
                    )

                    attempt.update({
                        "elapsed_seconds": round(elapsed, 3),
                        "http_status": response.status_code,
                        "final_url": response.url,
                        "response_bytes": len(response.content),
                        "content_type": response.headers.get("Content-Type"),
                        "server": response.headers.get("Server"),
                        "body_preview": response.text[:500].replace("\r", " ").replace("\n", " "),
                    })

                    response.raise_for_status()
                    payload = response.json()
                    data = payload.get("data") if isinstance(payload, dict) else None
                    klines = data.get("klines", []) if isinstance(data, dict) else []
                    attempt["json_ok"] = True
                    attempt["has_data"] = bool(data)
                    attempt["kline_count"] = len(klines)
                    attempt["parse_success"] = bool(klines)
                    attempt["ok"] = bool(klines)
                    attempt["message"] = (
                        f"成功解析{len(klines)}条K线"
                        if klines
                        else "HTTP和JSON正常，但未返回K线数据"
                    )
                except Exception as exc:
                    elapsed = time.perf_counter() - started
                    attempt.update({
                        "ok": False,
                        "elapsed_seconds": round(elapsed, 3),
                        "error_category": classify_data_exception(exc),
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    })
                    if logger:
                        logger.error(
                            "API explorer attempt failed | Endpoint=%s | Secid=%s | "
                            "ConnectionMode=%s | Category=%s | Error=%s",
                            endpoint_name,
                            params["secid"],
                            connection_name,
                            classify_data_exception(exc),
                            exc,
                        )
                        logger.debug(
                            "API explorer traceback | Endpoint=%s | Secid=%s\n%s",
                            endpoint_name,
                            params["secid"],
                            traceback.format_exc(),
                        )

                attempts.append(attempt)

    successful = [item for item in attempts if item.get("ok")]
    return {
        "symbol": symbol,
        "checked_at": current.isoformat(timespec="seconds"),
        "akshare_version": _safe_akshare_version(),
        "attempt_count": len(attempts),
        "success_count": len(successful),
        "successful_candidates": [
            {
                "endpoint_name": item["endpoint_name"],
                "url": item["url"],
                "secid": item["secid"],
                "connection_mode": item["connection_mode"],
                "http_status": item.get("http_status"),
                "kline_count": item.get("kline_count"),
                "elapsed_seconds": item.get("elapsed_seconds"),
            }
            for item in successful
        ],
        "attempts": attempts,
        "ok": bool(successful),
    }


def _safe_akshare_version() -> str:
    try:
        import akshare as ak
        return getattr(ak, "__version__", "unknown")
    except Exception:
        return "unavailable"

def diagnose_data_sources(
    asset: dict,
    config: dict,
    logger: logging.Logger | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now()
    result: dict[str, Any] = {
        "symbol": str(asset.get("symbol", "")),
        "checked_at": current.isoformat(timespec="seconds"),
        "call_chain": {
            "historical_primary": "AKShare fund_etf_hist_em → EastMoney",
            "historical_secondary": "Direct requests → EastMoney kline API",
            "realtime_primary": "AKShare fund_etf_spot_em → EastMoney",
            "intraday_primary": "AKShare fund_etf_hist_min_em → EastMoney",
        },
        "checks": [],
    }

    try:
        import akshare as ak
        result["akshare_version"] = getattr(ak, "__version__", "unknown")
    except Exception as exc:
        result["akshare_version"] = "unavailable"
        result["checks"].append({
            "name": "AKShare import",
            "ok": False,
            "category": classify_data_exception(exc),
            "message": f"{type(exc).__name__}: {exc}",
        })
        return result

    checks = [
        (
            "AKShare historical wrapper",
            "AKShare → EastMoney",
            lambda: _fetch_history(ak, asset, config, current, logger),
        ),
        (
            "EastMoney direct historical",
            "requests → EastMoney",
            lambda: _fetch_history_direct_eastmoney(asset, config, current, logger),
        ),
        (
            "AKShare realtime wrapper",
            "AKShare → EastMoney",
            lambda: _fetch_spot(ak, asset, current),
        ),
    ]

    for name, provider, operation in checks:
        started = time.perf_counter()
        try:
            value = operation()
            elapsed = time.perf_counter() - started
            if isinstance(value, tuple):
                value = value[0]
            rows = len(value) if hasattr(value, "__len__") else None
            result["checks"].append({
                "name": name,
                "provider": provider,
                "ok": True,
                "elapsed_seconds": round(elapsed, 3),
                "rows": rows,
                "message": "成功",
            })
        except Exception as exc:
            elapsed = time.perf_counter() - started
            if logger:
                logger.error(
                    "Data source diagnostic failed | Check=%s | Category=%s | Error=%s",
                    name,
                    classify_data_exception(exc),
                    exc,
                )
                logger.debug("Diagnostic traceback:\n%s", traceback.format_exc())
            result["checks"].append({
                "name": name,
                "provider": provider,
                "ok": False,
                "elapsed_seconds": round(elapsed, 3),
                "category": classify_data_exception(exc),
                "message": f"{type(exc).__name__}: {exc}",
            })

    result["ok"] = any(item.get("ok") for item in result["checks"])
    return result

def _provider_state_path(project_root: Path, config: dict) -> Path:
    relative = config.get("data", {}).get(
        "provider_state_file",
        "data/provider_state.json",
    )
    return project_root / relative


def _load_provider_state(project_root: Path, config: dict) -> dict[str, Any]:
    path = _provider_state_path(project_root, config)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save_provider_state(
    project_root: Path,
    config: dict,
    payload: dict[str, Any],
) -> None:
    path = _provider_state_path(project_root, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _history_api_success_today(
    project_root: Path,
    config: dict,
    now: datetime,
) -> bool:
    state = _load_provider_state(project_root, config)
    last_success = (
        state.get("eastmoney_history", {})
        .get("last_success")
    )
    if not last_success:
        return False
    try:
        return datetime.fromisoformat(str(last_success)).date() == now.date()
    except ValueError:
        return False

def _history_circuit_status(
    project_root: Path,
    config: dict,
    now: datetime,
) -> tuple[bool, str | None]:
    state = _load_provider_state(project_root, config)
    record = state.get("eastmoney_history", {})
    blocked_until = record.get("blocked_until")
    if not blocked_until:
        return False, None
    try:
        until = datetime.fromisoformat(str(blocked_until))
    except ValueError:
        return False, None
    if now < until:
        return True, until.isoformat(timespec="seconds")
    return False, None


def _open_history_circuit(
    project_root: Path,
    config: dict,
    now: datetime,
    reason: str,
    errors: list[str],
) -> None:
    hours = float(
        config.get("data", {}).get(
            "history_circuit_breaker_hours",
            6,
        )
    )
    state = _load_provider_state(project_root, config)
    state["eastmoney_history"] = {
        "blocked_until": (now + timedelta(hours=hours)).isoformat(
            timespec="seconds"
        ),
        "reason": reason,
        "last_failure": now.isoformat(timespec="seconds"),
        "errors": errors[-6:],
    }
    _save_provider_state(project_root, config, state)


def _close_history_circuit(
    project_root: Path,
    config: dict,
    now: datetime,
) -> None:
    state = _load_provider_state(project_root, config)
    state["eastmoney_history"] = {
        "blocked_until": None,
        "reason": None,
        "last_success": now.isoformat(timespec="seconds"),
        "errors": [],
    }
    _save_provider_state(project_root, config, state)


def _looks_like_antibot(errors: list[str]) -> bool:
    joined = " ".join(errors).upper()
    return (
        "REMOTE_DISCONNECTED" in joined
        or "ERR_EMPTY_RESPONSE" in joined
        or "ANTI_BOT_CHALLENGE" in joined
    )


def _previous_business_date(now: datetime) -> pd.Timestamp:
    return (
        pd.Timestamp(now.date()) - pd.tseries.offsets.BDay(1)
    ).normalize()


def _cache_covers_previous_business_day(
    history: pd.DataFrame,
    now: datetime,
) -> tuple[bool, str, str]:
    latest = pd.Timestamp(history["date"].max()).normalize()
    required = _previous_business_date(now)
    return (
        latest >= required,
        latest.strftime("%Y-%m-%d"),
        required.strftime("%Y-%m-%d"),
    )


def refresh_history_cache_now(
    asset: dict,
    config: dict,
    project_root: Path,
    logger: logging.Logger,
    now: datetime,
) -> dict[str, Any]:
    import akshare as ak

    retry_cfg = {"max_attempts": 1, "delays_seconds": []}
    history, errors = _retry_call(
        lambda: _fetch_history(ak, asset, config, now, logger),
        retry_cfg,
        "manual-history-refresh-akshare",
        logger,
    )
    if history is None:
        history, direct_errors = _retry_call(
            lambda: _fetch_history_direct_eastmoney(
                asset,
                config,
                now,
                logger,
            ),
            retry_cfg,
            "manual-history-refresh-direct",
            logger,
        )
        errors.extend(direct_errors)

    if history is None:
        if _looks_like_antibot(errors):
            _open_history_circuit(
                project_root,
                config,
                now,
                "ANTI_BOT_CHALLENGE",
                errors,
            )
        return {
            "success": False,
            "message": "历史数据刷新失败，可能仍处于东方财富风控限制中。",
            "errors": errors,
        }

    _save_history_cache(
        project_root,
        config,
        str(asset["symbol"]),
        history,
        now,
    )
    _close_history_circuit(project_root, config, now)
    latest = pd.Timestamp(history["date"].max()).strftime("%Y-%m-%d")
    return {
        "success": True,
        "message": f"历史缓存已刷新至{latest}。",
        "latest_date": latest,
    }

def _retry_call(
    operation: Callable[[], Any],
    retry_cfg: dict,
    label: str,
    logger: logging.Logger | None = None,
    before_attempt: Callable[[str, int], None] | None = None,
):
    delays = retry_cfg.get("delays_seconds", [5, 15, 30])
    max_attempts = int(retry_cfg.get("max_attempts", 3))
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        try:
            if before_attempt:
                before_attempt(label, attempt)
            if logger:
                logger.info("Data request | Source=%s | Attempt=%s/%s", label, attempt, max_attempts)
            result = operation()
            if logger:
                logger.info("Data request succeeded | Source=%s | Attempt=%s", label, attempt)
            return result, errors
        except Exception as exc:
            category = classify_data_exception(exc)
            message = (
                f"{label} attempt {attempt}: {category}: "
                f"{type(exc).__name__}: {exc}"
            )
            errors.append(message)
            if logger:
                logger.warning("Data request failed | %s", message)
                logger.debug(
                    "Data request traceback | Source=%s | Attempt=%s/%s\n%s",
                    label,
                    attempt,
                    max_attempts,
                    traceback.format_exc(),
                )
            if category == "REMOTE_DISCONNECTED":
                if logger:
                    logger.warning(
                        "Data retry stopped early | Source=%s | "
                        "Reason=possible anti-bot challenge",
                        label,
                    )
                break
            if attempt < max_attempts:
                delay = delays[min(attempt - 1, len(delays) - 1)] if delays else 5
                if logger:
                    logger.info("Data retry scheduled | Source=%s | Delay=%ss", label, delay)
                time.sleep(float(delay))

    return None, errors


class _RequestPacer:
    """Keep provider calls strictly serial and separated by a configurable pause."""

    def __init__(self, policy: dict, logger: logging.Logger | None = None):
        self.minimum = max(0.0, float(policy.get("minimum_interval_seconds", 0)))
        self.maximum = max(self.minimum, float(policy.get("maximum_interval_seconds", self.minimum)))
        self.logger = logger
        self._last_started: float | None = None

    def before_attempt(self, label: str, attempt: int) -> None:
        now_mono = time.monotonic()
        if self._last_started is not None:
            target = random.uniform(self.minimum, self.maximum)
            elapsed = now_mono - self._last_started
            wait = max(0.0, target - elapsed)
            if wait > 0:
                if self.logger:
                    self.logger.info(
                        "Provider pacing | Next=%s | Attempt=%s | Wait=%.2fs | Policy=%.1f-%.1fs",
                        label, attempt, wait, self.minimum, self.maximum,
                    )
                time.sleep(wait)
        self._last_started = time.monotonic()


def _intraday_cache_path(project_root: Path, symbol: str, target_date: datetime) -> Path:
    return project_root / "data" / "intraday" / str(symbol) / f"{target_date:%Y-%m-%d}.json"


def _load_intraday_cache(project_root: Path, symbol: str, target_date: datetime) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = _intraday_cache_path(project_root, symbol, target_date)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_intraday(), {}
    return _normalize_intraday(pd.DataFrame(doc.get("rows", []))), doc


def _save_intraday_cache(
    project_root: Path,
    symbol: str,
    target_date: datetime,
    frame: pd.DataFrame,
    *,
    period: str,
    source: str,
    now: datetime,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return _empty_intraday()
    path = _intraday_cache_path(project_root, symbol, target_date)
    existing, _ = _load_intraday_cache(project_root, symbol, target_date)
    merged = pd.concat([existing, _normalize_intraday(frame)], ignore_index=True)
    merged = (
        merged.dropna(subset=["time", "price"])
        .sort_values("time")
        .drop_duplicates(subset=["time"], keep="last")
        .reset_index(drop=True)
    )
    complete = bool(
        now.date() > target_date.date()
        or (now.date() == target_date.date() and now.time() >= datetime.strptime("15:00", "%H:%M").time())
    )
    rows = merged.copy()
    rows["time"] = pd.to_datetime(rows["time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    rows = rows.where(pd.notna(rows), None)
    payload = {
        "symbol": str(symbol),
        "date": target_date.strftime("%Y-%m-%d"),
        "period": str(period),
        "source": source,
        "complete": complete,
        "last_updated": now.isoformat(timespec="seconds"),
        "rows": rows.to_dict(orient="records"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return merged


# Compatibility wrappers retained for existing tests and older callers.
def _market_phase(now: datetime, mode: str) -> str:
    context = build_market_context(now)
    if mode == "close":
        return "AFTER_CLOSE"
    return context.phase


def _expected_history_date(now: datetime, phase: str) -> pd.Timestamp:
    context = build_market_context(now)
    # Tests may pass an explicit historical phase. Preserve that meaning.
    if phase != context.phase:
        context = type(context)(
            now=context.now,
            phase=phase,
            phase_label=phase,
            close_ready=(phase == "AFTER_CLOSE" and now.time() >= datetime.strptime("15:05", "%H:%M").time()),
            can_intraday=phase in SESSION_PHASES,
            can_close=(phase == "AFTER_CLOSE" and now.time() >= datetime.strptime("15:05", "%H:%M").time()),
        )
    return expected_history_date(now, context)


def _cache_path(project_root: Path, config: dict, symbol: str) -> Path:
    cache_dir = config.get("data", {}).get("cache_directory", "data/cache")
    return project_root / cache_dir / f"{symbol}_daily.json"


def _save_history_cache(
    project_root: Path,
    config: dict,
    symbol: str,
    history: pd.DataFrame,
    now: datetime,
) -> None:
    path = _cache_path(project_root, config, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)

    records = history.copy()
    records["date"] = records["date"].dt.strftime("%Y-%m-%d")
    payload = {
        "symbol": symbol,
        "cached_at": now.isoformat(timespec="seconds"),
        "rows": records.to_dict(orient="records"),
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _coerce_cached_frame(rows: list[dict]) -> pd.DataFrame | None:
    if not rows:
        return None
    frame = pd.DataFrame(rows)
    if "date" not in frame or "close" not in frame:
        return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in [
        "open",
        "close",
        "high",
        "low",
        "volume",
        "amount",
        "daily_change_pct",
    ]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return None if frame.empty else frame


def _load_history_cache(
    project_root: Path,
    config: dict,
    symbol: str,
) -> tuple[pd.DataFrame | None, str | None, str | None]:
    path = _cache_path(project_root, config, symbol)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            frame = _coerce_cached_frame(payload.get("rows", []))
            if frame is not None:
                frame, adjustment_notes = _adjust_unadjusted_history_for_splits(frame)
                if adjustment_notes:
                    payload["rows"] = frame.assign(
                        date=frame["date"].dt.strftime("%Y-%m-%d")
                    ).to_dict(orient="records")
                    payload["auto_adjustments"] = adjustment_notes
                    temp = path.with_suffix(path.suffix + ".tmp")
                    temp.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    temp.replace(path)
                    frame.attrs["adjustment_notes"] = adjustment_notes
                return frame, payload.get("cached_at"), str(path.relative_to(project_root))
        except Exception:
            pass

    latest_path = project_root / config.get("storage", {}).get("latest_file", "data/latest.json")
    if latest_path.exists():
        try:
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
            result = payload.get(str(symbol), {})
            chart_data = result.get("chart_data", [])
            if chart_data:
                frame = pd.DataFrame(chart_data).rename(columns={"price": "close"})
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
                frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
                frame = frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
                if not frame.empty:
                    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
                    frame["high"] = frame[["open", "close"]].max(axis=1)
                    frame["low"] = frame[["open", "close"]].min(axis=1)
                    frame["volume"] = 0.0
                    frame["amount"] = 0.0
                    frame["daily_change_pct"] = frame["close"].pct_change().fillna(0) * 100
                    return (
                        frame,
                        result.get("generated_at", "legacy latest.json"),
                        latest_path.relative_to(project_root).as_posix(),
                    )
        except Exception:
            pass

    return None, None, None


def _history_is_fresh_enough(
    history: pd.DataFrame,
    now: datetime,
    phase: str,
) -> tuple[bool, str, str]:
    context = build_market_context(now)
    if phase != context.phase:
        close_ready = phase == "AFTER_CLOSE" and now.time() >= datetime.strptime("15:05", "%H:%M").time()
        context = type(context)(
            now=now,
            phase=phase,
            phase_label=phase,
            close_ready=close_ready,
            can_intraday=phase in SESSION_PHASES,
            can_close=close_ready,
        )
    latest_date = pd.Timestamp(history["date"].max()).normalize()
    expected_date = expected_history_date(now, context).normalize()
    return (
        latest_date >= expected_date,
        latest_date.strftime("%Y-%m-%d"),
        expected_date.strftime("%Y-%m-%d"),
    )


def _normalize_history(df: pd.DataFrame, history_days: int) -> pd.DataFrame:
    renamed = df.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "daily_change_pct",
        }
    )
    if "date" not in renamed or "close" not in renamed:
        raise RuntimeError("Historical endpoint returned unexpected columns")

    renamed["date"] = pd.to_datetime(renamed["date"], errors="coerce")
    for column in ["open", "close", "high", "low", "volume", "amount", "daily_change_pct"]:
        if column in renamed:
            renamed[column] = pd.to_numeric(renamed[column], errors="coerce")

    normalized = (
        renamed.dropna(subset=["date", "close"])
        .sort_values("date")
        .tail(history_days)
        .reset_index(drop=True)
    )
    if normalized.empty:
        raise RuntimeError("Historical endpoint returned no usable rows")
    return normalized


def _fetch_history(
    ak,
    asset: dict,
    config: dict,
    now: datetime,
    logger: logging.Logger | None = None,
) -> pd.DataFrame:
    data_cfg = config["data"]
    history_days = int(data_cfg.get("history_days", 1000))
    start = now - timedelta(days=max(history_days * 2, 240))

    if logger:
        logger.info(
            "Provider call | Layer=AKShare | Function=fund_etf_hist_em | "
            "UnderlyingProvider=EastMoney | Symbol=%s | Start=%s | End=%s | Adjust=%s",
            asset["symbol"],
            start.strftime("%Y%m%d"),
            now.strftime("%Y%m%d"),
            data_cfg.get("adjust", "qfq"),
        )
    frame = ak.fund_etf_hist_em(
        symbol=asset["symbol"],
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=now.strftime("%Y%m%d"),
        adjust=data_cfg.get("adjust", "qfq"),
    )
    if frame is None or frame.empty:
        raise RuntimeError("Historical ETF endpoint returned no data")
    return _normalize_history(frame, history_days)



def _adjust_unadjusted_history_for_splits(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Back-adjust obvious ETF share splits in an unadjusted daily series.

    The fallback provider may return raw prices across a share split.  A raw
    series would make a split look like a 50%+ market crash and would poison
    drawdown, moving-average and strategy calculations.  This routine only
    adjusts discontinuities that are very close to common split ratios and
    whose open and close imply the same ratio.
    """
    if frame is None or frame.empty or len(frame) < 2:
        return frame, []

    adjusted = frame.copy().sort_values("date").reset_index(drop=True)
    price_columns = [name for name in ("open", "high", "low", "close") if name in adjusted]
    common_ratios = np.array([1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0], dtype=float)
    notes: list[str] = []

    # Walk forward.  When a split is found, modify all earlier rows so the
    # entire series remains on the latest share-unit basis.
    for idx in range(1, len(adjusted)):
        prev_close = float(pd.to_numeric(adjusted.at[idx - 1, "close"], errors="coerce"))
        curr_close = float(pd.to_numeric(adjusted.at[idx, "close"], errors="coerce"))
        curr_open = float(pd.to_numeric(adjusted.at[idx, "open"], errors="coerce")) if "open" in adjusted else curr_close
        if not np.isfinite(prev_close) or not np.isfinite(curr_close) or not np.isfinite(curr_open):
            continue
        if min(prev_close, curr_close, curr_open) <= 0:
            continue

        raw_change = curr_close / prev_close - 1.0
        if abs(raw_change) < 0.30:
            continue

        downward = prev_close > curr_close
        close_ratio = prev_close / curr_close if downward else curr_close / prev_close
        open_ratio = prev_close / curr_open if downward else curr_open / prev_close
        nearest = float(common_ratios[np.argmin(np.abs(common_ratios - close_ratio))])
        close_error = abs(close_ratio / nearest - 1.0)
        open_error = abs(open_ratio / nearest - 1.0)
        if close_error > 0.06 or open_error > 0.08:
            continue

        prior = adjusted.index < idx
        if downward:
            adjusted.loc[prior, price_columns] = adjusted.loc[prior, price_columns] / nearest
            if "volume" in adjusted:
                adjusted.loc[prior, "volume"] = adjusted.loc[prior, "volume"] * nearest
            action = f"按1:{nearest:g}份额拆分向后复权"
        else:
            adjusted.loc[prior, price_columns] = adjusted.loc[prior, price_columns] * nearest
            if "volume" in adjusted:
                adjusted.loc[prior, "volume"] = adjusted.loc[prior, "volume"] / nearest
            action = f"按{nearest:g}:1份额合并向后复权"

        event_date = pd.Timestamp(adjusted.at[idx, "date"]).strftime("%Y-%m-%d")
        notes.append(f"检测到{event_date}附近疑似份额变更，已{action}")

    if notes:
        adjusted["daily_change_pct"] = adjusted["close"].pct_change().fillna(0) * 100
        if "amount" in adjusted and "volume" in adjusted:
            # Preserve provider amount when available; fill only missing values.
            estimated = adjusted["volume"].fillna(0) * adjusted["close"].fillna(0)
            adjusted["amount"] = adjusted["amount"].where(adjusted["amount"].notna(), estimated)
    return adjusted, notes


def _fetch_history_sina(
    ak,
    asset: dict,
    config: dict,
    logger: logging.Logger | None = None,
) -> pd.DataFrame:
    symbol = str(asset["symbol"])
    market_symbol = (
        f"sh{symbol}"
        if symbol.startswith(("5", "6", "9"))
        else f"sz{symbol}"
    )
    if logger:
        logger.info(
            "Provider call | Layer=AKShare | Function=fund_etf_hist_sina | "
            "UnderlyingProvider=Sina | Symbol=%s",
            market_symbol,
        )
    frame = ak.fund_etf_hist_sina(symbol=market_symbol)
    if frame is None or frame.empty:
        raise RuntimeError("Sina ETF history endpoint returned no data")

    frame = frame.rename(
        columns={
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
    ).copy()
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise RuntimeError("Sina ETF history endpoint returned unexpected columns")

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date")
    frame["amount"] = frame["volume"].fillna(0) * frame["close"]
    frame, adjustment_notes = _adjust_unadjusted_history_for_splits(frame)
    frame["daily_change_pct"] = frame["close"].pct_change().fillna(0) * 100
    history_days = int(config.get("data", {}).get("history_days", 1000))
    normalized = frame.tail(history_days).reset_index(drop=True)
    normalized.attrs["adjustment_notes"] = adjustment_notes
    if normalized.empty:
        raise RuntimeError("Sina ETF history endpoint returned no usable rows")
    return normalized

def _optional_number(row: pd.Series, key: str) -> float | None:
    value = pd.to_numeric(row.get(key), errors="coerce")
    if pd.isna(value):
        return None
    return float(value)


def _fetch_spot(ak, asset: dict, now: datetime | None = None) -> tuple[dict, list[str]]:
    spot_frame = ak.fund_etf_spot_em()
    if spot_frame is None or spot_frame.empty:
        raise RuntimeError("ETF spot endpoint returned no data")
    if "代码" not in spot_frame:
        raise RuntimeError("ETF spot endpoint returned unexpected columns")

    matches = spot_frame.loc[spot_frame["代码"].astype(str) == str(asset["symbol"])]
    if matches.empty:
        raise RuntimeError(f"ETF {asset['symbol']} not found in spot endpoint")

    row = matches.iloc[0]
    price = _optional_number(row, "最新价")
    if price is None or price <= 0:
        raise RuntimeError("Realtime field 最新价 is missing")

    warnings: list[str] = []
    mapping = {
        "daily_change_pct": "涨跌幅",
        "open": "今开",
        "high": "最高",
        "low": "最低",
        "previous_close": "昨收",
        "volume": "成交量",
        "amount": "成交额",
    }
    values: dict[str, float | None] = {}
    for target, source in mapping.items():
        values[target] = _optional_number(row, source)
        if values[target] is None:
            warnings.append(f"Realtime optional field {source} is missing; fallback will be used")

    return {
        "timestamp": now or datetime.now(),
        "price": price,
        **values,
    }, warnings


def _empty_intraday() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["time", "price", "volume", "amount", "open", "high", "low"]
    )


def _normalize_intraday(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return _empty_intraday()

    frame = frame.rename(
        columns={
            "时间": "time",
            "收盘": "price",
            "最新价": "price",
            "成交量": "volume",
            "成交额": "amount",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
        }
    ).copy()

    if "time" not in frame or "price" not in frame:
        return _empty_intraday()

    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    for column in ["price", "volume", "amount", "open", "high", "low"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        else:
            frame[column] = np.nan

    return (
        frame.dropna(subset=["time", "price"])
        .sort_values("time")
        .reset_index(drop=True)
    )


def _fetch_intraday_for_date(
    ak,
    asset: dict,
    target_date: datetime,
    period: str = "1",
) -> pd.DataFrame:
    date_text = target_date.strftime("%Y-%m-%d")
    frame = ak.fund_etf_hist_min_em(
        symbol=asset["symbol"],
        start_date=date_text + " 09:30:00",
        end_date=date_text + " 15:00:00",
        period=str(period),
        adjust="",
    )
    normalized = _normalize_intraday(frame)
    if normalized.empty:
        raise RuntimeError(
            f"ETF minute endpoint returned no usable data for {date_text}"
        )
    return normalized


def _fetch_intraday_stock_for_date(
    ak,
    asset: dict,
    target_date: datetime,
    period: str = "1",
) -> pd.DataFrame:
    date_text = target_date.strftime("%Y-%m-%d")
    frame = ak.stock_zh_a_hist_min_em(
        symbol=str(asset["symbol"]),
        start_date=date_text + " 09:30:00",
        end_date=date_text + " 15:00:00",
        period=str(period),
        adjust="",
    )
    normalized = _normalize_intraday(frame)
    if normalized.empty:
        raise RuntimeError(
            f"Stock minute fallback returned no usable data for {date_text}"
        )
    return normalized



def _fetch_intraday_sina_for_date(
    ak,
    asset: dict,
    target_date: datetime,
    period: str = "1",
) -> pd.DataFrame:
    """Fetch recent minute bars through AKShare's Sina minute endpoint.

    This is deliberately independent from the EastMoney minute wrappers and
    gives the report a real cross-provider fallback when EastMoney is blocked.
    """
    if not hasattr(ak, "stock_zh_a_minute"):
        raise RuntimeError("Sina minute fallback is unavailable in this AKShare version")
    symbol = str(asset["symbol"])
    market_symbol = f"sh{symbol}" if symbol.startswith(("5", "6", "9")) else f"sz{symbol}"
    frame = ak.stock_zh_a_minute(
        symbol=market_symbol,
        period=str(period),
        adjust="",
    )
    if frame is None or frame.empty:
        raise RuntimeError("Sina minute fallback returned no data")
    frame = frame.rename(columns={
        "day": "time",
        "date": "time",
        "close": "price",
    }).copy()
    normalized = _normalize_intraday(frame)
    target = pd.Timestamp(target_date.date())
    if not normalized.empty:
        normalized = normalized.loc[
            pd.to_datetime(normalized["time"], errors="coerce").dt.normalize() == target
        ].reset_index(drop=True)
    if normalized.empty:
        raise RuntimeError(
            f"Sina minute fallback returned no usable data for {target:%Y-%m-%d}"
        )
    return normalized


def _build_daily_ohlc_approximation(
    history: pd.DataFrame,
    target_date: datetime,
) -> pd.DataFrame:
    target = pd.Timestamp(target_date.date())
    dates = pd.to_datetime(
        history["date"],
        errors="coerce",
    ).dt.normalize()
    rows = history.loc[dates == target]
    if rows.empty:
        return _empty_intraday()

    row = rows.iloc[-1]
    values = [
        ("09:30", float(row["open"])),
        ("10:30", float(row["high"])),
        ("13:30", float(row["low"])),
        ("15:00", float(row["close"])),
    ]
    return pd.DataFrame([
        {
            "time": datetime.combine(
                target.date(),
                datetime.strptime(clock, "%H:%M").time(),
            ),
            "price": price,
            "volume": 0.0,
            "amount": 0.0,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
        }
        for clock, price in values
    ])

def _fetch_intraday(ak, asset: dict, now: datetime) -> pd.DataFrame:
    return _fetch_intraday_for_date(ak, asset, now)


def _fetch_intraday_stock_wrapper(ak, asset: dict, now: datetime) -> pd.DataFrame:
    return _fetch_intraday_stock_for_date(ak, asset, now)


def _build_intraday_snapshot_approximation(
    spot: dict | None,
    now: datetime,
) -> pd.DataFrame:
    """Build a clearly marked two-point approximation when minute data is unavailable."""
    if not spot or not spot.get("price"):
        return _empty_intraday()

    current_price = float(spot["price"])
    open_price = float(spot.get("open") or current_price)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    current_time = spot.get("timestamp") or now
    if not isinstance(current_time, datetime):
        current_time = now
    if current_time < market_open:
        current_time = market_open

    return pd.DataFrame(
        [
            {
                "time": market_open,
                "price": open_price,
                "volume": 0.0,
                "amount": 0.0,
                "open": open_price,
                "high": max(open_price, current_price),
                "low": min(open_price, current_price),
            },
            {
                "time": current_time,
                "price": current_price,
                "volume": float(spot.get("volume") or 0),
                "amount": float(spot.get("amount") or 0),
                "open": open_price,
                "high": float(spot.get("high") or max(open_price, current_price)),
                "low": float(spot.get("low") or min(open_price, current_price)),
            },
        ]
    )


def _derive_spot_from_intraday(
    intraday: pd.DataFrame,
    history: pd.DataFrame,
    now: datetime,
) -> dict | None:
    if intraday is None or intraday.empty:
        return None
    last = intraday.iloc[-1]
    first = intraday.iloc[0]
    price = float(last["price"])
    previous_close = float(history.iloc[-1]["close"]) if not history.empty else None
    open_value = _optional_number(first, "open") or float(first["price"])
    high_value = (
        float(pd.to_numeric(intraday.get("high"), errors="coerce").max())
        if "high" in intraday and pd.to_numeric(intraday["high"], errors="coerce").notna().any()
        else float(intraday["price"].max())
    )
    low_value = (
        float(pd.to_numeric(intraday.get("low"), errors="coerce").min())
        if "low" in intraday and pd.to_numeric(intraday["low"], errors="coerce").notna().any()
        else float(intraday["price"].min())
    )
    volume = (
        float(pd.to_numeric(intraday["volume"], errors="coerce").fillna(0).sum())
        if "volume" in intraday
        else 0.0
    )
    amount = (
        float(pd.to_numeric(intraday["amount"], errors="coerce").fillna(0).sum())
        if "amount" in intraday
        else 0.0
    )
    daily_change = (
        (price / previous_close - 1) * 100
        if previous_close and previous_close > 0
        else 0.0
    )
    timestamp = pd.Timestamp(last["time"]).to_pydatetime()
    return {
        "timestamp": timestamp if timestamp else now,
        "price": price,
        "daily_change_pct": daily_change,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "previous_close": previous_close,
        "volume": volume,
        "amount": amount,
    }


def _enrich_spot(
    spot: dict,
    history: pd.DataFrame,
    intraday: pd.DataFrame,
) -> tuple[dict, list[str]]:
    enriched = dict(spot)
    warnings: list[str] = []
    price = float(enriched["price"])
    previous_history_close = float(history.iloc[-1]["close"]) if not history.empty else None

    if enriched.get("previous_close") is None:
        enriched["previous_close"] = previous_history_close
        warnings.append("昨收缺失，已使用最近历史收盘价")

    if enriched.get("daily_change_pct") is None:
        previous_close = enriched.get("previous_close")
        enriched["daily_change_pct"] = (
            (price / previous_close - 1) * 100
            if previous_close and previous_close > 0
            else 0.0
        )
        warnings.append("涨跌幅缺失，已根据最新价和昨收计算")

    minute_open = None
    minute_high = None
    minute_low = None
    if intraday is not None and not intraday.empty:
        first = intraday.iloc[0]
        minute_open = _optional_number(first, "open") or float(first["price"])
        minute_high = float(intraday["price"].max())
        minute_low = float(intraday["price"].min())

    if enriched.get("open") is None:
        enriched["open"] = minute_open if minute_open is not None else price
        warnings.append("今开缺失，已使用分时首价或最新价")
    if enriched.get("high") is None:
        enriched["high"] = minute_high if minute_high is not None else max(price, enriched["open"])
        warnings.append("最高价缺失，已使用分时最高价或最新价")
    if enriched.get("low") is None:
        enriched["low"] = minute_low if minute_low is not None else min(price, enriched["open"])
        warnings.append("最低价缺失，已使用分时最低价或最新价")
    if enriched.get("volume") is None:
        enriched["volume"] = 0.0
        warnings.append("成交量缺失，已按0处理")
    if enriched.get("amount") is None:
        enriched["amount"] = 0.0
        warnings.append("成交额缺失，已按0处理")

    return enriched, warnings


def _merge_spot_into_history(
    history: pd.DataFrame,
    spot: dict,
    *,
    provisional: bool = False,
) -> pd.DataFrame:
    data = history.copy()
    today = pd.Timestamp(spot["timestamp"]).normalize()
    same_day = data["date"].dt.normalize() == today
    row = {
        "date": today,
        "open": float(spot["open"]),
        "close": float(spot["price"]),
        "high": float(spot["high"]),
        "low": float(spot["low"]),
        "volume": float(spot["volume"] or 0),
        "amount": float(spot["amount"] or 0),
        "daily_change_pct": float(spot["daily_change_pct"] or 0),
        "provisional": bool(provisional),
        "source": (
            "realtime_close_snapshot"
            if provisional
            else "realtime_intraday_snapshot"
        ),
    }
    if same_day.any():
        index = data.index[same_day][-1]
        for key, value in row.items():
            data.loc[index, key] = value
    else:
        data = pd.concat([data, pd.DataFrame([row])], ignore_index=True)
    return data.sort_values("date").reset_index(drop=True)


def _build_latest(asset: dict, history: pd.DataFrame, spot: dict | None) -> MarketSnapshot:
    last = history.iloc[-1]
    if spot is not None:
        return MarketSnapshot(
            asset["symbol"],
            asset["name"],
            spot["timestamp"],
            float(spot["price"]),
            float(spot["daily_change_pct"] or 0),
            float(spot["volume"] or 0),
            float(spot["amount"] or 0),
            None,
        )

    if "daily_change_pct" in history and pd.notna(last.get("daily_change_pct")):
        daily_change = float(last["daily_change_pct"])
    elif len(history) >= 2:
        daily_change = float((last["close"] / history.iloc[-2]["close"] - 1) * 100)
    else:
        daily_change = 0.0
    return MarketSnapshot(
        asset["symbol"],
        asset["name"],
        pd.Timestamp(last["date"]).to_pydatetime(),
        float(last["close"]),
        daily_change,
        float(last.get("volume", 0) or 0),
        float(last.get("amount", 0) or 0),
        None,
    )


def _ak(
    asset: dict,
    config: dict,
    mode: str,
    project_root: Path,
    logger: logging.Logger,
    now: datetime,
) -> MarketBundle:
    import akshare as ak

    context = build_market_context(now)
    retry_cfg = config.get("data", {}).get("retry", {})
    request_policy = config.get("data", {}).get("request_policy", {})
    pacer = _RequestPacer(request_policy, logger)
    realtime_required = should_request_realtime(mode, context)
    close_snapshot_allowed = (
        context.phase == "AFTER_CLOSE"
        and context.close_ready
    )

    status: dict[str, Any] = {
        "history": {
            "status": "PENDING",
            "source": None,
            "api_requested": False,
            "latest_date": None,
            "message": "尚未处理历史日线",
            "errors": [],
        },
        "realtime": {
            "status": "NOT_REQUIRED",
            "source": None,
            "timestamp": None,
            "message": "当前阶段不需要实时快照",
            "errors": [],
        },
        "intraday": {
            "status": "NOT_REQUIRED",
            "source": None,
            "points": 0,
            "approximate": False,
            "message": "当前阶段不需要今日分时",
            "errors": [],
        },
        "previous_intraday": {
            "status": "PENDING",
            "source": None,
            "points": 0,
            "approximate": False,
            "date": None,
            "message": "尚未获取昨日走势",
            "errors": [],
        },
    }

    logger.info(
        "Market context | Mode=%s | Phase=%s | Label=%s | "
        "Realtime=%s | CloseSnapshotAllowed=%s | Time=%s",
        mode,
        context.phase,
        context.phase_label,
        realtime_required,
        close_snapshot_allowed,
        now.strftime("%Y-%m-%d %H:%M:%S"),
    )

    cached_history, cached_at, cache_source = _load_history_cache(
        project_root,
        config,
        asset["symbol"],
    )
    cache_covers_previous = False
    cache_latest_date = None
    cache_required_date = None
    if cached_history is not None:
        (
            cache_covers_previous,
            cache_latest_date,
            cache_required_date,
        ) = _cache_covers_previous_business_day(
            cached_history,
            now,
        )

    circuit_open, blocked_until = _history_circuit_status(
        project_root,
        config,
        now,
    )
    history_success_today = _history_api_success_today(
        project_root,
        config,
        now,
    )

    history = None
    history_provider = None
    history_errors: list[str] = []
    using_local_cache = False
    cache_date = None
    data_sources: list[str] = []
    warnings: list[str] = []

    cache_fresh_for_phase = False
    if cached_history is not None:
        cache_fresh_for_phase, _, _ = _history_is_fresh_enough(
            cached_history,
            now,
            context.phase,
        )

    # Before the market is close-ready, a date-valid cache is the primary source.
    # This prevents pre-open and intraday runs from touching the wind-controlled
    # EastMoney history endpoint without a real need.
    if history_success_today and cached_history is not None and (
        (realtime_required and cache_covers_previous)
        or (not context.close_ready and cache_fresh_for_phase)
    ):
        history = cached_history
        using_local_cache = True
        cache_date = cache_latest_date
        history_provider = f"本地日线缓存({cache_source})"
        status["history"].update({
            "status": "CACHE",
            "source": history_provider,
            "api_requested": False,
            "latest_date": cache_latest_date,
            "message": (
                f"今天已成功刷新过历史接口；缓存内最新交易日="
                f"{cache_latest_date}，盘中只获取实时行情"
                if realtime_required
                else (
                    f"今天已成功刷新过历史接口；缓存内最新交易日="
                    f"{cache_latest_date}，当前盘前要求>={cache_required_date}，"
                    "未再次请求历史接口"
                )
            ),
        })
        warnings.append(status["history"]["message"])
        logger.info(
            "Historical request skipped | Reason=cache_first | "
            "Latest=%s | RequiredPrevious=%s | Phase=%s",
            cache_latest_date,
            cache_required_date,
            context.phase,
        )
    elif circuit_open and cached_history is not None:
        history = cached_history
        using_local_cache = True
        cache_date = cache_latest_date
        history_provider = f"本地日线缓存({cache_source})"
        status["history"].update({
            "status": "CACHE_CIRCUIT",
            "source": history_provider,
            "api_requested": False,
            "latest_date": cache_latest_date,
            "message": f"历史接口熔断至{blocked_until}，使用本地缓存",
        })
        warnings.append(status["history"]["message"])
    else:
        status["history"]["api_requested"] = True
        if not history_success_today:
            logger.info(
                "Historical API requested | Reason=no successful history refresh today"
            )
        providers = [
            (
                "AKShare历史日线(EastMoney)",
                "historical-akshare-eastmoney",
                lambda: _fetch_history(ak, asset, config, now, logger),
                retry_cfg,
            ),
            (
                "东方财富直接历史日线",
                "historical-direct-eastmoney",
                lambda: _fetch_history_direct_eastmoney(
                    asset, config, now, logger
                ),
                {"max_attempts": 1, "delays_seconds": []},
            ),
            (
                "AKShare历史日线(Sina)",
                "historical-akshare-sina",
                lambda: _fetch_history_sina(ak, asset, config, logger),
                {"max_attempts": 1, "delays_seconds": []},
            ),
        ]
        for provider_name, label, operation, provider_retry in providers:
            history, errors = _retry_call(
                operation,
                provider_retry,
                label,
                logger,
                before_attempt=pacer.before_attempt,
            )
            history_errors.extend(errors)
            if history is not None:
                history_provider = provider_name
                break

        if history is not None:
            if history_provider == "AKShare历史日线(Sina)":
                adjustment_notes = list(history.attrs.get("adjustment_notes", []))
                if adjustment_notes:
                    warnings.append(
                        "历史日线已切换到新浪备用源；程序检测并修正了疑似份额拆分，"
                        "避免未复权价格跳变污染收益率和回撤计算"
                    )
                    warnings.extend(adjustment_notes)
                else:
                    warnings.append(
                        "历史日线已切换到新浪备用源；该备用源为未复权日线，"
                        "报告会明确标记数据来源"
                    )
            _save_history_cache(
                project_root,
                config,
                asset["symbol"],
                history,
                now,
            )
            _close_history_circuit(project_root, config, now)
            latest_date = pd.Timestamp(
                history["date"].max()
            ).strftime("%Y-%m-%d")
            status["history"].update({
                "status": "SUCCESS",
                "source": history_provider,
                "latest_date": latest_date,
                "message": f"历史日线获取成功，最新日期{latest_date}",
                "errors": history_errors,
            })
        else:
            if _looks_like_antibot(history_errors):
                _open_history_circuit(
                    project_root,
                    config,
                    now,
                    "ANTI_BOT_CHALLENGE",
                    history_errors,
                )
                warnings.append(
                    "东方财富历史接口疑似触发风控；已尝试新浪日线备用源"
                )

            if cached_history is None:
                raise DataFetchError(
                    f"Historical data unavailable for {asset['symbol']} and no local cache exists",
                    attempts=history_errors,
                )

            if close_snapshot_allowed and cache_covers_previous:
                history = cached_history
                using_local_cache = True
                cache_date = cache_latest_date
                history_provider = f"本地日线缓存({cache_source})"
                status["history"].update({
                    "status": "CACHE_PROVISIONAL_CLOSE",
                    "source": history_provider,
                    "latest_date": cache_latest_date,
                    "message": "正式日线不可用；将用实时收盘快照补齐今日",
                    "errors": history_errors,
                })
                warnings.append(status["history"]["message"])
            else:
                fresh, latest_date, expected_date = _history_is_fresh_enough(
                    cached_history,
                    now,
                    context.phase,
                )
                if not fresh:
                    raise DataFetchError(
                        (
                            f"Only stale local data is available for "
                            f"{asset['symbol']}: latest={latest_date}, "
                            f"expected>={expected_date}"
                        ),
                        attempts=history_errors,
                        last_available_date=latest_date,
                        expected_date=expected_date,
                        cache_source=cache_source,
                    )
                history = cached_history
                using_local_cache = True
                cache_date = latest_date
                history_provider = f"本地日线缓存({cache_source})"
                status["history"].update({
                    "status": "CACHE_FALLBACK",
                    "source": history_provider,
                    "latest_date": latest_date,
                    "message": "网络历史源失败，但本地缓存日期符合要求",
                    "errors": history_errors,
                })
                warnings.append(status["history"]["message"])

    if history is None:
        raise DataFetchError(
            f"No usable history data for {asset['symbol']}",
            attempts=history_errors,
        )

    if status["history"]["latest_date"] is None:
        status["history"]["latest_date"] = pd.Timestamp(
            history["date"].max()
        ).strftime("%Y-%m-%d")
    cached_adjustment_notes = list(history.attrs.get("adjustment_notes", []))
    if cached_adjustment_notes and history_provider != "AKShare历史日线(Sina)":
        warnings.append(
            "本地历史缓存中检测到疑似份额拆分造成的未复权跳变，"
            "已自动按最新份额口径修正后再计算指标"
        )
        warnings.extend(cached_adjustment_notes)
    data_sources.append(history_provider or "历史日线")

    spot = None
    intraday = _empty_intraday()
    previous_intraday = _empty_intraday()
    spot_errors: list[str] = []
    intraday_errors: list[str] = []

    need_snapshot = realtime_required or (
        close_snapshot_allowed
        and using_local_cache
        and pd.Timestamp(history["date"].max()).normalize()
        < pd.Timestamp(now.date())
    )

    if need_snapshot:
        status["realtime"].update({
            "status": "PENDING",
            "message": "正在获取实时快照",
        })
        spot_result, spot_errors = _retry_call(
            lambda: _fetch_spot(ak, asset, now),
            retry_cfg,
            "realtime",
            logger,
            before_attempt=pacer.before_attempt,
        )
        if spot_result is not None:
            spot, field_warnings = spot_result
            warnings.extend(field_warnings)
            status["realtime"].update({
                "status": "SUCCESS",
                "source": "AKShare fund_etf_spot_em(EastMoney)",
                "timestamp": spot["timestamp"].isoformat(timespec="seconds"),
                "message": "实时快照获取成功",
                "errors": spot_errors,
            })

        # Minute data is requested strictly serially and falls back by period.
        periods = [str(item) for item in request_policy.get("intraday_periods", ["1", "5", "15"])]
        cached_today, cached_today_meta = _load_intraday_cache(
            project_root, str(asset["symbol"]), now
        )
        minute_attempts = []
        for period in periods:
            minute_attempts.extend([
                (
                    f"AKShare fund_etf_hist_min_em({period}m)",
                    f"intraday-etf-{period}m",
                    period,
                    lambda period=period: _fetch_intraday_for_date(ak, asset, now, period),
                ),
                (
                    f"AKShare stock_zh_a_hist_min_em({period}m)",
                    f"intraday-stock-{period}m",
                    period,
                    lambda period=period: _fetch_intraday_stock_for_date(ak, asset, now, period),
                ),
                (
                    f"AKShare stock_zh_a_minute(Sina,{period}m)",
                    f"intraday-sina-{period}m",
                    period,
                    lambda period=period: _fetch_intraday_sina_for_date(ak, asset, now, period),
                ),
            ])
        for source_name, label, period, operation in minute_attempts:
            intraday_result, errors = _retry_call(
                operation,
                {"max_attempts": int(request_policy.get("same_endpoint_max_attempts", 1)),
                 "delays_seconds": [float(request_policy.get("retry_delay_seconds", 30))]},
                label,
                logger,
                before_attempt=pacer.before_attempt,
            )
            intraday_errors.extend(errors)
            if intraday_result is not None and not intraday_result.empty:
                intraday = _save_intraday_cache(
                    project_root, str(asset["symbol"]), now, intraday_result,
                    period=period, source=source_name, now=now,
                )
                status["intraday"].update({
                    "status": "SUCCESS",
                    "source": source_name,
                    "period": period,
                    "points": len(intraday),
                    "approximate": False,
                    "message": f"今日{period}分钟分时获取成功，共{len(intraday)}个点",
                    "errors": intraday_errors,
                })
                break
        if intraday.empty and not cached_today.empty:
            intraday = cached_today
            status["intraday"].update({
                "status": "CACHE",
                "source": "本地分时缓存",
                "period": str(cached_today_meta.get("period", "1")),
                "points": len(intraday),
                "approximate": False,
                "message": f"网络分时不可用，使用今日已保存的本地分时，共{len(intraday)}个点",
                "errors": intraday_errors,
            })

        if spot is None:
            spot = _derive_spot_from_intraday(intraday, history, now)
            if spot is not None:
                status["realtime"].update({
                    "status": "FALLBACK_FROM_MINUTE",
                    "source": status["intraday"].get("source"),
                    "timestamp": spot["timestamp"].isoformat(timespec="seconds"),
                    "message": "实时快照失败，使用分时最后一笔数据",
                    "errors": spot_errors,
                })
                warnings.append(status["realtime"]["message"])

        if spot is None:
            latest_date = pd.Timestamp(
                history["date"].max()
            ).strftime("%Y-%m-%d")
            expected_date = expected_history_date(
                now,
                context,
            ).strftime("%Y-%m-%d")
            raise DataFetchError(
                f"Realtime data unavailable for {asset['symbol']} during {context.phase}",
                attempts=history_errors + spot_errors + intraday_errors,
                last_available_date=latest_date,
                expected_date=expected_date,
                cache_source=cache_source,
            )

        spot, fallback_warnings = _enrich_spot(
            spot,
            history,
            intraday,
        )
        warnings.extend(fallback_warnings)

        if intraday.empty:
            intraday = _build_intraday_snapshot_approximation(
                spot,
                now,
            )
            status["intraday"].update({
                "status": "APPROXIMATE",
                "source": "实时快照近似线",
                "points": len(intraday),
                "approximate": True,
                "message": (
                    "一分钟分时接口均失败；走势图仅连接今开与当前价，"
                    "不是完整分钟走势"
                ),
                "errors": intraday_errors,
            })
            warnings.append(status["intraday"]["message"])

        provisional = bool(close_snapshot_allowed)
        history = _merge_spot_into_history(
            history,
            spot,
            provisional=provisional,
        )
        data_sources.append(status["realtime"]["source"] or "实时行情")
        if status["intraday"]["source"]:
            data_sources.append(status["intraday"]["source"])

        if provisional:
            _save_history_cache(
                project_root,
                config,
                asset["symbol"],
                history,
                now,
            )
            cache_date = now.strftime("%Y-%m-%d")
            warnings.append(
                "今日收盘行来自实时快照，已标记为待验证；正式日线恢复后会覆盖"
            )
    else:
        status["realtime"].update({
            "status": "NOT_REQUIRED",
            "message": "正式日线已包含收盘数据，无需实时快照",
        })

        # A close report still needs today's actual intraday chart.  Realtime
        # snapshot and intraday chart are separate concerns and must not share
        # the same gate.
        should_fetch_closed_intraday = (
            context.phase == "AFTER_CLOSE" or mode == "close"
        )
        if should_fetch_closed_intraday:
            cached_today, cached_today_meta = _load_intraday_cache(
                project_root, str(asset["symbol"]), now
            )
            periods = [
                str(item)
                for item in request_policy.get("intraday_periods", ["1", "5", "15"])
            ]
            minute_attempts = []
            for period in periods:
                minute_attempts.extend([
                    (
                        f"AKShare fund_etf_hist_min_em({period}m)",
                        f"close-intraday-etf-{period}m",
                        period,
                        lambda period=period: _fetch_intraday_for_date(ak, asset, now, period),
                    ),
                    (
                        f"AKShare stock_zh_a_hist_min_em({period}m)",
                        f"close-intraday-stock-{period}m",
                        period,
                        lambda period=period: _fetch_intraday_stock_for_date(ak, asset, now, period),
                    ),
                    (
                        f"AKShare stock_zh_a_minute(Sina,{period}m)",
                        f"close-intraday-sina-{period}m",
                        period,
                        lambda period=period: _fetch_intraday_sina_for_date(ak, asset, now, period),
                    ),
                ])
            for source_name, label, period, operation in minute_attempts:
                intraday_result, errors = _retry_call(
                    operation,
                    {
                        "max_attempts": int(request_policy.get("same_endpoint_max_attempts", 1)),
                        "delays_seconds": [float(request_policy.get("retry_delay_seconds", 30))],
                    },
                    label,
                    logger,
                    before_attempt=pacer.before_attempt,
                )
                intraday_errors.extend(errors)
                if intraday_result is not None and not intraday_result.empty:
                    intraday = _save_intraday_cache(
                        project_root,
                        str(asset["symbol"]),
                        now,
                        intraday_result,
                        period=period,
                        source=source_name,
                        now=now,
                    )
                    status["intraday"].update({
                        "status": "SUCCESS",
                        "source": source_name,
                        "period": period,
                        "points": len(intraday),
                        "approximate": False,
                        "message": f"收盘后今日{period}分钟走势获取成功，共{len(intraday)}个点",
                        "errors": intraday_errors,
                    })
                    data_sources.append(source_name)
                    break
            if intraday.empty and not cached_today.empty:
                intraday = cached_today
                status["intraday"].update({
                    "status": "CACHE",
                    "source": "本地分时缓存",
                    "period": str(cached_today_meta.get("period", "1")),
                    "points": len(intraday),
                    "approximate": False,
                    "message": f"网络分时不可用，使用今日本地分时，共{len(intraday)}个点",
                    "errors": intraday_errors,
                })
            elif intraday.empty:
                status["intraday"].update({
                    "status": "FAILED",
                    "source": None,
                    "points": 0,
                    "approximate": False,
                    "message": "收盘后仍已尝试今日1/5/15分钟走势，但所有来源均不可用",
                    "errors": intraday_errors,
                })
        else:
            status["intraday"].update({
                "status": "NOT_REQUIRED",
                "message": "开盘前暂无当日分时数据",
            })

    previous_date = _previous_business_date(now).to_pydatetime()
    status["previous_intraday"]["date"] = previous_date.strftime("%Y-%m-%d")
    previous_errors: list[str] = []
    cached_previous, cached_previous_meta = _load_intraday_cache(
        project_root, str(asset["symbol"]), previous_date
    )
    if not cached_previous.empty:
        previous_intraday = cached_previous
        status["previous_intraday"].update({
            "status": "CACHE",
            "source": "本地分时缓存",
            "period": str(cached_previous_meta.get("period", "1")),
            "points": len(previous_intraday),
            "approximate": False,
            "message": f"{previous_date:%Y-%m-%d}使用本地分时缓存，共{len(previous_intraday)}个点",
            "errors": [],
        })
    else:
        periods = [str(item) for item in request_policy.get("intraday_periods", ["1", "5", "15"])]
        previous_attempts = []
        for period in periods:
            previous_attempts.extend([
                (
                    f"AKShare fund_etf_hist_min_em({period}m)",
                    f"previous-intraday-etf-{period}m",
                    period,
                    lambda period=period: _fetch_intraday_for_date(ak, asset, previous_date, period),
                ),
                (
                    f"AKShare stock_zh_a_hist_min_em({period}m)",
                    f"previous-intraday-stock-{period}m",
                    period,
                    lambda period=period: _fetch_intraday_stock_for_date(ak, asset, previous_date, period),
                ),
                (
                    f"AKShare stock_zh_a_minute(Sina,{period}m)",
                    f"previous-intraday-sina-{period}m",
                    period,
                    lambda period=period: _fetch_intraday_sina_for_date(ak, asset, previous_date, period),
                ),
            ])
        for source_name, label, period, operation in previous_attempts:
            previous_result, errors = _retry_call(
                operation,
                {"max_attempts": int(request_policy.get("same_endpoint_max_attempts", 1)),
                 "delays_seconds": [float(request_policy.get("retry_delay_seconds", 30))]},
                label,
                logger,
                before_attempt=pacer.before_attempt,
            )
            previous_errors.extend(errors)
            if previous_result is not None and not previous_result.empty:
                previous_intraday = _save_intraday_cache(
                    project_root, str(asset["symbol"]), previous_date, previous_result,
                    period=period, source=source_name, now=now,
                )
                status["previous_intraday"].update({
                    "status": "SUCCESS",
                    "source": source_name,
                    "period": period,
                    "points": len(previous_intraday),
                    "approximate": False,
                    "message": f"{previous_date:%Y-%m-%d}{period}分钟走势获取成功，共{len(previous_intraday)}个点",
                    "errors": previous_errors,
                })
                break

    if previous_intraday.empty:
        previous_intraday = _build_daily_ohlc_approximation(
            history,
            previous_date,
        )
        if not previous_intraday.empty:
            status["previous_intraday"].update({
                "status": "APPROXIMATE",
                "source": "历史日线OHLC示意",
                "points": len(previous_intraday),
                "approximate": True,
                "message": (
                    f"{previous_date:%Y-%m-%d}分钟数据不可用；"
                    "仅用开、高、低、收生成示意线，不代表真实日内先后顺序"
                ),
                "errors": previous_errors,
            })
        else:
            status["previous_intraday"].update({
                "status": "FAILED",
                "source": None,
                "points": 0,
                "approximate": False,
                "message": f"{previous_date:%Y-%m-%d}没有可用走势数据",
                "errors": previous_errors,
            })

    for warning in dict.fromkeys(warnings):
        logger.warning(
            "Market data warning | Asset=%s | %s",
            asset["symbol"],
            warning,
        )

    latest = _build_latest(asset, history, spot)
    return MarketBundle(
        history=history,
        latest=latest,
        data_source=" + ".join(data_sources),
        generated_at=now,
        intraday=intraday,
        previous_intraday=previous_intraday,
        data_phase=context.phase,
        market_timestamp=latest.timestamp,
        using_local_cache=using_local_cache,
        cache_date=cache_date,
        data_warnings=list(dict.fromkeys(warnings)),
        phase_label=context.phase_label,
        data_status=status,
    )

def _mock(asset: dict, config: dict) -> MarketBundle:
    n = config["data"].get("history_days", 120)
    rng = np.random.default_rng(20260711)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    noise = rng.normal(0.0008, 0.018, n)
    if n >= 10:
        noise[-10:] = [-.012, -.018, -.015, .004, -.020, -.011, -.024, -.016, -.029, -.018]
    close = [1.0]
    for value in noise[1:]:
        close.append(close[-1] * (1 + value))
    frame = pd.DataFrame({"date": dates, "close": close})
    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
    frame["high"] = frame[["open", "close"]].max(axis=1) * 1.006
    frame["low"] = frame[["open", "close"]].min(axis=1) * .994
    frame["volume"] = rng.integers(1_000_000, 9_000_000, n)
    frame["amount"] = frame["volume"] * frame["close"]
    frame["daily_change_pct"] = frame["close"].pct_change().fillna(0) * 100
    last = frame.iloc[-1]
    latest = MarketSnapshot(
        asset["symbol"], asset["name"], pd.Timestamp(last["date"]).to_pydatetime(),
        float(last["close"]), float(last["daily_change_pct"]),
        float(last["volume"]), float(last["amount"]), -35.77,
    )
    return MarketBundle(
        frame,
        latest,
        "Mock data",
        datetime.now(),
        intraday=pd.DataFrame(columns=["time", "price", "volume", "amount"]),
        data_phase="AFTER_CLOSE",
        market_timestamp=latest.timestamp,
        data_warnings=[],
        phase_label="模拟收盘数据",
        data_status={
            "history": {
                "status": "SUCCESS",
                "source": "Mock data",
                "message": "模拟历史日线",
            },
            "realtime": {
                "status": "NOT_REQUIRED",
                "source": None,
                "message": "模拟模式无需实时接口",
            },
            "intraday": {
                "status": "NOT_REQUIRED",
                "source": None,
                "points": 0,
                "approximate": False,
                "message": "模拟模式未生成分时数据",
            },
        },
    )
