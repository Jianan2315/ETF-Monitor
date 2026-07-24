from __future__ import annotations

import json
import os
import csv
import html
import io
import shutil
import socket
import subprocess
import re
import uuid
import xml.etree.ElementTree as ET
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from typing import Any

import requests
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, url_for

from src.market_clock import build_market_context, expected_history_date, market_now, validate_run_mode
from src.data_fetcher import (
    diagnose_data_sources,
    explore_eastmoney_history_endpoints,
    refresh_history_cache_now,
)
from src.browser_inspector import inspect_browser_requests
from src.logger import configure_logging
from src.transactions import (
    delete_transaction,
    delete_transactions,
    load_transactions,
    reconcile_pending_transactions,
    save_transaction,
)
from src.position import calculate_position
from src.asset_store import (
    ensure_layout, load_assets, save_asset, delete_asset,
    asset_transactions_path, asset_cache_dir, asset_data_dir, asset_latest_path,
    asset_report_path, asset_history_dir, asset_history_index_path,
)
from src.power_management import (
    disable_keep_awake,
    enable_keep_awake,
    keep_awake_status,
)
from src.html_report import generate_history_index
from src.status import migrate_email_triggers
from src.sell_reminders import stop_sell_reminders


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765
CONTROL_VERSION = "13.2.0"

app = Flask(
    __name__,
    template_folder=str(ROOT / "control_web" / "templates"),
    static_folder=str(ROOT / "control_web" / "static"),
    static_url_path="/control-static",
)

_RUN_LOCK = threading.Lock()
_PENDING_SETTLEMENT_LOCK = threading.Lock()
_PENDING_REFRESH_ATTEMPTS: dict[str, datetime] = {}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    json.loads(temp_path.read_text(encoding="utf-8"))
    temp_path.replace(path)


def _backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None

    backup_dir = ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = backup_dir / f"{stamp}_{path.name}"
    shutil.copy2(path, backup_path)
    return backup_path


def _disable_legacy_force_email_once() -> None:
    marker = (
        ROOT
        / "data"
        / "migrations"
        / "v11_6_force_email_disabled"
    )
    if marker.exists():
        return

    email_path = ROOT / "config" / "global" / "email.json"
    email_doc = _read_json(email_path, {"email": {}})
    debug = email_doc.setdefault("email", {}).setdefault(
        "debug",
        {},
    )
    changed = bool(
        debug.get("enabled")
        or debug.get("force_send_email")
    )
    debug["enabled"] = False
    debug["force_send_email"] = False
    if changed:
        _backup_file(email_path)
        _atomic_write_json(email_path, email_doc)

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        datetime.now().isoformat(timespec="seconds"),
        encoding="utf-8",
    )

def _migrate_legacy_report() -> None:
    """Retained as a no-op for callers from older launchers.

    V13.2 never migrates a global report: reports belong to individual assets.
    """
    return None


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _schedule_config() -> dict[str, Any]:
    schedule_doc = _read_json(
        ROOT / "config" / "global" / "schedule.json",
        {"schedule": {"timezone": "Asia/Shanghai"}},
    )
    return {"schedule": schedule_doc.get("schedule", {})}


def _run_status_file() -> Path:
    return ROOT / "data" / "global" / "run_status" / "latest_run.json"


def _read_run_status() -> dict[str, Any]:
    return _read_json(_run_status_file(), {})


def _run_monitor(mode: str, slot: str, asset_id: str = "") -> dict[str, Any]:
    config = _schedule_config()
    now = market_now(config)
    validation = validate_run_mode(mode, now)
    if not validation.allowed:
        return {
            "success": False,
            "code": "INVALID_MODE",
            "message": validation.message,
            "market": validation.context.to_dict(),
        }

    if not _RUN_LOCK.acquire(blocking=False):
        return {
            "success": False,
            "code": "RUNNING",
            "message": "监控程序正在运行，请等待当前任务完成。",
        }

    try:
        status_path = _run_status_file()
        status_path.unlink(missing_ok=True)

        command = [
            _python_executable(),
            str(ROOT / "run.py"),
            "--mode",
            mode,
        ]
        if slot:
            command += ["--slot", slot]
        if asset_id:
            command += ["--asset", asset_id]

        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt"
                else 0
            ),
        )

        status = _read_run_status()
        report_path = None
        if asset_id:
            try:
                report_path = asset_report_path(ROOT, _asset_identity(_find_asset(asset_id)))
            except ValueError:
                report_path = None
        success = (
            completed.returncode == 0
            and status.get("status") == "SUCCESS"
            and (not asset_id or (report_path is not None and report_path.exists()))
        )

        message = status.get("message") or (
            "报告已生成。"
            if success
            else "程序已结束，但没有生成新的报告。请查看日志。"
        )
        return {
            "success": success,
            "code": status.get("status", "UNKNOWN"),
            "return_code": completed.returncode,
            "message": message,
            "phase": status.get("phase"),
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "steps": status.get("steps", []),
            "data_status": status.get("data_status", {}),
            "email_status": status.get("email_status", "UNKNOWN"),
            "errors": status.get("errors", []),
            "report_url": (f"/report?asset_id={asset_id}" if success and asset_id else None),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "code": "TIMEOUT",
            "message": "运行超时。请查看 logs/global/application.log。",
        }
    finally:
        _RUN_LOCK.release()


def _ollama_url() -> str:
    llm_doc = _read_json(ROOT / "config" / "global" / "llm.json", {})
    llm = llm_doc.get("llm", {})
    return str(llm.get("base_url", "http://127.0.0.1:11434")).rstrip("/")


def _check_ollama() -> dict[str, Any]:
    base_url = _ollama_url()
    try:
        response = requests.get(
            f"{base_url}/api/tags",
            timeout=3,
        )
        response.raise_for_status()
        payload = response.json()
        models = [
            item.get("name", "")
            for item in payload.get("models", [])
            if item.get("name")
        ]
        return {
            "ok": True,
            "message": "Ollama 服务正常",
            "models": models,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Ollama 未运行或无法连接：{exc}",
            "models": [],
        }


def _find_ollama_executable() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "Ollama"
        / "ollama.exe",
        Path(os.environ.get("ProgramFiles", ""))
        / "Ollama"
        / "ollama.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _start_ollama() -> dict[str, Any]:
    current = _check_ollama()
    if current["ok"]:
        return current

    executable = _find_ollama_executable()
    if not executable:
        return {
            "ok": False,
            "message": "没有找到 ollama.exe。请先安装 Ollama。",
            "models": [],
        }

    try:
        subprocess.Popen(
            [executable, "serve"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt"
                else 0
            ),
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"启动 Ollama 失败：{exc}",
            "models": [],
        }

    for _ in range(20):
        time.sleep(1)
        status = _check_ollama()
        if status["ok"]:
            status["message"] = "Ollama 已启动"
            return status

    return {
        "ok": False,
        "message": "已尝试启动 Ollama，但服务在20秒内没有就绪。",
        "models": [],
    }


def _network_check() -> dict[str, Any]:
    try:
        response = requests.get(
            "https://graph.microsoft.com/v1.0/$metadata",
            timeout=5,
        )
        return {
            "ok": response.status_code < 500,
            "message": f"网络可用（HTTP {response.status_code}）",
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"网络连接失败：{exc}",
        }


def _asset_identity(asset: dict[str, Any]) -> str:
    return str(asset.get("id") or asset.get("asset_code") or asset.get("symbol") or "").strip()


def _asset_tracking_code(asset: dict[str, Any]) -> str:
    return str(asset.get("tracking_code") or asset.get("symbol") or "").strip()


def _normalize_asset(asset: dict[str, Any], index: int = 0) -> dict[str, Any]:
    item = dict(asset or {})
    legacy_symbol = str(item.get("symbol") or "").strip()
    item.setdefault("id", item.get("asset_code") or legacy_symbol or f"asset-{index+1}")
    item.setdefault("asset_code", legacy_symbol)
    item.setdefault("tracking_code", legacy_symbol)
    item.setdefault("tracking_name", item.get("name", ""))
    # Keep symbol as the market-data code for backward compatibility.
    item["symbol"] = _asset_tracking_code(item)
    return item


def _all_assets() -> list[dict[str, Any]]:
    ensure_layout(ROOT)
    return [_normalize_asset(asset, idx) for idx, asset in enumerate(load_assets(ROOT))]


def _find_asset(asset_id: str | None = None) -> dict[str, Any]:
    assets = _all_assets()
    if not assets:
        raise ValueError("尚未添加任何资产")
    wanted = str(asset_id or "").strip()
    if wanted:
        for asset in assets:
            if wanted in {_asset_identity(asset), str(asset.get("asset_code", "")), str(asset.get("tracking_code", ""))}:
                return asset
        raise ValueError(f"未找到资产：{wanted}")
    return assets[0]


def _primary_asset() -> dict[str, Any]:
    return _find_asset(None)


def _eastmoney_check(asset: dict[str, Any] | None = None) -> dict[str, Any]:
    asset = asset or _primary_asset()
    symbol = str(asset.get("symbol", "561380"))
    market_prefix = "1" if symbol.startswith(("5", "6", "9")) else "0"
    now = market_now(_schedule_config())
    begin = (now - timedelta(days=20)).strftime("%Y%m%d")
    try:
        response = requests.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "klt": "101",
                "fqt": "1",
                "beg": begin,
                "end": now.strftime("%Y%m%d"),
                "secid": f"{market_prefix}.{symbol}",
            },
            timeout=6,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return {
            "ok": bool(data),
            "message": (
                "东方财富历史接口可访问"
                if data
                else "东方财富返回成功，但没有行情数据"
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"东方财富接口不可用：{exc}",
        }


def _cache_status(asset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return cache status for one holding asset.

    Asset-owned runtime data is scoped by the holding-code asset identity, while
    the market-data cache filename is scoped by the tracking code.
    """
    asset = asset or _primary_asset()
    asset_key = _asset_identity(asset)
    tracking_code = _asset_tracking_code(asset)
    path = asset_cache_dir(ROOT, asset_key) / f"{tracking_code}_daily.json"
    if not path.exists():
        return {"ok": False, "message": "尚未找到日线缓存"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows", [])
        latest_date = rows[-1].get("date") if rows else None
        context = build_market_context(market_now(_schedule_config()))
        expected = expected_history_date(context.now, context).strftime("%Y-%m-%d")
        fresh = bool(latest_date and latest_date >= expected)
        return {
            "ok": fresh,
            "message": (
                f"缓存最新至{latest_date}，符合当前要求（期望≥{expected}）"
                if fresh
                else f"缓存最新至{latest_date or '未知'}，当前期望≥{expected}"
            ),
        }
    except Exception as exc:
        return {"ok": False, "message": f"读取日线缓存失败：{exc}"}


def _latest_status(asset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the latest report status from the selected asset's own storage."""
    asset = asset or _primary_asset()
    asset_key = _asset_identity(asset)
    tracking_code = _asset_tracking_code(asset)
    path = asset_latest_path(ROOT, asset_key)
    if not path.exists():
        return {
            "ok": False,
            "message": f"持有代码 {asset_key}（跟踪代码 {tracking_code}）尚未生成 latest.json",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = payload.get(asset_key, {})
        generated_at = result.get("generated_at") or "未知"
        market_time = result.get("market_timestamp") or "未知"
        return {
            "ok": bool(result),
            "message": f"最近结果生成于{generated_at}，行情时间{market_time}",
        }
    except Exception as exc:
        return {"ok": False, "message": f"读取 latest.json 失败：{exc}"}


def _market_status() -> dict[str, Any]:
    now = market_now(_schedule_config())
    context = build_market_context(now)
    auto = validate_run_mode("auto", now)
    intraday = validate_run_mode("intraday", now)
    close = validate_run_mode("close", now)
    return {
        "ok": True,
        "message": f"{context.phase_label} · {now:%Y-%m-%d %H:%M:%S}",
        "phase": context.phase,
        "actions": {
            "auto": {"enabled": auto.allowed, "reason": auto.message},
            "intraday": {"enabled": intraday.allowed, "reason": intraday.message},
            "close": {"enabled": close.allowed, "reason": close.message},
        },
    }


def _coerce_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数字") from exc


def _coerce_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是整数") from exc



def _transaction_context(asset_id: str | None = None) -> tuple[str, str, Path]:
    asset = _find_asset(asset_id)
    asset_key = _asset_identity(asset)
    transaction_key = str(asset.get("asset_code") or asset_key)
    tracking_code = _asset_tracking_code(asset)
    return transaction_key, tracking_code, asset_transactions_path(ROOT, asset_key)


def _asset_runtime_data_config(asset: dict[str, Any]) -> dict[str, Any]:
    """Build the same per-asset cache configuration used by ``run.py``."""
    asset_key = _asset_identity(asset)
    data_dir = asset_data_dir(ROOT, asset_key)
    return {
        "data": {
            **_global_config("data", {"data": {}}).get("data", {}),
            "cache_directory": str(asset_cache_dir(ROOT, asset_key).relative_to(ROOT)).replace("\\", "/"),
            "provider_state_file": str((data_dir / "provider_state.json").relative_to(ROOT)).replace("\\", "/"),
        },
        "storage": _global_config("storage", {"storage": {}}).get("storage", {}),
    }


def _history_frame_from_asset_cache(asset: dict[str, Any]) -> pd.DataFrame:
    asset_key = _asset_identity(asset)
    tracking_code = _asset_tracking_code(asset)
    cache_path = asset_cache_dir(ROOT, asset_key) / f"{tracking_code}_daily.json"
    cache_doc = _read_json(cache_path, {})
    rows = cache_doc.get("rows", []) if isinstance(cache_doc, dict) else []
    if not rows:
        return pd.DataFrame()
    history = pd.DataFrame(rows)
    if "date" not in history or "close" not in history:
        return pd.DataFrame()
    history["date"] = pd.to_datetime(history["date"], errors="coerce")
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    return history.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)


def _settle_pending_transactions(asset: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reconcile pending trades, refreshing this asset's daily cache when needed.

    A page refresh should not leave a prior-day trade pending merely because a
    report has not been run yet.  Refreshing is throttled per asset so repeated
    browser reloads cannot hammer the market-data providers after a failure.
    """
    asset_key = _asset_identity(asset)
    symbol = str(asset.get("asset_code") or asset_key)
    transaction_path = asset_transactions_path(ROOT, asset_key)
    now = market_now(_schedule_config())
    context = build_market_context(now)
    required_date = expected_history_date(now, context).strftime("%Y-%m-%d")

    history = _history_frame_from_asset_cache(asset)
    records = reconcile_pending_transactions(transaction_path, symbol, history)
    pending = [
        item for item in records
        if str(item.get("status", "")).upper() in {"PENDING", "PENDING_CLOSE"}
    ]
    eligible_pending = [
        item for item in pending
        if str(item.get("date") or "") <= required_date
    ]
    latest_cache_date = (
        history["date"].max().strftime("%Y-%m-%d")
        if not history.empty else ""
    )
    settlement = {
        "attempted_refresh": False,
        "refreshed": False,
        "required_date": required_date,
        "latest_cache_date": latest_cache_date,
        "message": "",
    }

    if eligible_pending and latest_cache_date < required_date:
        should_refresh = False
        with _PENDING_SETTLEMENT_LOCK:
            last_attempt = _PENDING_REFRESH_ATTEMPTS.get(asset_key)
            if last_attempt is None or now - last_attempt >= timedelta(minutes=5):
                _PENDING_REFRESH_ATTEMPTS[asset_key] = now
                should_refresh = True

        if should_refresh:
            settlement["attempted_refresh"] = True
            logger = configure_logging(
                ROOT / "logs" / "global" / "application.log",
                ROOT / "logs" / "global" / "mail.log",
            )
            refresh = refresh_history_cache_now(
                asset,
                _asset_runtime_data_config(asset),
                ROOT,
                logger,
                now,
            )
            settlement["refreshed"] = bool(refresh.get("success"))
            settlement["message"] = str(refresh.get("message") or "")
            if refresh.get("success"):
                history = _history_frame_from_asset_cache(asset)
                records = reconcile_pending_transactions(transaction_path, symbol, history)
                settlement["latest_cache_date"] = (
                    history["date"].max().strftime("%Y-%m-%d")
                    if not history.empty else ""
                )
        else:
            settlement["message"] = "已在最近五分钟内尝试刷新收盘数据。"

    return records, settlement


def _list_transactions(asset_id: str | None = None) -> dict[str, Any]:
    try:
        symbol, tracking_code, transaction_path = _transaction_context(asset_id)
        asset = _find_asset(asset_id)
    except ValueError:
        return {"success": True, "symbol": "", "tracking_code": "", "transactions": [], "records": []}
    records, settlement = _settle_pending_transactions(asset)
    transactions = list(reversed(records))
    return {
        "success": True,
        "symbol": symbol,
        "tracking_code": tracking_code,
        "transactions": transactions,
        "records": transactions,
        "settlement": settlement,
    }


def _transaction_chart_data(asset_id: str | None = None) -> dict[str, Any]:
    """Return an asset's tracking-code price history and recorded trades."""
    try:
        symbol, tracking_code, transaction_path = _transaction_context(asset_id)
        asset = _find_asset(asset_id)
    except ValueError:
        return {"success": True, "history": [], "transactions": []}

    transactions, settlement = _settle_pending_transactions(asset)
    cache_path = asset_cache_dir(ROOT, _asset_identity(asset)) / f"{tracking_code}_daily.json"
    cache_doc = _read_json(cache_path, {})
    history: list[dict[str, Any]] = []
    for row in cache_doc.get("rows", []):
        if not isinstance(row, dict):
            continue
        try:
            price = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        date_text = str(row.get("date") or "")[:10]
        if date_text and price > 0:
            history.append({"date": date_text, "price": price})

    return {
        "success": True,
        "symbol": symbol,
        "tracking_code": tracking_code,
        "history": history,
        "transactions": list(reversed(transactions)),
        "settlement": settlement,
    }


def _save_transaction_record(payload: dict[str, Any], asset_id: str | None = None) -> dict[str, Any]:
    symbol, tracking_code, transaction_path = _transaction_context(asset_id)
    _backup_file(transaction_path)
    record = save_transaction(
        transaction_path,
        symbol,
        payload,
    )

    # When the selected trade date already exists in the local daily cache,
    # calculate shares and price immediately. Otherwise the record remains
    # pending and run.py will reconcile it after fresh daily data is available.
    cache_path = asset_cache_dir(ROOT, _asset_identity(_find_asset(asset_id))) / f"{tracking_code}_daily.json"
    cache_doc = _read_json(cache_path, {})
    rows = cache_doc.get("rows", [])
    if rows:
        history = pd.DataFrame(rows)
        if "date" in history:
            history["date"] = pd.to_datetime(
                history["date"],
                errors="coerce",
            )
            records = reconcile_pending_transactions(
                transaction_path,
                symbol,
                history,
            )
            record = next(
                (
                    item
                    for item in records
                    if item.get("id") == record.get("id")
                ),
                record,
            )

    calculated = record.get("status") == "ESTIMATED"
    return {
        "success": True,
        "message": (
            (
                "交易记录已更新，并已根据该交易日收盘价自动计算份额和均价。"
                if payload.get("id")
                else "交易记录已新增，并已根据该交易日收盘价自动计算份额和均价。"
            )
            if calculated
            else (
                (
                    "交易记录已更新；该日期收盘数据尚不可用，将在后续报告运行时自动计算。"
                    if payload.get("id")
                    else "交易记录已新增；该日期收盘数据尚不可用，将在后续报告运行时自动计算。"
                )
            )
        ),
        "record": record,
    }


def _delete_transaction_record(transaction_id: str, asset_id: str | None = None) -> dict[str, Any]:
    symbol, tracking_code, transaction_path = _transaction_context(asset_id)
    _backup_file(transaction_path)
    deleted = delete_transaction(
        transaction_path,
        symbol,
        transaction_id,
    )
    if not deleted:
        raise ValueError("没有找到该交易记录")
    return {
        "success": True,
        "message": "交易记录已删除。",
    }


def _delete_transaction_records(transaction_ids: list[str], asset_id: str | None = None) -> dict[str, Any]:
    ids = [str(transaction_id) for transaction_id in transaction_ids if str(transaction_id)]
    if not ids:
        raise ValueError("Please select transaction records to delete.")

    symbol, _tracking_code, transaction_path = _transaction_context(asset_id)
    _backup_file(transaction_path)
    deleted_count = delete_transactions(transaction_path, symbol, ids)
    if not deleted_count:
        raise ValueError("Selected transaction records were not found.")
    return {
        "success": True,
        "message": f"Deleted {deleted_count} transaction record(s).",
        "deleted_count": deleted_count,
    }


SCHEDULE_PLANS_FILE = ROOT / "data" / "global" / "schedule_plans.json"
SCHEDULE_WRAPPER_DIR = ROOT / "data" / "global" / "scheduled_tasks"
TASK_PREFIX = "ETFMonitor_"

DAY_ORDER = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_LABELS = {
    "MON": "周一",
    "TUE": "周二",
    "WED": "周三",
    "THU": "周四",
    "FRI": "周五",
    "SAT": "周六",
    "SUN": "周日",
}


def _system_task_names() -> list[str]:
    if os.name != "nt":
        return []
    completed = _run_schtasks([
        "/Query",
        "/FO",
        "CSV",
        "/NH",
    ])
    if completed.returncode != 0:
        return []

    names: list[str] = []
    rows = csv.reader(io.StringIO(completed.stdout))
    for row in rows:
        if not row:
            continue
        name = str(row[0]).strip().lstrip("\\")
        if (
            name.startswith(TASK_PREFIX)
            and name != "ETFMonitor_KeepAwake"
        ):
            names.append(name)
    return names


def _xml_text(root, local_name: str) -> str | None:
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == local_name:
            return node.text
    return None


def _query_windows_task_xml(task_name: str) -> str | None:
    completed = _run_schtasks([
        "/Query",
        "/TN",
        task_name,
        "/XML",
    ])
    if completed.returncode != 0:
        return None
    xml_text = completed.stdout.strip()
    return xml_text or None


def _normalize_task_path(value: str) -> str:
    return str(value or "").strip().strip('"').replace("/", "\\").rstrip("\\").lower()


def _task_uses_hidden_runner(
    plan: dict[str, Any],
    task_xml: str | None = None,
) -> bool:
    task_xml = task_xml or _query_windows_task_xml(str(plan["task_name"]))
    if not task_xml:
        return False
    try:
        root = ET.fromstring(task_xml)
    except ET.ParseError:
        return False

    command = _xml_text(root, "Command") or ""
    arguments = _xml_text(root, "Arguments") or ""
    expected_command = _normalize_task_path(str(_schedule_pythonw()))
    actual_command = _normalize_task_path(command)
    normalized_arguments = arguments.replace("/", "\\").lower()
    expected_runner = str(ROOT / "scheduled_runner.py").replace("/", "\\").lower()
    expected_plan_id = str(plan["id"]).lower()

    return (
        actual_command == expected_command
        and expected_runner in normalized_arguments
        and "--plan-id" in normalized_arguments
        and expected_plan_id in normalized_arguments
    )


def _ensure_schedule_task_current(
    plan: dict[str, Any],
    task_xml: str | None = None,
) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "ok": True,
            "migrated": False,
            "message": "当前不是Windows，无需迁移系统任务。",
        }
    if not _task_exists(str(plan["task_name"])):
        return {
            "ok": True,
            "migrated": False,
            "message": "系统任务不存在，未执行迁移。",
        }
    if _task_uses_hidden_runner(plan, task_xml):
        return {
            "ok": True,
            "migrated": False,
            "message": "系统任务已使用隐藏运行器。",
        }

    sync_result = _sync_plan_to_windows(plan)
    return {
        "ok": bool(sync_result.get("ok")),
        "migrated": bool(sync_result.get("ok")),
        "message": (
            "已将旧版CMD定时任务迁移为后台隐藏运行器。"
            if sync_result.get("ok")
            else sync_result.get("message", "迁移系统任务失败")
        ),
    }


def _import_plan_from_windows_task(
    task_name: str,
    task_xml: str | None = None,
) -> dict[str, Any] | None:
    task_xml = task_xml or _query_windows_task_xml(task_name)
    if not task_xml:
        return None

    try:
        root = ET.fromstring(task_xml)
    except ET.ParseError:
        return None

    start_boundary = _xml_text(root, "StartBoundary")
    if not start_boundary:
        return None
    try:
        run_time = datetime.fromisoformat(start_boundary).strftime("%H:%M")
    except ValueError:
        return None

    day_map = {
        "Monday": "MON",
        "Tuesday": "TUE",
        "Wednesday": "WED",
        "Thursday": "THU",
        "Friday": "FRI",
        "Saturday": "SAT",
        "Sunday": "SUN",
    }
    weekdays = []
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local in day_map:
            weekdays.append(day_map[local])
    if not weekdays:
        weekdays = ["MON", "TUE", "WED", "THU", "FRI"]

    arguments = _xml_text(root, "Arguments") or ""
    description = _xml_text(root, "Description") or ""
    mode_match = re.search(
        r"--mode\s+[\"']?(auto|intraday|close)",
        arguments,
        re.IGNORECASE,
    )
    slot_match = re.search(
        r"--slot\s+[\"']?(\d{2}:\d{2})",
        arguments,
    )
    plan_id_match = re.search(
        r"--plan-id\s+[\"']?([0-9a-fA-F-]{36})",
        arguments,
    )

    mode = (
        mode_match.group(1).lower()
        if mode_match
        else (
            "close"
            if run_time >= "15:05"
            else "intraday"
        )
    )
    if slot_match:
        run_time = slot_match.group(1)

    plan_id = (
        _normalize_plan_id(plan_id_match.group(1))
        if plan_id_match
        else str(uuid.uuid4())
    )
    enabled_text = _xml_text(root, "Enabled")
    enabled = str(enabled_text or "true").lower() != "false"

    name = description.strip()
    if name.startswith("ETF Monitor定时报告："):
        name = name.split("：", 1)[1].strip()
    if not name:
        name = f"导入的系统计划 {run_time}"

    now_text = datetime.now().isoformat(timespec="seconds")
    return {
        "id": plan_id,
        "name": name[:60],
        "mode": mode,
        "time": run_time,
        "weekdays": _normalize_weekdays(weekdays),
        "enabled": enabled,
        "task_name": task_name,
        "created_at": now_text,
        "updated_at": now_text,
        "imported_from_windows": True,
    }


def _import_existing_windows_plans() -> dict[str, Any]:
    if os.name != "nt":
        return {
            "imported": 0,
            "migrated": 0,
            "migration_failures": [],
            "message": "当前不是Windows，未扫描系统任务。",
        }

    plans = _load_schedule_plans_raw()
    known_by_task = {
        str(item.get("task_name", "")): item
        for item in plans
        if item.get("task_name")
    }
    imported: list[dict[str, Any]] = []
    migrated: list[dict[str, Any]] = []
    migration_failures: list[str] = []

    for task_name in _system_task_names():
        task_xml = _query_windows_task_xml(task_name)
        plan = known_by_task.get(task_name)
        if plan is None:
            plan = _import_plan_from_windows_task(task_name, task_xml)
            if plan is None:
                continue
            plans.append(plan)
            known_by_task[task_name] = plan
            imported.append(plan)

        migration = _ensure_schedule_task_current(plan, task_xml)
        if migration.get("migrated"):
            migrated.append(plan)
        elif not migration.get("ok"):
            migration_failures.append(
                f'{plan.get("name", task_name)}: {migration.get("message", "迁移失败")}'
            )

    if imported:
        _save_schedule_plans(plans)

    parts = []
    if imported:
        parts.append(f"导入{len(imported)}个旧计划")
    if migrated:
        parts.append(f"迁移{len(migrated)}个旧版CMD任务为后台隐藏运行器")
    if migration_failures:
        parts.append(f"{len(migration_failures)}个任务迁移失败")
    if not parts:
        parts.append("未发现需要导入或迁移的旧计划")

    return {
        "imported": len(imported),
        "migrated": len(migrated),
        "migration_failures": migration_failures,
        "message": "；".join(parts) + "。",
    }


def _load_schedule_plans_raw() -> list[dict[str, Any]]:
    payload = _read_json(SCHEDULE_PLANS_FILE, {"plans": []})
    plans = payload.get("plans", [])
    return plans if isinstance(plans, list) else []

def _load_schedule_plans() -> list[dict[str, Any]]:
    return _load_schedule_plans_raw()


def _save_schedule_plans(plans: list[dict[str, Any]]) -> None:
    _backup_file(SCHEDULE_PLANS_FILE)
    _atomic_write_json(SCHEDULE_PLANS_FILE, {"plans": plans})


def _normalize_plan_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("计划名称不能为空")
    if len(name) > 60:
        raise ValueError("计划名称不能超过60个字符")
    return name


def _normalize_plan_time(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
        raise ValueError("运行时间必须使用 HH:MM 格式")
    return text


def _normalize_plan_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in {"auto", "intraday", "close"}:
        raise ValueError("运行模式必须是 auto、intraday 或 close")
    return mode


def _normalize_weekdays(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("运行日期格式错误")
    weekdays = []
    for item in value:
        day = str(item).upper().strip()
        if day in DAY_ORDER and day not in weekdays:
            weekdays.append(day)
    weekdays.sort(key=DAY_ORDER.index)
    if not weekdays:
        raise ValueError("至少选择一个运行日期")
    return weekdays


def _validate_plan_time_for_mode(mode: str, run_time: str) -> None:
    hour, minute = [int(part) for part in run_time.split(":")]
    minutes = hour * 60 + minute
    if mode == "intraday" and not (9 * 60 + 30 <= minutes <= 15 * 60):
        raise ValueError("盘中报告计划只能设置在09:30至15:00之间")
    if mode == "close" and minutes < 15 * 60 + 5:
        raise ValueError("收盘报告计划最早只能设置为15:05")


def _normalize_plan_id(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("定时计划ID无效") from exc


def _plan_task_name(plan_id: str) -> str:
    safe_id = _normalize_plan_id(plan_id)
    return f"{TASK_PREFIX}{safe_id.replace('-', '')}"


def _plan_wrapper_path(plan_id: str) -> Path:
    return SCHEDULE_WRAPPER_DIR / f"{plan_id}.cmd"


def _write_plan_wrapper(plan: dict[str, Any]) -> Path:
    """Write a legacy wrapper without a UTF-8 BOM.

    Current Windows tasks use scheduled_runner.py through pythonw.exe and do
    not need this file. Keeping the wrapper valid allows old/manual entries to
    run without CMD interpreting the BOM as the characters "ï»¿".
    """
    SCHEDULE_WRAPPER_DIR.mkdir(parents=True, exist_ok=True)
    wrapper_path = _plan_wrapper_path(plan["id"])
    mode = plan["mode"]
    run_time = plan["time"]
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f'cd /d "{ROOT}"',
        'if not exist "logs" mkdir "logs"',
        f'set "PYTHON_EXE={ROOT / ".venv" / "Scripts" / "python.exe"}"',
        'if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"',
        f'echo [%date% %time%] Scheduled plan {plan["id"]} started. >> "logs\\global\\scheduler.log"',
        f'"%PYTHON_EXE%" run.py --mode {mode} --slot {run_time} >> "logs\\global\\scheduler.log" 2>&1',
        f'echo [%date% %time%] Scheduled plan {plan["id"]} finished with code %errorlevel%. >> "logs\\global\\scheduler.log"',
    ]
    wrapper_path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    return wrapper_path


def _run_schtasks(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    if os.name != "nt":
        raise RuntimeError("Windows任务计划程序只支持Windows")
    return subprocess.run(
        ["schtasks", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _delete_windows_task(task_name: str, ignore_missing: bool = True) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "ok": False,
            "message": "当前不是Windows，未修改系统任务计划程序。",
        }
    completed = _run_schtasks(["/Delete", "/TN", task_name, "/F"])
    if completed.returncode == 0:
        return {"ok": True, "message": "系统任务已删除"}
    combined = f"{completed.stdout}\n{completed.stderr}".strip()
    if ignore_missing:
        return {"ok": True, "message": "系统任务原本不存在"}
    return {"ok": False, "message": combined or "删除系统任务失败"}


def _task_exists(task_name: str) -> bool:
    if os.name != "nt":
        return False
    completed = _run_schtasks(["/Query", "/TN", task_name])
    return completed.returncode == 0


def _schedule_status_path(plan_id: str) -> Path:
    return ROOT / "data" / "global" / "schedule_status" / f"{plan_id}.json"


def _schedule_task_xml_path(plan_id: str) -> Path:
    return SCHEDULE_WRAPPER_DIR / f"{plan_id}.xml"


def _schedule_pythonw() -> Path:
    candidate = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate
    fallback = Path(sys.executable).with_name("pythonw.exe")
    return fallback if fallback.exists() else Path(sys.executable)


def _current_user_sid() -> str:
    completed = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=15,
        creationflags=(
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt"
            else 0
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or "无法读取当前Windows用户SID"
        )
    rows = list(
        csv.reader(io.StringIO(completed.stdout.strip()))
    )
    if not rows or len(rows[0]) < 2:
        raise RuntimeError("无法解析当前Windows用户SID")
    return rows[0][1].strip()


def _days_xml(weekdays: list[str]) -> str:
    mapping = {
        "MON": "Monday",
        "TUE": "Tuesday",
        "WED": "Wednesday",
        "THU": "Thursday",
        "FRI": "Friday",
        "SAT": "Saturday",
        "SUN": "Sunday",
    }
    return "".join(
        f"<{mapping[day]} />"
        for day in weekdays
        if day in mapping
    )


def _schedule_task_xml(plan: dict[str, Any]) -> str:
    start_boundary = (
        datetime.now().strftime("%Y-%m-%d")
        + f"T{plan['time']}:00"
    )
    command = html.escape(str(_schedule_pythonw()))
    runner = ROOT / "scheduled_runner.py"
    safe_plan_name = str(plan["name"]).replace('"', "'")
    arguments = html.escape(
        f'"{runner}" '
        f'--plan-id "{plan["id"]}" '
        f'--mode "{plan["mode"]}" '
        f'--slot "{plan["time"]}" '
        f'--name "{safe_plan_name}" ' f'--asset "{plan.get("asset_id", "")}"'
    )
    working_dir = html.escape(str(ROOT))
    sid = html.escape(_current_user_sid())
    enabled = "true" if plan.get("enabled", True) else "false"

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>ETF Monitor定时报告：{html.escape(plan["name"])}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start_boundary}</StartBoundary>
      <Enabled>{enabled}</Enabled>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>{_days_xml(plan["weekdays"])}</DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{sid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>{enabled}</Enabled>
    <Hidden>true</Hidden>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _next_schedule_run(plan: dict[str, Any]) -> str | None:
    if not plan.get("enabled", True):
        return None
    day_index = {
        "MON": 0,
        "TUE": 1,
        "WED": 2,
        "THU": 3,
        "FRI": 4,
        "SAT": 5,
        "SUN": 6,
    }
    allowed = {
        day_index[day]
        for day in plan.get("weekdays", [])
        if day in day_index
    }
    if not allowed:
        return None

    hour, minute = [
        int(part) for part in plan["time"].split(":")
    ]
    now = datetime.now()
    for offset in range(0, 8):
        candidate_date = now.date() + timedelta(days=offset)
        if candidate_date.weekday() not in allowed:
            continue
        candidate = datetime.combine(
            candidate_date,
            datetime.min.time(),
        ).replace(hour=hour, minute=minute)
        if candidate > now:
            return candidate.isoformat(timespec="minutes")
    return None


def _schedule_runtime_status(plan_id: str) -> dict[str, Any]:
    payload = _read_json(
        _schedule_status_path(plan_id),
        {},
    )
    return payload if isinstance(payload, dict) else {}

def _sync_plan_to_windows(plan: dict[str, Any]) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "ok": False,
            "message": "计划已保存，但当前不是Windows，无法创建系统定时任务。",
        }

    SCHEDULE_WRAPPER_DIR.mkdir(parents=True, exist_ok=True)
    xml_path = _schedule_task_xml_path(plan["id"])
    xml_path.write_text(
        _schedule_task_xml(plan),
        encoding="utf-16",
    )
    completed = _run_schtasks([
        "/Create",
        "/TN",
        plan["task_name"],
        "/XML",
        str(xml_path),
        "/F",
    ])
    if completed.returncode != 0:
        combined = f"{completed.stdout}\n{completed.stderr}".strip()
        return {
            "ok": False,
            "message": combined or "创建Windows定时任务失败",
        }
    return {
        "ok": True,
        "message": (
            "Windows定时任务已同步。保存计划不会立即运行；"
            "只有到达设定时间或点击“立即运行”才会执行。"
        ),
    }


def _schedule_plan_view(plan: dict[str, Any]) -> dict[str, Any]:
    task_exists = (
        _task_exists(plan["task_name"])
        if os.name == "nt"
        else False
    )
    runtime = _schedule_runtime_status(plan["id"])
    return {
        **plan,
        "weekday_labels": [
            DAY_LABELS[day]
            for day in plan["weekdays"]
        ],
        "system_task_exists": task_exists,
        "platform_supported": os.name == "nt",
        "next_run": _next_schedule_run(plan),
        "last_run_started": runtime.get("started_at"),
        "last_run_finished": runtime.get("finished_at"),
        "last_result": runtime.get("status"),
        "last_message": runtime.get("message"),
        "last_return_code": runtime.get("return_code"),
        "last_report_generated": runtime.get(
            "report_generated"
        ),
        "last_email_status": runtime.get("email_status"),
        "running": runtime.get("status") == "RUNNING",
    }


def _upsert_schedule_plan(payload: dict[str, Any]) -> dict[str, Any]:
    plans = _load_schedule_plans()
    raw_plan_id = payload.get("id")
    plan_id = (
        _normalize_plan_id(raw_plan_id)
        if raw_plan_id
        else str(uuid.uuid4())
    )
    existing = next((item for item in plans if item.get("id") == plan_id), None)

    name = _normalize_plan_name(payload.get("name"))
    mode = _normalize_plan_mode(payload.get("mode"))
    run_time = _normalize_plan_time(payload.get("time"))
    weekdays = _normalize_weekdays(payload.get("weekdays"))
    enabled = bool(payload.get("enabled", True))
    asset_id = str(payload.get("asset_id", "")).strip()
    if not asset_id:
        raise ValueError("定时计划必须绑定一只资产")
    _find_asset(asset_id)
    _validate_plan_time_for_mode(mode, run_time)

    now_text = datetime.now().isoformat(timespec="seconds")
    plan = {
        "id": plan_id,
        "name": name,
        "mode": mode,
        "time": run_time,
        "weekdays": weekdays,
        "enabled": enabled,
        "asset_id": asset_id,
        "task_name": _plan_task_name(plan_id),
        "created_at": existing.get("created_at", now_text) if existing else now_text,
        "updated_at": now_text,
    }

    if existing and existing.get("task_name") and existing.get("task_name") != plan["task_name"]:
        _delete_windows_task(str(existing["task_name"]), ignore_missing=True)

    sync_result = _sync_plan_to_windows(plan)
    if os.name == "nt" and not sync_result["ok"]:
        raise RuntimeError(sync_result["message"])

    if existing:
        plans = [plan if item.get("id") == plan_id else item for item in plans]
        action = "修改"
    else:
        plans.append(plan)
        action = "新增"

    plans.sort(key=lambda item: (item.get("time", ""), item.get("name", "")))
    _save_schedule_plans(plans)

    return {
        "success": True,
        "message": f"定时计划已{action}。",
        "plan": _schedule_plan_view(plan),
        "system_message": sync_result["message"],
    }


def _delete_schedule_plan(plan_id: str) -> dict[str, Any]:
    plan_id = _normalize_plan_id(plan_id)
    plans = _load_schedule_plans()
    plan = next((item for item in plans if item.get("id") == plan_id), None)
    if not plan:
        raise ValueError("没有找到该定时计划")

    delete_result = _delete_windows_task(plan["task_name"], ignore_missing=True)
    if os.name == "nt" and not delete_result["ok"]:
        raise RuntimeError(delete_result["message"])

    _plan_wrapper_path(plan_id).unlink(missing_ok=True)
    _schedule_task_xml_path(plan_id).unlink(missing_ok=True)
    _schedule_status_path(plan_id).unlink(missing_ok=True)
    plans = [item for item in plans if item.get("id") != plan_id]
    _save_schedule_plans(plans)
    return {
        "success": True,
        "message": "定时计划已删除。",
        "system_message": delete_result["message"],
    }


def _run_schedule_plan_now(plan_id: str) -> dict[str, Any]:
    plan_id = _normalize_plan_id(plan_id)
    plans = _load_schedule_plans()
    plan = next((item for item in plans if item.get("id") == plan_id), None)
    if not plan:
        raise ValueError("没有找到该定时计划")
    if not plan.get("enabled", True):
        raise ValueError("该定时计划当前已停用")

    if os.name == "nt" and _task_exists(plan["task_name"]):
        migration = _ensure_schedule_task_current(plan)
        if not migration.get("ok"):
            raise RuntimeError(
                migration.get("message", "系统定时任务迁移失败")
            )
        completed = _run_schtasks(["/Run", "/TN", plan["task_name"]])
        if completed.returncode != 0:
            combined = f"{completed.stdout}\n{completed.stderr}".strip()
            raise RuntimeError(combined or "启动系统任务失败")
        return {
            "success": True,
            "message": "系统定时任务已开始运行，请稍后查看报告和日志。",
        }

    result = _run_monitor(plan["mode"], plan["time"])
    return {
        **result,
        "message": result.get("message", "计划运行结束"),
    }


def _sync_all_schedule_plans() -> dict[str, Any]:
    import_result = _import_existing_windows_plans()
    plans = _load_schedule_plans()
    results = []
    failures = []
    for plan in plans:
        sync_result = _sync_plan_to_windows(plan)
        results.append({
            "id": plan["id"],
            "name": plan["name"],
            **sync_result,
        })
        if not sync_result["ok"]:
            failures.append(f'{plan["name"]}: {sync_result["message"]}')

    return {
        "success": not failures,
        "message": (
            f"已同步{len(results)}个定时计划。"
            if not failures
            else "部分定时计划同步失败：" + "；".join(failures)
        ),
        "results": results,
        "import_result": import_result,
    }


# Clean V13.1 data/config layer overrides. These definitions intentionally
# replace the legacy single-file helpers above without carrying old runtime data.
def _global_config(name: str, default: dict[str, Any]) -> dict[str, Any]:
    return _read_json(ROOT / "config" / "global" / f"{name}.json", default)


def _asset_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _asset_identity(item),
        "name": item.get("name", ""),
        "asset_code": item.get("asset_code", ""),
        "tracking_name": item.get("tracking_name", ""),
        "tracking_code": item.get("tracking_code", item.get("symbol", "")),
        "enabled": item.get("enabled", True),
        "has_report": asset_report_path(ROOT, _asset_identity(item)).exists(),
        "report_url": f"/report?asset_id={_asset_identity(item)}",
        "history_url": f"/history?asset_id={_asset_identity(item)}",
    }


def _homepage_asset_position_summary(asset: dict[str, Any]) -> dict[str, Any]:
    """Return one asset's settled position for the homepage card.

    During a trading day this intentionally values the position with the most
    recent daily close, rather than an intraday quote.  A same-day pending buy
    is called out separately and is not included until its closing price can
    be confirmed.
    """
    asset_key = _asset_identity(asset)
    tracking_code = _asset_tracking_code(asset)
    now = market_now(_schedule_config())
    today = now.strftime("%Y-%m-%d")
    transactions, settlement = _settle_pending_transactions(asset)
    pending_buys = [
        item for item in transactions
        if item.get("type") == "BUY"
        if str(item.get("status", "")).upper() in {"PENDING", "PENDING_CLOSE"}
    ]
    pending_today_buys = [item for item in pending_buys if item.get("date") == today]
    pending_buy_amount = round(sum(float(item.get("amount", 0) or 0) for item in pending_buys), 2)

    cache = _read_json(
        asset_cache_dir(ROOT, asset_key) / f"{tracking_code}_daily.json",
        {},
    )
    rows = cache.get("rows", []) if isinstance(cache, dict) else []
    latest_row = rows[-1] if rows and isinstance(rows[-1], dict) else {}
    close_price = latest_row.get("close")
    close_date = str(latest_row.get("date") or "")
    try:
        close_price = float(close_price)
    except (TypeError, ValueError):
        close_price = 0.0

    if close_price > 0:
        position = calculate_position(transactions, close_price)
        return {
            "success": True,
            "asset": _asset_summary(asset),
            "position": position,
            "earnings_as_of": close_date,
            "uses_previous_close": close_date < today,
            "today_buy_amount": round(sum(float(item.get("amount", 0) or 0) for item in pending_today_buys), 2),
            "today_buy_pending": bool(pending_today_buys),
            "pending_buy_amount": pending_buy_amount,
            "pending_buy_count": len(pending_buys),
            "pending_buy_dates": sorted({str(item.get("date") or "") for item in pending_buys if item.get("date")}),
            "settlement": settlement,
            "valuation_basis": "tracking_close",
            "valuation_basis_label": f"\u6309\u8ddf\u8e2a\u4ee3\u7801 {tracking_code} \u7684\u65e5\u7ebf\u6536\u76d8\u4ef7\u4f30\u7b97\uff0c\u4e0d\u7b49\u540c\u4e8e\u6301\u6709\u57fa\u91d1\u51c0\u503c",
            "message": (
                f"收益按最近可用收盘价（{close_date}）计算"
                if close_date < today else f"收益按收盘价（{close_date}）计算"
            ),
        }

    latest_doc = _read_json(asset_latest_path(ROOT, asset_key), {})
    latest_result = latest_doc.get(asset_key, {}) if isinstance(latest_doc, dict) else {}
    position = latest_result.get("position_metrics", {}) if isinstance(latest_result, dict) else {}
    return {
        "success": bool(position),
        "asset": _asset_summary(asset),
        "position": position,
        "earnings_as_of": "",
        "uses_previous_close": True,
        "today_buy_amount": round(sum(float(item.get("amount", 0) or 0) for item in pending_today_buys), 2),
        "today_buy_pending": bool(pending_today_buys),
        "pending_buy_amount": pending_buy_amount,
        "pending_buy_count": len(pending_buys),
        "pending_buy_dates": sorted({str(item.get("date") or "") for item in pending_buys if item.get("date")}),
        "settlement": settlement,
        "message": "暂无可用收盘价，收益沿用最近一次已生成结果",
    }


def _load_system_settings() -> dict[str, Any]:
    """Return only configuration that is global to every asset."""
    email = _global_config("email", {"email": {}}).get("email", {})
    email = {**email, "triggers": migrate_email_triggers(email.get("triggers", {}))}
    return {
        "email": email,
        "llm": _global_config("llm", {"llm": {}}).get("llm", {}),
        "data": _global_config("data", {"data": {}}).get("data", {}),
        "report": _global_config("report", {"report": {}}).get("report", {}),
    }


def _save_system_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Save global settings without requiring or modifying an asset."""
    docs = {
        "email": _global_config("email", {"email": {}}),
        "llm": _global_config("llm", {"llm": {}}),
        "data": _global_config("data", {"data": {}}),
        "report": _global_config("report", {"report": {}}),
    }
    changed_docs: set[str] = set()
    changes: list[str] = []

    def update_value(document: str, container: dict[str, Any], key: str, value: Any, label: str) -> None:
        if container.get(key) != value:
            container[key] = value
            changed_docs.add(document)
            changes.append(f"{label}: 已更新")

    def update_integer(document: str, container: dict[str, Any], key: str, value: Any, label: str) -> None:
        if value not in (None, ""):
            update_value(document, container, key, _coerce_int(value, label), label)

    llm = docs["llm"].setdefault("llm", {})
    for key, label in {
        "enabled": "启用本地模型",
        "model": "主模型",
        "keep_alive": "keep_alive",
    }.items():
        if key in payload.get("llm", {}):
            update_value("llm", llm, key, payload["llm"][key], label)
    for key, label in {
        "timeout_seconds": "模型超时",
        "max_summary_chars": "最大摘要长度",
        "num_ctx": "上下文长度",
        "num_predict": "最大生成 Token",
    }.items():
        update_integer("llm", llm, key, payload.get("llm", {}).get(key), label)

    email = docs["email"].setdefault("email", {})
    migrated_triggers = migrate_email_triggers(email.get("triggers", {}))
    if email.get("triggers") != migrated_triggers:
        email["triggers"] = migrated_triggers
        changed_docs.add("email")
    for key, label in {
        "enabled": "启用邮件",
        "recipient": "收件人",
        "include_llm_summary": "包含模型摘要",
        "include_raw_metrics": "包含原始指标",
    }.items():
        if key in payload.get("email", {}):
            update_value("email", email, key, payload["email"][key], label)
    debug = email.setdefault("debug", {})
    if "force_send_email" in payload.get("email", {}):
        update_value("email", debug, "force_send_email", bool(payload["email"]["force_send_email"]), "强制发送（调试）")
    update_integer("email", email, "data_error_dedup_minutes", payload.get("email", {}).get("data_error_dedup_minutes"), "DATA_ERROR 去重分钟")
    triggers = payload.get("email", {}).get("triggers")
    if isinstance(triggers, dict):
        triggers = migrate_email_triggers(triggers)
        configured_triggers = email.setdefault("triggers", {})
        for status in {"OBSERVE", "REVIEW_BUY", "PARTIAL_SELL"}:
            if status in triggers:
                update_value("email", configured_triggers, status, bool(triggers[status]), f"邮件触发 {status}")

    data = docs["data"].setdefault("data", {})
    update_integer("data", data, "history_days", payload.get("data", {}).get("history_days"), "历史交易日数量")
    retry = data.setdefault("retry", {})
    update_integer("data", retry, "max_attempts", payload.get("data", {}).get("retry_max_attempts"), "数据重试次数")

    report = docs["report"].setdefault("report", {})
    update_integer("report", report, "retention_days", payload.get("report", {}).get("retention_days"), "报告保留天数")
    update_integer("report", report, "max_history_files", payload.get("report", {}).get("max_history_files"), "历史报告最大数量")

    for name in changed_docs:
        _atomic_write_json(ROOT / "config" / "global" / f"{name}.json", docs[name])
    return {
        "success": True,
        "message": "系统设置已保存。" if changes else "没有检测到需要保存的修改。",
        "changes": changes,
    }


def _load_settings(asset_id: str | None = None) -> dict[str, Any]:
    assets = _all_assets()
    asset = _find_asset(asset_id) if assets and asset_id else (assets[0] if assets else {})
    transactions: list[dict[str, Any]] = []
    if asset:
        key = _asset_identity(asset)
        transaction_key = str(asset.get("asset_code") or key)
        transactions = load_transactions(asset_transactions_path(ROOT, key), transaction_key)
    return {
        "asset": asset,
        "assets": [_asset_summary(item) for item in assets],
        "email": _global_config("email", {"email": {}}).get("email", {}),
        "llm": _global_config("llm", {"llm": {}}).get("llm", {}),
        "data": _global_config("data", {"data": {}}).get("data", {}),
        "report": _global_config("report", {"report": {}}).get("report", {}),
        "schedule": _global_config("schedule", {"schedule": {}}).get("schedule", {}),
        "transactions": transactions,
    }


def _save_settings(payload: dict[str, Any], asset_id: str | None = None) -> dict[str, Any]:
    wanted = str(asset_id or payload.get("asset_id") or "").strip()
    if not wanted:
        raise ValueError("请先选择要编辑的资产")
    current = _find_asset(wanted)
    old_id = _asset_identity(current)
    asset_payload = payload.get("asset", {})
    strategy_payload = payload.get("strategy", {})
    updated = dict(current)
    changes: list[str] = []
    labels = {
        "name": "资产名称", "asset_code": "持有代码",
        "tracking_code": "跟踪代码",
        "tracking_name": "盘中跟踪名称",
    }
    for key, label in labels.items():
        if key in asset_payload:
            value = str(asset_payload.get(key, "")).strip()
            if not value:
                raise ValueError(f"{label}不能为空")
            if updated.get(key) != value:
                changes.append(f"{label}: {updated.get(key)} → {value}")
                updated[key] = value
    if "enabled" in asset_payload:
        updated["enabled"] = bool(asset_payload["enabled"])
    updated["id"] = str(updated.get("asset_code") or old_id)
    updated["symbol"] = str(updated.get("tracking_code") or "")
    strategy = dict(updated.get("strategy", {}))
    integer_fields = {
        "stable_days_required",
        "consecutive_down_warning_days",
        "new_low_window_days",
        "sell_first_max_reminder_days",
        "sell_residual_max_reminder_days",
    }
    allowed_strategy_fields = {
        "strategy_type", "cycle_confirmation_pct", "consecutive_down_warning_days",
        "new_low_window_days", "stable_days_required", "stable_range_pct",
        "normal_add_amount", "enhanced_add_amount", "rebound_recovery_threshold_pct",
        "deep_drawdown_percentile", "sell_first_percentile", "sell_second_percentile",
        "sell_early_alert_points", "sell_first_max_reminder_days",
        "sell_first_stop_reduction_pct", "sell_residual_position_pct",
        "sell_residual_max_reminder_days",
    }
    for key, value in strategy_payload.items():
        if key not in allowed_strategy_fields or value in (None, ""):
            continue
        if key == "strategy_type":
            strategy[key] = str(value).upper() if str(value).upper() in {"TREND", "CYCLICAL"} else "CYCLICAL"
        else:
            strategy[key] = _coerce_int(value, key) if key in integer_fields else _coerce_float(value, key)
    updated["strategy"] = strategy
    saved = save_asset(ROOT, updated, old_id=old_id)
    return {"success": True, "message": "资产与策略已保存。", "changes": changes, "asset": saved}


def _environment_status(asset_id: str | None = None) -> dict[str, Any]:
    assets = _all_assets()
    selected = _find_asset(asset_id) if assets else None
    token_path = ROOT / "config" / "private" / "msal_token_cache.json"
    report_exists = bool(selected and asset_report_path(ROOT, _asset_identity(selected)).exists())
    packages = {}
    for package in ["pandas", "akshare", "msal", "flask", "requests", "playwright"]:
        try:
            __import__(package); packages[package] = True
        except Exception:
            packages[package] = False
    empty = {"ok": False, "message": "尚未添加资产"}
    required_config = ["app.json", "data.json", "email.json", "llm.json", "report.json", "schedule.json", "storage.json"]
    missing_config = [name for name in required_config if not (ROOT / "config" / "global" / name).exists()]
    runtime_paths = [ROOT / "data", ROOT / "logs", ROOT / "output", ROOT / "config" / "private"]
    directory_ok = all(path.exists() and os.access(path, os.W_OK) for path in runtime_paths)
    return {
        "control_version": CONTROL_VERSION,
        "python": {"ok": True, "message": sys.version.split()[0]},
        "packages": {"ok": all(packages.values()), "message": ", ".join(f"{n}:{'正常' if ok else '缺失'}" for n, ok in packages.items())},
        "market": _market_status(), "network": _network_check(),
        "eastmoney": _eastmoney_check(selected) if selected else empty,
        "cache": _cache_status(selected) if selected else empty,
        "latest": _latest_status(selected) if selected else empty,
        "keep_awake": keep_awake_status(ROOT), "ollama": _check_ollama(),
        "configuration": {"ok": not missing_config, "message": "配置文件正常" if not missing_config else "缺少：" + ", ".join(missing_config)},
        "directories": {"ok": directory_ok, "message": "运行目录可写" if directory_ok else "运行目录不存在或不可写"},
        "microsoft_token": {"ok": token_path.exists(), "message": "本地Token缓存已存在" if token_path.exists() else "尚未找到Token缓存，首次发信时需要授权"},
        "report": {"ok": report_exists, "message": "所选资产已有报告" if report_exists else "所选资产尚未生成报告"},
    }

@app.get("/")
def index():
    ensure_layout(ROOT)
    return render_template("control_index.html")


@app.get("/settings")
def settings_page():
    return render_template("system_settings.html")


@app.get("/asset/new")
def new_asset_page():
    return render_template("new_asset.html")


@app.get("/asset/manage")
def asset_manage_page():
    return render_template("asset_manage.html")


@app.get("/system-settings")
def system_settings_page():
    return render_template("system_settings.html")


@app.get("/trigger-conditions")
def trigger_conditions_page():
    return render_template("trigger_conditions.html")


@app.get("/system-tools")
def system_tools_page():
    return render_template("system_tools.html")


@app.get("/schedules")
def schedules_page():
    return render_template("schedules.html")


@app.get("/report")
def report_page():
    asset_id = str(request.args.get("asset_id", "")).strip()
    if not asset_id:
        return (render_template("empty_report.html", message="请先在控制台选择一只资产。"), 404)
    try:
        asset = _find_asset(asset_id)
    except ValueError as exc:
        return (render_template("empty_report.html", message=str(exc)), 404)
    report_path = asset_report_path(ROOT, _asset_identity(asset))
    if not report_path.exists():
        return (render_template("empty_report.html", asset=asset, message="当前资产尚未生成报告。请返回控制台运行一次报告。"), 404)
    return send_from_directory(report_path.parent, report_path.name)


@app.get("/history")
def history_page():
    asset_id = str(request.args.get("asset_id", "")).strip()
    if not asset_id:
        return (render_template("empty_report.html", message="历史报告属于单个资产，请先选择资产。"), 404)
    asset = _find_asset(asset_id)
    asset_key = _asset_identity(asset)
    path = generate_history_index(
        ROOT,
        asset_id=asset_key,
        asset_name=str(asset.get("name") or "当前资产"),
        asset_code=str(asset.get("asset_code") or ""),
        history_dir=asset_history_dir(ROOT, asset_key),
        index_path=asset_history_index_path(ROOT, asset_key),
    )
    return send_from_directory(path.parent, path.name)


@app.get("/history/<asset_id>/<path:filename>")
def history_file(asset_id: str, filename: str):
    return send_from_directory(asset_history_dir(ROOT, asset_id), filename)


@app.get("/static/<path:filename>")
def project_static(filename: str):
    return send_from_directory(
        ROOT / "static",
        filename,
    )


@app.get("/api/version")
def api_version():
    return jsonify({
        "name": "ETF Monitor Control Server",
        "version": CONTROL_VERSION,
        "pid": os.getpid(),
    })



@app.get("/api/power/keep-awake")
def api_keep_awake_status():
    return jsonify({
        "success": True,
        "status": keep_awake_status(ROOT),
    })


@app.post("/api/power/keep-awake/enable")
def api_keep_awake_enable():
    try:
        result = enable_keep_awake(ROOT)
        return jsonify({
            **result,
            "status": keep_awake_status(ROOT),
        })
    except (ValueError, RuntimeError) as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400


@app.post("/api/power/keep-awake/disable")
def api_keep_awake_disable():
    try:
        result = disable_keep_awake(ROOT)
        return jsonify({
            **result,
            "status": keep_awake_status(ROOT),
        })
    except RuntimeError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400
@app.get("/api/environment")
def api_environment():
    return jsonify(_environment_status(request.args.get("asset_id")))






@app.post("/api/data-sources/refresh-history")
def api_refresh_history_cache():
    logger = configure_logging(
        ROOT / "logs" / "global" / "application.log",
        ROOT / "logs" / "global" / "mail.log",
    )
    try:
        payload = request.get_json(silent=True) or {}
        asset = _find_asset(request.args.get("asset_id") or payload.get("asset_id"))
        result = refresh_history_cache_now(
            asset,
            _asset_runtime_data_config(asset),
            ROOT,
            logger,
            market_now(_schedule_config()),
        )
        status = 200 if result.get("success") else 409
        return jsonify(result), status
    except Exception as exc:
        logger.exception("Manual history cache refresh crashed")
        return jsonify({
            "success": False,
            "message": f"{type(exc).__name__}: {exc}",
        }), 500
@app.post("/api/data-sources/browser-inspect")
def api_browser_inspect():
    logger = configure_logging(
        ROOT / "logs" / "global" / "application.log",
        ROOT / "logs" / "global" / "mail.log",
    )
    payload = request.get_json(silent=True) or {}
    visible_seconds = payload.get("visible_seconds", 12)
    try:
        visible_seconds = max(5, min(60, int(visible_seconds)))
    except (TypeError, ValueError):
        visible_seconds = 12

    try:
        asset = _primary_asset()
        result = inspect_browser_requests(
            symbol=str(asset.get("symbol", "561380")),
            project_root=ROOT,
            logger=logger,
            visible_seconds=visible_seconds,
        )
        return jsonify({
            "success": True,
            "inspection": result,
        })
    except Exception as exc:
        logger.exception("Browser API inspector crashed")
        return jsonify({
            "success": False,
            "message": f"{type(exc).__name__}: {exc}",
        }), 500


@app.post("/api/system/browser-check")
def api_system_browser_check():
    """Verify browser automation, then refresh the selected asset cache on success."""
    logger = configure_logging(ROOT / "logs" / "global" / "application.log", ROOT / "logs" / "global" / "mail.log")
    try:
        asset = _primary_asset()
        inspection = inspect_browser_requests(
            symbol=str(asset.get("symbol", "")), project_root=ROOT, logger=logger, visible_seconds=8,
        )
        if not inspection.get("ok"):
            return jsonify({"success": False, "message": inspection.get("interpretation", "浏览器检查未通过。"), "inspection": inspection}), 409
        full_config = {
            "data": _global_config("data", {"data": {}}).get("data", {}),
            "storage": _global_config("storage", {"storage": {}}).get("storage", {}),
        }
        refresh = refresh_history_cache_now(asset, full_config, ROOT, logger, market_now(_schedule_config()))
        return jsonify({"success": bool(refresh.get("success")), "inspection": inspection, "cache_refresh": refresh, "message": refresh.get("message", "浏览器检查完成。")})
    except Exception as exc:
        logger.exception("System browser check crashed")
        return jsonify({"success": False, "message": f"浏览器检查失败：{type(exc).__name__}: {exc}"}), 500
@app.post("/api/data-sources/explore")
def api_data_sources_explore():
    logger = configure_logging(
        ROOT / "logs" / "global" / "application.log",
        ROOT / "logs" / "global" / "mail.log",
    )
    try:
        full_config = {
            "data": _read_json(
                ROOT / "config" / "global" / "data.json",
                {},
            ).get("data", {}),
        }
        asset = _primary_asset()
        result = explore_eastmoney_history_endpoints(
            asset,
            full_config,
            logger=logger,
            now=market_now(_schedule_config()),
        )
        output_path = ROOT / "data" / "diagnostics" / "latest_api_explorer.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(output_path, result)
        return jsonify({
            "success": True,
            "explorer": result,
        })
    except Exception as exc:
        logger.exception("EastMoney API explorer crashed")
        return jsonify({
            "success": False,
            "message": f"{type(exc).__name__}: {exc}",
        }), 500
@app.post("/api/data-sources/diagnose")
def api_data_sources_diagnose():
    logger = configure_logging(
        ROOT / "logs" / "global" / "application.log",
        ROOT / "logs" / "global" / "mail.log",
    )
    try:
        config = {
            **_read_json(ROOT / "config" / "global" / "app.json", {}),
            "data": _read_json(ROOT / "config" / "global" / "data.json", {}).get("data", {}),
        }
        # data_fetcher expects the merged config shape used by run.py.
        full_config = {
            "data": config.get("data", {}),
        }
        asset = _primary_asset()
        result = diagnose_data_sources(
            asset,
            full_config,
            logger=logger,
            now=market_now(_schedule_config()),
        )
        diagnostic_path = ROOT / "data" / "diagnostics" / "latest_data_source.json"
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(diagnostic_path, result)
        return jsonify({"success": True, "diagnostic": result})
    except Exception as exc:
        logger.exception("Data source diagnostic crashed")
        return jsonify({
            "success": False,
            "message": f"{type(exc).__name__}: {exc}",
        }), 500
@app.post("/api/ollama/start")
def api_ollama_start():
    return jsonify(_start_ollama())


@app.post("/api/report/run")
def api_report_run():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "auto"))
    if mode not in {"auto", "intraday", "close", "manual"}:
        return jsonify(
            {
                "success": False,
                "message": "无效的运行模式。",
            }
        ), 400

    slot = str(payload.get("slot", "")).strip()
    return jsonify(_run_monitor(mode, slot, str(payload.get("asset_id", "")).strip()))



@app.get("/api/schedules")
def api_schedules_get():
    import_result = _import_existing_windows_plans()
    asset_id = str(request.args.get("asset_id", "")).strip()
    plans = [_schedule_plan_view(plan) for plan in _load_schedule_plans() if not asset_id or str(plan.get("asset_id", "")) == asset_id]
    return jsonify({
        "success": True,
        "platform_supported": os.name == "nt",
        "plans": plans,
        "import_result": import_result,
    })


@app.post("/api/schedules/save")
def api_schedules_save():
    payload = request.get_json(silent=True) or {}
    payload.setdefault("asset_id", request.args.get("asset_id", ""))
    try:
        return jsonify(_upsert_schedule_plan(payload))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.post("/api/schedules/delete")
def api_schedules_delete():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_delete_schedule_plan(str(payload.get("id", ""))))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.post("/api/schedules/run")
def api_schedules_run():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_run_schedule_plan_now(str(payload.get("id", ""))))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@app.post("/api/schedules/import")
def api_schedules_import():
    try:
        result = _import_existing_windows_plans()
        return jsonify({
            "success": True,
            **result,
            "plans": [
                _schedule_plan_view(plan)
                for plan in _load_schedule_plans()
            ],
        })
    except RuntimeError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 500


@app.post("/api/schedules/sync")
def api_schedules_sync():
    try:
        return jsonify(_sync_all_schedule_plans())
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 500
@app.get("/api/assets")
def api_assets_get():
    return jsonify({"success": True, "assets": [_asset_summary(item) for item in _all_assets()]})


@app.get("/api/assets/summary")
def api_asset_summary_get():
    try:
        return jsonify(_homepage_asset_position_summary(_find_asset(request.args.get("asset_id"))))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404


@app.post("/api/assets/create")
def api_assets_create():
    payload = request.get_json(silent=True) or {}
    asset_code = str(payload.get("asset_code", "")).strip()
    tracking_code = str(payload.get("tracking_code", "")).strip()
    name = str(payload.get("name", "")).strip()
    tracking_name = str(payload.get("tracking_name", "")).strip()
    if not name or not asset_code or not tracking_name or not tracking_code:
        return jsonify({"success": False, "message": "资产名称、持有代码、跟踪名称和跟踪代码均不能为空。"}), 400
    if not asset_code.replace("-", "").replace("_", "").isalnum() or not tracking_code.replace("-", "").replace("_", "").isalnum():
        return jsonify({"success": False, "message": "持有代码和跟踪代码只能使用字母、数字、连字符或下划线。"}), 400
    if any(asset_code in {_asset_identity(item), str(item.get("asset_code", ""))} for item in _all_assets()):
        return jsonify({"success": False, "message": f"资产 {asset_code} 已存在。"}), 400
    template = _read_json(ROOT / "config" / "asset_template.json", {"asset": {}}).get("asset", {})
    asset = {
        **template,
        "id": asset_code,
        "name": name,
        "asset_code": asset_code,
        "tracking_code": tracking_code,
        "tracking_name": tracking_name,
        "symbol": tracking_code,
        "enabled": True,
    }
    saved = save_asset(ROOT, asset)
    return jsonify({"success": True, "message": "资产已新增。", "asset": saved})


@app.post("/api/assets/delete")
def api_assets_delete():
    payload = request.get_json(silent=True) or {}
    wanted = str(payload.get("asset_id", "")).strip()
    try:
        asset = _find_asset(wanted)
        asset_id = _asset_identity(asset)
        schedule_failures = []
        for plan in _load_schedule_plans():
            if str(plan.get("asset_id", "")) != asset_id:
                continue
            result = _delete_schedule_plan(str(plan.get("id", "")))
            if not result.get("success"):
                schedule_failures.append(result.get("message", "未能删除资产计划"))
        if schedule_failures:
            raise RuntimeError("；".join(schedule_failures))
        delete_asset(ROOT, asset_id, purge_runtime=True)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"success": False, "message": f"资产计划删除失败，资产未删除：{exc}"}), 500
    return jsonify({"success": True, "message": f"已删除 {asset.get('name') or wanted} 的配置、交易、报告、缓存、计划和资产日志；全局组合数据未受影响。"})


@app.get("/api/settings")
def api_settings_get():
    return jsonify(_load_settings(request.args.get("asset_id")))


@app.get("/api/system-settings")
def api_system_settings_get():
    return jsonify(_load_system_settings())


@app.post("/api/system-settings/save")
def api_system_settings_save():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_save_system_settings(payload))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@app.post("/api/settings/save")
def api_settings_save():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_save_settings(payload, request.args.get("asset_id") or payload.get("asset_id")))
    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400


@app.post("/api/sell-reminders/stop")
def api_sell_reminders_stop():
    payload = request.get_json(silent=True) or {}
    try:
        asset = _find_asset(payload.get("asset_id") or request.args.get("asset_id"))
        state = stop_sell_reminders(ROOT, _asset_identity(asset), str(payload.get("reason") or ""))
        return jsonify({"success": True, "message": "已停止当前市场周期的减仓提醒。进入下一轮上涨周期后会自动恢复。", "state": state})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@app.get("/api/transactions")
def api_transactions_get():
    try:
        return jsonify(_list_transactions(request.args.get("asset_id")))
    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400


@app.post("/api/transactions/save")
def api_transactions_save():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_save_transaction_record(payload, request.args.get("asset_id") or payload.get("asset_id")))
    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400


@app.get("/api/transactions/chart")
def api_transactions_chart():
    try:
        return jsonify(_transaction_chart_data(request.args.get("asset_id")))
    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400


@app.post("/api/transactions/delete")
def api_transactions_delete():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            _delete_transaction_record(
                str(payload.get("id", "")),
                request.args.get("asset_id") or payload.get("asset_id"),
            )
        )
    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400


@app.post("/api/transactions/delete-batch")
def api_transactions_delete_batch():
    payload = request.get_json(silent=True) or {}
    transaction_ids = payload.get("ids", [])
    if not isinstance(transaction_ids, list):
        return jsonify({
            "success": False,
            "message": "ids must be a list.",
        }), 400
    try:
        return jsonify(
            _delete_transaction_records(
                transaction_ids,
                request.args.get("asset_id") or payload.get("asset_id"),
            )
        )
    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400


@app.post("/api/transactions/add")
def api_transactions_add_legacy():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_save_transaction_record(payload, request.args.get("asset_id") or payload.get("asset_id")))
    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400


def _open_browser() -> None:
    time.sleep(1.2)
    webbrowser.open(f"http://{HOST}:{PORT}/")


def _port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, PORT)) == 0


if __name__ == "__main__":
    _disable_legacy_force_email_once()

    if _port_is_open():
        webbrowser.open(f"http://{HOST}:{PORT}/")
        raise SystemExit(0)

    threading.Thread(
        target=_open_browser,
        daemon=True,
    ).start()
    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        use_reloader=False,
    )
