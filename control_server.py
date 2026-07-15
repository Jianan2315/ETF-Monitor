from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import re
import uuid
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, url_for

from src.market_clock import build_market_context, expected_history_date, market_now, validate_run_mode
from src.data_fetcher import diagnose_data_sources, explore_eastmoney_history_endpoints
from src.logger import configure_logging


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765
CONTROL_VERSION = "11.2"

app = Flask(
    __name__,
    template_folder=str(ROOT / "control_web" / "templates"),
    static_folder=str(ROOT / "control_web" / "static"),
    static_url_path="/control-static",
)

_RUN_LOCK = threading.Lock()


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


def _migrate_legacy_report() -> None:
    old_path = ROOT / "output" / "index.html"
    new_path = ROOT / "output" / "report.html"
    if old_path.exists() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_path, new_path)


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _schedule_config() -> dict[str, Any]:
    schedule_doc = _read_json(
        ROOT / "config" / "schedule.json",
        {"schedule": {"timezone": "Asia/Shanghai"}},
    )
    return {"schedule": schedule_doc.get("schedule", {})}


def _run_status_file() -> Path:
    return ROOT / "data" / "run_status" / "latest_run.json"


def _read_run_status() -> dict[str, Any]:
    return _read_json(_run_status_file(), {})


def _run_monitor(mode: str, slot: str) -> dict[str, Any]:
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
        report_path = ROOT / "output" / "report.html"
        success = (
            completed.returncode == 0
            and status.get("status") == "SUCCESS"
            and report_path.exists()
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
            "report_url": "/report" if success else None,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "code": "TIMEOUT",
            "message": "运行超时。请查看 logs/monitor.log。",
        }
    finally:
        _RUN_LOCK.release()


def _ollama_url() -> str:
    llm_doc = _read_json(ROOT / "config" / "llm.json", {})
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


def _primary_asset() -> dict[str, Any]:
    assets_doc = _read_json(ROOT / "config" / "assets.json", {"assets": []})
    assets = assets_doc.get("assets", [])
    return assets[0] if assets else {"symbol": "561380", "name": "ETF"}


def _eastmoney_check() -> dict[str, Any]:
    asset = _primary_asset()
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


def _cache_status() -> dict[str, Any]:
    asset = _primary_asset()
    symbol = str(asset.get("symbol", "561380"))
    path = ROOT / "data" / "cache" / f"{symbol}_daily.json"
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


def _latest_status() -> dict[str, Any]:
    path = ROOT / "data" / "latest.json"
    if not path.exists():
        return {"ok": False, "message": "尚未找到 latest.json"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        asset = _primary_asset()
        result = payload.get(str(asset.get("symbol", "")), {})
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


def _environment_status() -> dict[str, Any]:
    ollama = _check_ollama()
    token_path = ROOT / "data" / "private" / "msal_token_cache.json"
    report_path = ROOT / "output" / "report.html"

    packages = {}
    for package in ["pandas", "akshare", "msal", "flask", "requests"]:
        try:
            __import__(package)
            packages[package] = True
        except Exception:
            packages[package] = False

    return {
        "control_version": CONTROL_VERSION,
        "python": {"ok": True, "message": sys.version.split()[0]},
        "packages": {
            "ok": all(packages.values()),
            "message": ", ".join(
                f"{name}:{'正常' if ok else '缺失'}"
                for name, ok in packages.items()
            ),
        },
        "market": _market_status(),
        "network": _network_check(),
        "eastmoney": _eastmoney_check(),
        "cache": _cache_status(),
        "latest": _latest_status(),
        "ollama": ollama,
        "microsoft_token": {
            "ok": token_path.exists(),
            "message": (
                "本地Token缓存已存在"
                if token_path.exists()
                else "尚未找到Token缓存，首次发信时需要授权"
            ),
        },
        "report": {
            "ok": report_path.exists(),
            "message": "最新报告已存在" if report_path.exists() else "尚未生成报告",
        },
    }


def _load_settings() -> dict[str, Any]:
    assets_doc = _read_json(ROOT / "config" / "assets.json", {"assets": []})
    email_doc = _read_json(ROOT / "config" / "email.json", {"email": {}})
    llm_doc = _read_json(ROOT / "config" / "llm.json", {"llm": {}})
    data_doc = _read_json(ROOT / "config" / "data.json", {"data": {}})
    report_doc = _read_json(ROOT / "config" / "report.json", {"report": {}})
    schedule_doc = _read_json(ROOT / "config" / "schedule.json", {"schedule": {}})
    transactions_doc = _read_json(
        ROOT / "data" / "transactions.json",
        {},
    )

    asset = assets_doc.get("assets", [{}])[0] if assets_doc.get("assets") else {}

    return {
        "asset": asset,
        "email": email_doc.get("email", {}),
        "llm": llm_doc.get("llm", {}),
        "data": data_doc.get("data", {}),
        "report": report_doc.get("report", {}),
        "schedule": schedule_doc.get("schedule", {}),
        "transactions": transactions_doc.get(
            str(asset.get("symbol", "")),
            [],
        ),
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


def _save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    assets_path = ROOT / "config" / "assets.json"
    email_path = ROOT / "config" / "email.json"
    llm_path = ROOT / "config" / "llm.json"
    data_path = ROOT / "config" / "data.json"
    report_path = ROOT / "config" / "report.json"
    schedule_path = ROOT / "config" / "schedule.json"

    assets_doc = _read_json(assets_path, {"assets": []})
    email_doc = _read_json(email_path, {"email": {}})
    llm_doc = _read_json(llm_path, {"llm": {}})
    data_doc = _read_json(data_path, {"data": {}})
    report_doc = _read_json(report_path, {"report": {}})
    schedule_doc = _read_json(schedule_path, {"schedule": {}})

    if not assets_doc.get("assets"):
        raise ValueError("config/assets.json 中没有可编辑标的")

    asset = assets_doc["assets"][0]
    position = asset.setdefault("position", {})
    strategy = asset.setdefault("strategy", {})

    changes: list[str] = []

    def update_number(container, key, new_value, label, integer=False):
        old_value = container.get(key)
        value = (
            _coerce_int(new_value, label)
            if integer
            else _coerce_float(new_value, label)
        )
        if old_value != value:
            container[key] = value
            changes.append(f"{label}: {old_value} → {value}")

    asset_payload = payload.get("asset", {})
    strategy_payload = payload.get("strategy", {})

    if "target_amount" in asset_payload:
        update_number(
            position,
            "target_amount",
            asset_payload["target_amount"],
            "目标仓位",
        )

    strategy_fields = {
        "drop_from_last_buy_trigger_pct": ("回撤触发阈值", False),
        "stable_days_required": ("横盘确认天数", True),
        "stable_range_pct": ("横盘波动范围", False),
        "consecutive_down_warning_days": ("连续下跌警告天数", True),
        "normal_add_amount": ("普通加仓额度", False),
        "enhanced_add_amount": ("增强加仓额度", False),
        "reversal_confirmation_days": ("反转确认天数", True),
        "minimum_rebound_pct": ("最小反弹幅度", False),
        "first_take_profit_pct": ("第一止盈阈值", False),
        "first_sell_position_pct": ("第一卖出比例", False),
        "second_take_profit_pct": ("第二止盈阈值", False),
        "second_sell_position_pct": ("第二卖出比例", False),
        "minimum_core_position_pct": ("最小核心仓位比例", False),
    }
    for key, (label, integer) in strategy_fields.items():
        if key in strategy_payload:
            update_number(
                strategy,
                key,
                strategy_payload[key],
                label,
                integer=integer,
            )

    email_payload = payload.get("email", {})
    email_cfg = email_doc.setdefault("email", {})
    for key in [
        "enabled",
        "recipient",
        "include_llm_summary",
        "include_raw_metrics",
    ]:
        if key in email_payload and email_cfg.get(key) != email_payload[key]:
            changes.append(
                f"邮件 {key}: {email_cfg.get(key)} → {email_payload[key]}"
            )
            email_cfg[key] = email_payload[key]

    if "force_send_email" in email_payload:
        debug = email_cfg.setdefault("debug", {})
        old = debug.get("force_send_email")
        new = bool(email_payload["force_send_email"])
        if old != new:
            debug["force_send_email"] = new
            changes.append(f"强制发送邮件: {old} → {new}")

    if "data_error_dedup_minutes" in email_payload:
        update_number(
            email_cfg,
            "data_error_dedup_minutes",
            email_payload["data_error_dedup_minutes"],
            "DATA_ERROR邮件去重分钟",
            integer=True,
        )

    triggers_payload = email_payload.get("triggers", {})
    if triggers_payload:
        triggers = email_cfg.setdefault("triggers", {})
        for key, new_value in triggers_payload.items():
            if key in {
                "WAIT",
                "WATCH",
                "HOLD",
                "REVIEW_BUY",
                "PARTIAL_SELL",
            }:
                new_bool = bool(new_value)
                if triggers.get(key) != new_bool:
                    changes.append(
                        f"邮件触发 {key}: {triggers.get(key)} → {new_bool}"
                    )
                    triggers[key] = new_bool

    llm_payload = payload.get("llm", {})
    llm_cfg = llm_doc.setdefault("llm", {})
    for key in ["enabled", "model", "keep_alive"]:
        if key in llm_payload and llm_cfg.get(key) != llm_payload[key]:
            changes.append(
                f"本地模型 {key}: {llm_cfg.get(key)} → {llm_payload[key]}"
            )
            llm_cfg[key] = llm_payload[key]
    for key, label, integer in [
        ("timeout_seconds", "模型超时", True),
        ("max_summary_chars", "摘要最大长度", True),
        ("num_ctx", "上下文长度", True),
        ("num_predict", "最大生成Token", True),
    ]:
        if key in llm_payload:
            update_number(
                llm_cfg,
                key,
                llm_payload[key],
                label,
                integer=integer,
            )

    data_payload = payload.get("data", {})
    data_cfg = data_doc.setdefault("data", {})
    if "history_days" in data_payload:
        update_number(
            data_cfg,
            "history_days",
            data_payload["history_days"],
            "历史交易日数量",
            integer=True,
        )
    retry = data_cfg.setdefault("retry", {})
    if "retry_max_attempts" in data_payload:
        update_number(
            retry,
            "max_attempts",
            data_payload["retry_max_attempts"],
            "数据重试次数",
            integer=True,
        )

    report_payload = payload.get("report", {})
    report_cfg = report_doc.setdefault("report", {})
    for key, label in [
        ("retention_days", "报告保留天数"),
        ("max_history_files", "历史报告最大数量"),
    ]:
        if key in report_payload:
            update_number(
                report_cfg,
                key,
                report_payload[key],
                label,
                integer=True,
            )

    schedule_payload = payload.get("schedule", {})
    schedule_cfg = schedule_doc.setdefault("schedule", {})
    for key in ["intraday_slots", "close_slot"]:
        if key in schedule_payload and schedule_cfg.get(key) != schedule_payload[key]:
            changes.append(
                f"调度 {key}: {schedule_cfg.get(key)} → {schedule_payload[key]}"
            )
            schedule_cfg[key] = schedule_payload[key]

    if not changes:
        return {
            "success": True,
            "message": "没有检测到需要保存的修改。",
            "changes": [],
        }

    for path in [
        assets_path,
        email_path,
        llm_path,
        data_path,
        report_path,
        schedule_path,
    ]:
        _backup_file(path)

    _atomic_write_json(assets_path, assets_doc)
    _atomic_write_json(email_path, email_doc)
    _atomic_write_json(llm_path, llm_doc)
    _atomic_write_json(data_path, data_doc)
    _atomic_write_json(report_path, report_doc)
    _atomic_write_json(schedule_path, schedule_doc)

    return {
        "success": True,
        "message": "配置已保存，并已备份原文件。",
        "changes": changes,
    }


def _add_transaction(payload: dict[str, Any]) -> dict[str, Any]:
    assets_doc = _read_json(
        ROOT / "config" / "assets.json",
        {"assets": []},
    )
    if not assets_doc.get("assets"):
        raise ValueError("没有配置标的")

    asset = assets_doc["assets"][0]
    symbol = str(asset["symbol"])
    transaction_path = ROOT / "data" / "transactions.json"
    transactions_doc = _read_json(transaction_path, {})
    records = transactions_doc.setdefault(symbol, [])

    tx_type = str(payload.get("type", "")).upper()
    if tx_type not in {"BUY", "SELL"}:
        raise ValueError("交易类型必须是 BUY 或 SELL")

    date = str(payload.get("date", "")).strip()
    if not date:
        raise ValueError("交易日期不能为空")

    amount = _coerce_float(payload.get("amount"), "交易金额")
    price = _coerce_float(payload.get("price"), "成交价格")
    if amount <= 0 or price <= 0:
        raise ValueError("交易金额和价格必须大于0")

    record = {
        "date": date,
        "type": tx_type,
        "amount": amount,
        "price": price,
        "note": str(payload.get("note", "")).strip(),
    }

    shares = payload.get("shares")
    if shares not in (None, ""):
        record["shares"] = _coerce_float(shares, "卖出份额")

    _backup_file(transaction_path)
    records.append(record)
    records.sort(key=lambda item: str(item.get("date", "")))
    _atomic_write_json(transaction_path, transactions_doc)

    return {
        "success": True,
        "message": "交易记录已保存。",
        "record": record,
    }


SCHEDULE_PLANS_FILE = ROOT / "config" / "schedule_plans.json"
SCHEDULE_WRAPPER_DIR = ROOT / "data" / "scheduled_tasks"
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


def _load_schedule_plans() -> list[dict[str, Any]]:
    payload = _read_json(SCHEDULE_PLANS_FILE, {"plans": []})
    plans = payload.get("plans", [])
    return plans if isinstance(plans, list) else []


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
    SCHEDULE_WRAPPER_DIR.mkdir(parents=True, exist_ok=True)
    wrapper_path = _plan_wrapper_path(plan["id"])
    mode = plan["mode"]
    run_time = plan["time"]
    content = (
        '@echo off\n'
        f'cd /d "{ROOT}"\n'
        'if not exist "logs" mkdir "logs"\n'
        f'set "PYTHON_EXE={ROOT / ".venv" / "Scripts" / "python.exe"}"\n'
        'if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"\n'
        f'echo [%date% %time%] Scheduled plan {plan["id"]} started. >> "logs\\scheduled_tasks.log"\n'
        f'"%PYTHON_EXE%" run.py --mode {mode} --slot {run_time} >> "logs\\scheduled_tasks.log" 2>&1\n'
        f'echo [%date% %time%] Scheduled plan {plan["id"]} finished with code %errorlevel%. >> "logs\\scheduled_tasks.log"\n'
    )
    wrapper_path.write_text(content, encoding="utf-8-sig")
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


def _sync_plan_to_windows(plan: dict[str, Any]) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "ok": False,
            "message": "计划已保存，但当前不是Windows，无法创建系统定时任务。",
        }

    wrapper_path = _write_plan_wrapper(plan)
    task_name = plan["task_name"]
    day_argument = ",".join(plan["weekdays"])
    task_command = f'cmd.exe /d /c ""{wrapper_path}""'

    completed = _run_schtasks([
        "/Create",
        "/TN",
        task_name,
        "/TR",
        task_command,
        "/SC",
        "WEEKLY",
        "/D",
        day_argument,
        "/ST",
        plan["time"],
        "/F",
        "/RL",
        "LIMITED",
    ])

    if completed.returncode != 0:
        combined = f"{completed.stdout}\n{completed.stderr}".strip()
        return {
            "ok": False,
            "message": combined or "创建Windows定时任务失败",
        }

    if not plan.get("enabled", True):
        disabled = _run_schtasks(["/Change", "/TN", task_name, "/Disable"])
        if disabled.returncode != 0:
            combined = f"{disabled.stdout}\n{disabled.stderr}".strip()
            return {
                "ok": False,
                "message": combined or "任务已创建，但禁用任务失败",
            }

    return {
        "ok": True,
        "message": "Windows定时任务已同步",
    }


def _schedule_plan_view(plan: dict[str, Any]) -> dict[str, Any]:
    task_exists = _task_exists(plan["task_name"]) if os.name == "nt" else False
    return {
        **plan,
        "weekday_labels": [DAY_LABELS[day] for day in plan["weekdays"]],
        "system_task_exists": task_exists,
        "platform_supported": os.name == "nt",
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
    _validate_plan_time_for_mode(mode, run_time)

    now_text = datetime.now().isoformat(timespec="seconds")
    plan = {
        "id": plan_id,
        "name": name,
        "mode": mode,
        "time": run_time,
        "weekdays": weekdays,
        "enabled": enabled,
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
    }

@app.get("/")
def index():
    _migrate_legacy_report()
    return render_template("control_index.html")


@app.get("/settings")
def settings_page():
    return render_template("settings.html")


@app.get("/schedules")
def schedules_page():
    return render_template("schedules.html")


@app.get("/report")
def report_page():
    report_path = ROOT / "output" / "report.html"
    if not report_path.exists():
        return redirect(url_for("index"))
    return send_from_directory(
        ROOT / "output",
        "report.html",
    )


@app.get("/history")
def history_page():
    history_path = ROOT / "output" / "history.html"
    if not history_path.exists():
        return redirect(url_for("index"))
    return send_from_directory(
        ROOT / "output",
        "history.html",
    )


@app.get("/history/<path:filename>")
def history_file(filename: str):
    return send_from_directory(
        ROOT / "output" / "history",
        filename,
    )


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


@app.get("/api/environment")
def api_environment():
    return jsonify(_environment_status())




@app.post("/api/data-sources/explore")
def api_data_sources_explore():
    logger = configure_logging(
        ROOT / "logs" / "monitor.log",
        ROOT / "logs" / "email.log",
    )
    try:
        full_config = {
            "data": _read_json(
                ROOT / "config" / "data.json",
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
        ROOT / "logs" / "monitor.log",
        ROOT / "logs" / "email.log",
    )
    try:
        config = {
            **_read_json(ROOT / "config" / "app.json", {}),
            "data": _read_json(ROOT / "config" / "data.json", {}).get("data", {}),
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
    return jsonify(_run_monitor(mode, slot))



@app.get("/api/schedules")
def api_schedules_get():
    plans = [_schedule_plan_view(plan) for plan in _load_schedule_plans()]
    return jsonify({
        "success": True,
        "platform_supported": os.name == "nt",
        "plans": plans,
    })


@app.post("/api/schedules/save")
def api_schedules_save():
    payload = request.get_json(silent=True) or {}
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


@app.post("/api/schedules/sync")
def api_schedules_sync():
    try:
        return jsonify(_sync_all_schedule_plans())
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 500
@app.get("/api/settings")
def api_settings_get():
    return jsonify(_load_settings())


@app.post("/api/settings/save")
def api_settings_save():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_save_settings(payload))
    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400


@app.post("/api/transactions/add")
def api_transactions_add():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_add_transaction(payload))
    except ValueError as exc:
        return jsonify(
            {
                "success": False,
                "message": str(exc),
            }
        ), 400


def _open_browser() -> None:
    time.sleep(1.2)
    webbrowser.open(f"http://{HOST}:{PORT}/")


def _port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, PORT)) == 0


if __name__ == "__main__":
    _migrate_legacy_report()

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
