from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, url_for


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765

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


def _run_monitor(mode: str, slot: str) -> dict[str, Any]:
    if not _RUN_LOCK.acquire(blocking=False):
        return {
            "success": False,
            "message": "监控程序正在运行，请等待当前任务完成。",
        }

    try:
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

        report_path = ROOT / "output" / "report.html"
        success = completed.returncode == 0 and report_path.exists()

        return {
            "success": success,
            "return_code": completed.returncode,
            "message": (
                "报告已生成。"
                if success
                else "程序已结束，但没有生成新的报告。请查看日志。"
            ),
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "report_url": "/report" if report_path.exists() else None,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
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
        "python": {
            "ok": True,
            "message": sys.version.split()[0],
        },
        "packages": {
            "ok": all(packages.values()),
            "message": ", ".join(
                f"{name}:{'正常' if ok else '缺失'}"
                for name, ok in packages.items()
            ),
        },
        "network": _network_check(),
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
            "message": (
                "最新报告已存在"
                if report_path.exists()
                else "尚未生成报告"
            ),
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


@app.get("/")
def index():
    _migrate_legacy_report()
    return render_template("control_index.html")


@app.get("/settings")
def settings_page():
    return render_template("settings.html")


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


@app.get("/api/environment")
def api_environment():
    return jsonify(_environment_status())


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
