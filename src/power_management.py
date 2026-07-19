from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

TASK_NAME = "ETFMonitor_KeepAwake"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _validate_time(value: str) -> str:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("时间必须使用HH:MM格式") from exc
    return value


def _validate_window(start: str, end: str) -> tuple[str, str]:
    start = _validate_time(start)
    end = _validate_time(end)
    start_minutes = int(start[:2]) * 60 + int(start[3:])
    end_minutes = int(end[:2]) * 60 + int(end[3:])
    if end_minutes <= start_minutes:
        raise ValueError("结束时间必须晚于开始时间")
    return start, end


def _run_schtasks(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    if os.name != "nt":
        raise RuntimeError("防休眠计划仅支持Windows")
    return subprocess.run(
        ["schtasks", *arguments],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _task_exists() -> bool:
    if os.name != "nt":
        return False
    return _run_schtasks(["/Query", "/TN", TASK_NAME]).returncode == 0


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(root: ET.Element, local_name: str) -> str | None:
    for node in root.iter():
        if _local_name(node.tag) == local_name and node.text:
            return node.text.strip()
    return None


def _settings_text(root: ET.Element, local_name: str) -> str | None:
    for node in root.iter():
        if _local_name(node.tag) != "Settings":
            continue
        for child in node:
            if _local_name(child.tag) == local_name and child.text:
                return child.text.strip()
    return None


def _parse_task_xml(xml_text: str) -> dict[str, Any]:
    """Parse the Keep Awake Windows task into user-facing settings.

    The local power.json file is only a convenience cache.  The Windows task
    is the source of truth after it has been created, so this parser lets the
    control page recover an existing plan after an upgrade, folder copy, or
    stale local configuration.
    """
    root = ET.fromstring(xml_text.lstrip("\ufeff"))

    start_boundary = _first_text(root, "StartBoundary") or ""
    start_match = re.search(r"T(\d{2}:\d{2})(?::\d{2})?", start_boundary)
    start = start_match.group(1) if start_match else "09:00"

    arguments = _first_text(root, "Arguments") or ""
    end_match = re.search(r"--until(?:=|\s+)[\"']?(\d{2}:\d{2})", arguments)
    end = end_match.group(1) if end_match else "15:30"

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
        day = day_map.get(_local_name(node.tag))
        if day and day not in weekdays:
            weekdays.append(day)
    if not weekdays:
        weekdays = ["MON", "TUE", "WED", "THU", "FRI"]

    enabled_text = _settings_text(root, "Enabled")
    if enabled_text is None:
        enabled_text = _first_text(root, "Enabled")
    wake_text = _settings_text(root, "WakeToRun")

    return {
        "enabled": str(enabled_text or "true").lower() != "false",
        "start": _validate_time(start),
        "end": _validate_time(end),
        "weekdays": weekdays,
        "task_name": TASK_NAME,
        "wake_to_run": str(wake_text or "false").lower() != "false",
    }


def _windows_task_settings() -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    completed = _run_schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        return _parse_task_xml(completed.stdout)
    except (ET.ParseError, ValueError):
        return None


def _sync_config_from_windows_task(
    root: Path,
    task_settings: dict[str, Any],
) -> dict[str, Any]:
    config = load_power_config(root)
    current = config.setdefault("keep_awake", {})
    relevant_keys = (
        "enabled",
        "start",
        "end",
        "weekdays",
        "task_name",
        "wake_to_run",
    )
    changed = any(current.get(key) != task_settings.get(key) for key in relevant_keys)
    if changed:
        current.update({key: task_settings[key] for key in relevant_keys})
        current["detected_from_windows"] = True
        current["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _atomic_write_json(_config_path(root), config)
    return config


def _current_user_sid() -> str:
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "无法读取当前Windows用户SID")
    rows = list(csv.reader(io.StringIO(result.stdout.strip())))
    if not rows or len(rows[0]) < 2:
        raise RuntimeError("无法解析当前Windows用户SID")
    return rows[0][1].strip()


def _pythonw_executable(root: Path) -> Path:
    candidate = root / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else Path(sys.executable)


def _task_xml(root: Path, start: str, end: str, sid: str) -> str:
    start_boundary = datetime.now().strftime("%Y-%m-%d") + f"T{start}:00"
    pythonw = _pythonw_executable(root)
    script = root / "keep_awake.py"
    command = html.escape(str(pythonw))
    working_dir = html.escape(str(root))
    arguments = html.escape(
        f'"{script}" --until {end} '
        '--state-file "data/keep_awake_status.json" '
        '--stop-file "data/keep_awake.stop" '
        '--log-file "logs/keep_awake.log"'
    )
    sid_xml = html.escape(sid)

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>ETF Monitor工作日交易时段防休眠</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start_boundary}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>
          <Monday /><Tuesday /><Wednesday /><Thursday /><Friday />
        </DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{sid_xml}</UserId>
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
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT8H</ExecutionTimeLimit>
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


def _config_path(root: Path) -> Path:
    return root / "config" / "power.json"


def _state_path(root: Path) -> Path:
    return root / "data" / "keep_awake_status.json"


def _stop_path(root: Path) -> Path:
    return root / "data" / "keep_awake.stop"


def _default_config() -> dict[str, Any]:
    return {"keep_awake": {
        "enabled": False,
        "start": "09:00",
        "end": "15:30",
        "weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
        "task_name": TASK_NAME,
        "wake_to_run": False,
    }}


def load_power_config(root: Path) -> dict[str, Any]:
    config = _read_json(_config_path(root), _default_config())
    config.setdefault("keep_awake", _default_config()["keep_awake"])
    return config


def _window_context() -> dict[str, Any]:
    """Return the fixed Beijing monitoring window in the system timezone."""
    local_now = datetime.now().astimezone()
    beijing_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    start = beijing_now.replace(hour=9, minute=0, second=0, microsecond=0)
    end = beijing_now.replace(hour=15, minute=30, second=0, microsecond=0)
    active = beijing_now.weekday() < 5 and start <= beijing_now < end
    next_start = start
    if beijing_now >= end or beijing_now.weekday() >= 5:
        days = 1
        while (beijing_now + timedelta(days=days)).weekday() >= 5:
            days += 1
        next_start = (beijing_now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
    return {
        "system_timezone": str(local_now.tzinfo),
        "system_time": local_now.strftime("%Y-%m-%d %H:%M"),
        "beijing_time": beijing_now.strftime("%Y-%m-%d %H:%M"),
        "local_window": f"{start.astimezone(local_now.tzinfo):%H:%M}–{end.astimezone(local_now.tzinfo):%H:%M}",
        "next_window": f"下一个窗口开始于 {next_start.astimezone(local_now.tzinfo):%Y-%m-%d %H:%M}",
        "window_active": active,
    }


def _currently_in_window(start: str, end: str) -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    current = now.hour * 60 + now.minute
    return (
        int(start[:2]) * 60 + int(start[3:])
        <= current
        < int(end[:2]) * 60 + int(end[3:])
    )


def enable_keep_awake(root: Path, start: str = "09:00", end: str = "15:30") -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("防休眠计划仅支持Windows")
    # Task Scheduler uses local wall time, so translate the fixed Beijing
    # window before creating the task (including windows that span midnight).
    start, end = _window_context()["local_window"].split("–")
    sid = _current_user_sid()
    task_dir = root / "data" / "scheduled_tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    xml_path = task_dir / "keep_awake_task.xml"
    xml_path.write_text(_task_xml(root, start, end, sid), encoding="utf-16")

    result = _run_schtasks(["/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"])
    if result.returncode != 0:
        combined = f"{result.stdout}\n{result.stderr}".strip()
        raise RuntimeError(combined or "创建Windows防休眠任务失败")

    config = _default_config()
    config["keep_awake"].update({
        "enabled": True,
        "start": start,
        "end": end,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })
    _atomic_write_json(_config_path(root), config)
    _stop_path(root).unlink(missing_ok=True)

    started_now = False
    if _window_context()["window_active"]:
        started_now = _run_schtasks(["/Run", "/TN", TASK_NAME]).returncode == 0

    return {
        "success": True,
        "message": (
            "已启用保持系统唤醒（北京时间工作日09:00–15:30）。"
            + ("当前处于设定时段，任务已立即启动。" if started_now else "任务将在下一个工作日开始时间自动启动。")
        ),
        "started_now": started_now,
    }


def _stop_running_helper(root: Path) -> None:
    state = _read_json(_state_path(root), {})
    pid = state.get("pid")
    _stop_path(root).parent.mkdir(parents=True, exist_ok=True)
    _stop_path(root).write_text("stop", encoding="utf-8")
    if os.name == "nt" and pid:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    _atomic_write_json(_state_path(root), {
        "active": False,
        "pid": pid,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "message": "防休眠已由控制台停用",
    })


def disable_keep_awake(root: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("防休眠计划仅支持Windows")
    _stop_running_helper(root)
    result = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0 and _task_exists():
        combined = f"{result.stdout}\n{result.stderr}".strip()
        raise RuntimeError(combined or "删除Windows防休眠任务失败")
    config = load_power_config(root)
    config["keep_awake"]["enabled"] = False
    config["keep_awake"]["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write_json(_config_path(root), config)
    return {"success": True, "message": "防休眠计划已停用，电脑恢复原有电源与休眠设置。"}


def keep_awake_status(root: Path) -> dict[str, Any]:
    config = load_power_config(root)
    settings = config.get("keep_awake", {})
    state = _read_json(_state_path(root), {})

    task_settings = _windows_task_settings() if os.name == "nt" else None
    task_exists = task_settings is not None
    detected_from_windows = False
    window = _window_context()
    if task_settings is not None:
        config = _sync_config_from_windows_task(root, task_settings)
        settings = config.get("keep_awake", {})
        detected_from_windows = True
    elif os.name == "nt":
        # Keep the existence check separate from XML parsing so the page can
        # explain a malformed/unreadable task instead of pretending it vanished.
        task_exists = _task_exists()

    active = False
    if state.get("active") and state.get("updated_at"):
        try:
            age = (
                datetime.now()
                - datetime.fromisoformat(str(state["updated_at"]))
            ).total_seconds()
            active = age < 120
        except ValueError:
            pass

    if task_settings is not None:
        enabled = bool(task_settings.get("enabled"))
        start = str(task_settings.get("start", "09:00"))
        end = str(task_settings.get("end", "15:30"))
        wake_to_run = bool(task_settings.get("wake_to_run", False))
        weekdays = list(task_settings.get("weekdays", []))
    else:
        enabled = bool(settings.get("enabled")) and task_exists
        start = str(settings.get("start", "09:00"))
        end = str(settings.get("end", "15:30"))
        wake_to_run = bool(settings.get("wake_to_run", False))
        weekdays = list(
            settings.get(
                "weekdays",
                ["MON", "TUE", "WED", "THU", "FRI"],
            )
        )

    if os.name != "nt":
        message = "当前不是Windows，无法创建系统防休眠计划"
    elif active and enabled:
        message = (
            f"防休眠正在运行，将保持系统唤醒至{end}；"
            "显示器仍可按原设置关闭"
        )
    elif enabled and detected_from_windows:
        message = (
            f"已从Windows任务计划程序识别：工作日{start}–{end}防休眠；"
            "当前不在运行时段"
        )
    elif enabled:
        message = f"已启用工作日{start}–{end}防休眠；当前不在运行时段"
    elif task_exists:
        message = "Windows防休眠任务存在，但任务当前已停用或无法读取完整设置"
    else:
        message = "尚未启用交易时段防休眠"

    return {
        "ok": enabled,
        "enabled": enabled,
        "active": active,
        "task_exists": task_exists,
        "detected_from_windows": detected_from_windows,
        "source": (
            "windows_task_scheduler"
            if detected_from_windows
            else "local_config"
        ),
        "platform_supported": os.name == "nt",
        "start": start,
        "end": end,
        "weekdays": weekdays,
        "wake_to_run": wake_to_run,
        **window,
        "message": message,
        "state": state,
    }
