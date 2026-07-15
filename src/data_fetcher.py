from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import logging
import time
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


def _retry_call(
    operation: Callable[[], Any],
    retry_cfg: dict,
    label: str,
    logger: logging.Logger | None = None,
):
    delays = retry_cfg.get("delays_seconds", [5, 15, 30])
    max_attempts = int(retry_cfg.get("max_attempts", 3))
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        try:
            if logger:
                logger.info("Data request | Source=%s | Attempt=%s/%s", label, attempt, max_attempts)
            result = operation()
            if logger:
                logger.info("Data request succeeded | Source=%s | Attempt=%s", label, attempt)
            return result, errors
        except Exception as exc:
            message = f"{label} attempt {attempt}: {type(exc).__name__}: {exc}"
            errors.append(message)
            if logger:
                logger.warning("Data request failed | %s", message)
            if attempt < max_attempts:
                delay = delays[min(attempt - 1, len(delays) - 1)] if delays else 5
                if logger:
                    logger.info("Data retry scheduled | Source=%s | Delay=%ss", label, delay)
                time.sleep(float(delay))

    return None, errors


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
                        str(latest_path.relative_to(project_root)),
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


def _fetch_history(ak, asset: dict, config: dict, now: datetime) -> pd.DataFrame:
    data_cfg = config["data"]
    history_days = int(data_cfg.get("history_days", 1000))
    start = now - timedelta(days=max(history_days * 2, 240))

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


def _fetch_intraday(ak, asset: dict, now: datetime) -> pd.DataFrame:
    start = now.strftime("%Y-%m-%d") + " 09:30:00"
    end = now.strftime("%Y-%m-%d") + " 15:00:00"
    frame = ak.fund_etf_hist_min_em(
        symbol=asset["symbol"],
        start_date=start,
        end_date=end,
        period="1",
        adjust="",
    )
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["time", "price", "volume", "amount", "open", "high", "low"])

    frame = frame.rename(
        columns={
            "时间": "time",
            "收盘": "price",
            "成交量": "volume",
            "成交额": "amount",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
        }
    )
    if "time" not in frame or "price" not in frame:
        return pd.DataFrame(columns=["time", "price", "volume", "amount", "open", "high", "low"])

    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    for column in ["price", "volume", "amount", "open", "high", "low"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["time", "price"]).sort_values("time").reset_index(drop=True)


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


def _merge_spot_into_history(history: pd.DataFrame, spot: dict) -> pd.DataFrame:
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
    realtime_required = should_request_realtime(mode, context)
    logger.info(
        "Market context | Mode=%s | Phase=%s | Label=%s | Realtime=%s | Time=%s",
        mode,
        context.phase,
        context.phase_label,
        realtime_required,
        now.strftime("%Y-%m-%d %H:%M:%S"),
    )

    history, history_errors = _retry_call(
        lambda: _fetch_history(ak, asset, config, now),
        retry_cfg,
        "historical",
        logger,
    )

    using_local_cache = False
    cache_date = None
    cache_source = None
    cache_note = None
    data_sources: list[str] = []
    warnings: list[str] = []

    if history is not None:
        _save_history_cache(project_root, config, asset["symbol"], history, now)
        data_sources.append("AKShare历史日线")
    else:
        history, cached_at, cache_source = _load_history_cache(project_root, config, asset["symbol"])
        if history is None:
            raise DataFetchError(
                f"Historical data unavailable for {asset['symbol']} and no local cache exists",
                attempts=history_errors,
            )
        fresh, latest_date, expected_date = _history_is_fresh_enough(history, now, context.phase)
        if not fresh:
            raise DataFetchError(
                (
                    f"Only stale local data is available for {asset['symbol']}: "
                    f"latest={latest_date}, expected>={expected_date}"
                ),
                attempts=history_errors,
                last_available_date=latest_date,
                expected_date=expected_date,
                cache_source=cache_source,
            )
        using_local_cache = True
        cache_date = latest_date
        cache_note = cached_at or "unknown"
        data_sources.append(f"本地日线缓存({cache_source})")
        warnings.append("历史接口不可用，已使用日期符合要求的本地日线缓存")
        logger.warning(
            "Historical API unavailable; using fresh local cache | Source=%s | Latest=%s | CachedAt=%s",
            cache_source,
            latest_date,
            cache_note,
        )

    spot = None
    intraday = pd.DataFrame(columns=["time", "price", "volume", "amount", "open", "high", "low"])
    spot_errors: list[str] = []

    if realtime_required:
        spot_result, spot_errors = _retry_call(
            lambda: _fetch_spot(ak, asset, now),
            retry_cfg,
            "realtime",
            logger,
        )
        if spot_result is not None:
            spot, field_warnings = spot_result
            warnings.extend(field_warnings)

        # Minute data is useful for the chart and provides a fallback if the
        # market-wide realtime table is incomplete or temporarily unavailable.
        intraday_result, intraday_errors = _retry_call(
            lambda: _fetch_intraday(ak, asset, now),
            {"max_attempts": 1, "delays_seconds": []},
            "intraday-chart",
            logger,
        )
        if intraday_result is not None:
            intraday = intraday_result
        else:
            warnings.extend(intraday_errors)

        if spot is None:
            spot = _derive_spot_from_intraday(intraday, history, now)
            if spot is not None:
                warnings.append("实时快照接口失败，已使用当天分时最后一笔数据")
                logger.warning("Realtime spot fallback succeeded from intraday data")

        if spot is None:
            latest_date = pd.Timestamp(history["date"].max()).strftime("%Y-%m-%d")
            expected_date = expected_history_date(now, context).strftime("%Y-%m-%d")
            raise DataFetchError(
                f"Realtime data unavailable for {asset['symbol']} during {context.phase}",
                attempts=history_errors + spot_errors + intraday_errors,
                last_available_date=latest_date,
                expected_date=expected_date,
                cache_source=cache_source,
            )

        spot, fallback_warnings = _enrich_spot(spot, history, intraday)
        warnings.extend(fallback_warnings)
        history = _merge_spot_into_history(history, spot)
        data_sources.append("AKShare实时行情")
        if intraday is not None and not intraday.empty:
            data_sources.append("AKShare一分钟分时")

    for warning in dict.fromkeys(warnings):
        logger.warning("Market data warning | Asset=%s | %s", asset["symbol"], warning)

    latest = _build_latest(asset, history, spot)
    return MarketBundle(
        history=history,
        latest=latest,
        data_source=" + ".join(data_sources),
        generated_at=now,
        intraday=intraday,
        data_phase=context.phase,
        market_timestamp=latest.timestamp,
        using_local_cache=using_local_cache,
        cache_date=cache_date,
        data_warnings=list(dict.fromkeys(warnings)),
        phase_label=context.phase_label,
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
    )
