"""Industry research facts for monitoring and manual review only.

This module deliberately does not decide trades.  It records the provenance,
freshness and completeness of slowly changing industry inputs so a report can
show what was observed and what still needs a human judgement.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd


DEFAULT_PROFILE_BY_TRACKING_CODE = {
    "399395": "nonferrous",
    "561380": "grid_equipment",
}

MANUAL_REVIEW_ITEMS = {
    "nonferrous": [
        "美元指数与全球风险偏好：本轮数据源未通过稳定性验证，请人工核对。",
        "矿山停产、复产、减产和新矿投产等供给事件的持续性。",
        "AI、新能源和电网需求变化是否构成长期逻辑变化。",
        "行业盈利与估值是否与价格表现匹配。",
    ],
    "grid_equipment": [
        "国网、南网投资方向及项目实际进度。",
        "招标项目的编号去重、延期、取消、重招及金额完整性。",
        "订单增长是否同步转化为收入、利润和经营现金流。",
        "应收账款、估值、竞争格局和政策变化是否改变长期逻辑。",
    ],
}


def _asset_id(asset: dict[str, Any]) -> str:
    return str(asset.get("id") or asset.get("asset_code") or asset.get("symbol") or "unknown")


def _snapshot_path(root: Path, asset: dict[str, Any]) -> Path:
    return root / "data" / "assets" / _asset_id(asset) / "research" / "latest.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _date_text(value: Any) -> str | None:
    match = re.search(r"(\d{4})年(\d{1,2})月", str(value))
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-01"
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def _month_timestamp(value: Any) -> pd.Timestamp:
    text = str(value)
    match = re.search(r"(\d{4})年(\d{1,2})月", text)
    if match:
        return pd.Timestamp(year=int(match.group(1)), month=int(match.group(2)), day=1)
    return pd.to_datetime(value, errors="coerce")


def _observation(
    *,
    name: str,
    value: Any,
    as_of_date: Any,
    source: str,
    frequency: str,
    status: str,
    confidence: float,
    definition: str,
    now: datetime,
    source_url: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "metric_name": name,
        "value": value,
        "as_of_date": _date_text(as_of_date),
        "updated_at": now.isoformat(timespec="seconds"),
        "source": source,
        "source_url": source_url,
        "frequency": frequency,
        "status": status,
        "confidence": confidence,
        "definition": definition,
        "note": note,
    }


def _reuse_if_fresh(
    previous: dict[str, Any],
    key: str,
    now: datetime,
    max_age_days: int,
) -> list[dict[str, Any]] | None:
    item = previous.get("collector_cache", {}).get(key, {})
    updated = pd.to_datetime(item.get("updated_at"), errors="coerce")
    observations = item.get("observations")
    if not observations or pd.isna(updated):
        return None
    if now - updated.to_pydatetime().replace(tzinfo=None) > timedelta(days=max_age_days):
        return None
    return observations


def _run_collector(
    *,
    key: str,
    previous: dict[str, Any],
    now: datetime,
    max_age_days: int,
    collect: Callable[[], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cached = _reuse_if_fresh(previous, key, now, max_age_days)
    if cached is not None:
        return cached, {"updated_at": previous["collector_cache"][key]["updated_at"], "observations": cached, "cache": True}
    try:
        observations = collect()
        return observations, {"updated_at": now.isoformat(timespec="seconds"), "observations": observations, "cache": False}
    except Exception as exc:
        error = _observation(
            name=key,
            value=None,
            as_of_date=None,
            source="AKShare",
            frequency="按配置频率",
            status="ERROR",
            confidence=0.0,
            definition="自动采集失败；不得用缺失值推导行业结论。",
            now=now,
            note=f"{type(exc).__name__}: {exc}",
        )
        return [error], {"updated_at": now.isoformat(timespec="seconds"), "observations": [error], "cache": False}


def _nonferrous_collectors(ak: Any, now: datetime) -> dict[str, Callable[[], list[dict[str, Any]]]]:
    def copper() -> list[dict[str, Any]]:
        frame = ak.futures_zh_daily_sina(symbol="CU0")
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
        if frame.empty:
            raise ValueError("沪铜日线为空")
        latest = frame.iloc[-1]
        observations = [
            _observation(
                name="沪铜连续合约收盘价",
                value=float(latest["close"]),
                as_of_date=latest["date"],
                source="AKShare futures_zh_daily_sina(CU0)",
                frequency="日",
                status="OK",
                confidence=0.85,
                definition="沪铜连续合约收盘价，仅用于行业供需与价格趋势的辅助观察。",
                now=now,
            )
        ]
        for days in (5, 20, 60):
            if len(frame) > days:
                change = (float(latest["close"]) / float(frame.iloc[-days - 1]["close"]) - 1) * 100
                observations.append(_observation(
                    name=f"沪铜{days}日涨跌幅",
                    value=round(change, 4),
                    as_of_date=latest["date"],
                    source="AKShare futures_zh_daily_sina(CU0)",
                    frequency="日",
                    status="OK",
                    confidence=0.85,
                    definition="以连续合约收盘价计算的区间涨跌幅，不构成交易指令。",
                    now=now,
                ))
        return observations

    def lme_inventory() -> list[dict[str, Any]]:
        frame = ak.macro_euro_lme_stock().copy()
        if frame.empty or "铜-库存" not in frame.columns:
            raise ValueError("LME 铜库存字段不可用")
        frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
        frame["铜-库存"] = pd.to_numeric(frame["铜-库存"], errors="coerce")
        frame = frame.dropna(subset=["日期", "铜-库存"]).sort_values("日期").reset_index(drop=True)
        latest = frame.iloc[-1]
        observations = [_observation(
            name="LME铜库存",
            value=float(latest["铜-库存"]),
            as_of_date=latest["日期"],
            source="AKShare macro_euro_lme_stock",
            frequency="周",
            status="OK",
            confidence=0.75,
            definition="LME 铜库存，需与 SHFE/COMEX、季节性和需求数据共同人工解读。",
            now=now,
        )]
        for weeks, trading_days in ((1, 5), (4, 20), (13, 65)):
            if len(frame) > trading_days:
                change = (float(latest["铜-库存"]) / float(frame.iloc[-trading_days - 1]["铜-库存"]) - 1) * 100
                observations.append(_observation(
                    name=f"LME铜库存{weeks}周变化率",
                    value=round(change, 4),
                    as_of_date=latest["日期"],
                    source="AKShare macro_euro_lme_stock",
                    frequency="周",
                    status="OK",
                    confidence=0.75,
                    definition="按连续观测点计算的库存变化率；不是完整三地库存或季节性结论。",
                    now=now,
                ))
        return observations

    def pmi() -> list[dict[str, Any]]:
        frame = ak.macro_china_pmi().copy()
        if frame.empty or "制造业-指数" not in frame.columns:
            raise ValueError("制造业 PMI 字段不可用")
        frame["_month"] = frame["月份"].map(_month_timestamp)
        frame["制造业-指数"] = pd.to_numeric(frame["制造业-指数"], errors="coerce")
        frame = frame.dropna(subset=["_month", "制造业-指数"]).sort_values("_month", ascending=False).reset_index(drop=True)
        latest = frame.iloc[0]
        return [_observation(
            name="中国制造业PMI",
            value=float(latest["制造业-指数"]),
            as_of_date=latest["_month"],
            source="AKShare macro_china_pmi",
            frequency="月",
            status="PARTIAL",
            confidence=0.65,
            definition="该接口只提供制造业 PMI 总指数；生产、新订单及连续三个月趋势仍需人工补充。",
            now=now,
            note="数据项不完整，不能独立用于长期逻辑判断。",
        )]

    return {"copper": copper, "lme_inventory": lme_inventory, "pmi": pmi}


def _grid_collectors(ak: Any, asset: dict[str, Any], now: datetime) -> dict[str, Callable[[], list[dict[str, Any]]]]:
    def etf_market() -> list[dict[str, Any]]:
        tracking_code = str(asset.get("tracking_code") or asset.get("symbol") or "").zfill(6)
        frame = ak.fund_etf_spot_em()
        found = frame.loc[frame.iloc[:, 0].astype(str).str.zfill(6) == tracking_code]
        if found.empty:
            raise ValueError(f"未找到跟踪 ETF {tracking_code}")
        row = found.iloc[0]
        columns = {str(column): row[column] for column in frame.columns}
        as_of = columns.get("数据日期") or columns.get("更新时间")
        return [
            _observation(
                name="ETF最新份额",
                value=_as_float(columns.get("最新份额")),
                as_of_date=as_of,
                source="AKShare fund_etf_spot_em",
                frequency="日",
                status="OK",
                confidence=0.8,
                definition="跟踪 ETF 最新份额。累计多个交易日后才可观察份额变化趋势。",
                now=now,
            ),
            _observation(
                name="ETF主力净流入",
                value=_as_float(columns.get("主力净流入-净额")),
                as_of_date=as_of,
                source="AKShare fund_etf_spot_em",
                frequency="日",
                status="OK",
                confidence=0.55,
                definition="单日主力净流入仅为确认项，不能单独改变策略。",
                now=now,
            ),
        ]

    def etf_scale_sse() -> list[dict[str, Any]]:
        tracking_code = str(asset.get("tracking_code") or asset.get("symbol") or "").zfill(6)
        frame = ak.fund_etf_scale_sse(date=now.strftime("%Y%m%d"))
        found = frame.loc[frame.iloc[:, 1].astype(str).str.zfill(6) == tracking_code]
        if found.empty:
            raise ValueError(f"上交所 ETF 规模表未找到跟踪 ETF {tracking_code}")
        row = found.iloc[0]
        columns = {str(column): row[column] for column in frame.columns}
        shares = _as_float(columns.get("基金份额"))
        if shares is None:
            raise ValueError("上交所 ETF 规模表未返回基金份额")
        return [_observation(
            name="上交所ETF份额",
            value=shares,
            as_of_date=columns.get("统计日期"),
            source="AKShare fund_etf_scale_sse",
            frequency="日",
            status="OK",
            confidence=0.8,
            definition="交易所公布的 ETF 份额，用于与实时份额交叉核对；不同来源不直接混算。",
            now=now,
        )]

    return {"etf_market": etf_market, "etf_scale_sse": etf_scale_sse}


def collect_research_snapshot(
    asset: dict[str, Any],
    root: Path,
    *,
    now: datetime | None = None,
    research_config: dict[str, Any] | None = None,
    ak_module: Any | None = None,
) -> dict[str, Any]:
    """Collect configured industry facts without producing a trade decision."""
    now = now or datetime.now()
    research_config = research_config or {}
    asset_id = _asset_id(asset)
    profiles = research_config.get("asset_profiles", {})
    tracking_code = str(asset.get("tracking_code") or asset.get("symbol") or "")
    profile = str(profiles.get(asset_id) or profiles.get(tracking_code) or DEFAULT_PROFILE_BY_TRACKING_CODE.get(tracking_code, "manual"))
    previous = _read_json(_snapshot_path(root, asset), {})
    cache: dict[str, Any] = {}
    observations: list[dict[str, Any]] = []

    if profile == "manual":
        manual_items = ["尚未为该资产配置行业研究模板；请先选择或新增人工复核清单。"]
        snapshot = {
            "asset_id": asset_id,
            "profile": profile,
            "generated_at": now.isoformat(timespec="seconds"),
            "research_data_state": "MANUAL_REVIEW_REQUIRED",
            "thesis_state": "MANUAL_REVIEW_REQUIRED",
            "observations": [],
            "manual_review_items": manual_items,
            "collector_cache": {},
            "trade_automation": False,
        }
        _write_json(_snapshot_path(root, asset), snapshot)
        return snapshot

    if ak_module is None:
        import akshare as ak_module

    if profile == "nonferrous":
        collectors = _nonferrous_collectors(ak_module, now)
        ages = {"copper": 1, "lme_inventory": 7, "pmi": 30}
    elif profile == "grid_equipment":
        collectors = _grid_collectors(ak_module, asset, now)
        ages = {"etf_market": 1, "etf_scale_sse": 1}
    else:
        collectors, ages = {}, {}

    for key, collector in collectors.items():
        items, cache_item = _run_collector(
            key=key,
            previous=previous,
            now=now,
            max_age_days=ages[key],
            collect=collector,
        )
        observations.extend(items)
        cache[key] = cache_item

    has_error = any(item["status"] == "ERROR" for item in observations)
    has_partial = any(item["status"] == "PARTIAL" for item in observations)
    data_state = "DATA_PARTIAL" if has_error or has_partial else "DATA_OK"
    snapshot = {
        "asset_id": asset_id,
        "profile": profile,
        "generated_at": now.isoformat(timespec="seconds"),
        "research_data_state": data_state,
        "thesis_state": "MANUAL_REVIEW_REQUIRED",
        "observations": observations,
        "manual_review_items": MANUAL_REVIEW_ITEMS.get(profile, []),
        "collector_cache": cache,
        "trade_automation": False,
    }
    _write_json(_snapshot_path(root, asset), snapshot)
    return snapshot
