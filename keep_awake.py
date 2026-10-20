from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from src.schedule_clock import beijing_now

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
_STOP = False


def _handle_stop(signum, frame) -> None:
    global _STOP
    _STOP = True


def _parse_time(value: str) -> dt_time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("时间必须使用HH:MM格式") from exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def _set_awake(enabled: bool) -> bool:
    if os.name != "nt":
        return False
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if enabled else ES_CONTINUOUS
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


def _beijing_window_end(now: datetime, start: dt_time, end: dt_time) -> datetime | None:
    """A delayed weekday trigger must not keep the system awake overnight."""
    if now.weekday() >= 5 or not start <= now.time() < end:
        return None
    return datetime.combine(now.date(), end, tzinfo=now.tzinfo)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--until", default="15:30", type=_parse_time)
    parser.add_argument("--start", default="09:00", type=_parse_time)
    parser.add_argument("--timezone", choices=["Asia/Shanghai", "system"], default="system")
    parser.add_argument("--state-file", default="data/keep_awake_status.json")
    parser.add_argument("--stop-file", default="data/keep_awake.stop")
    parser.add_argument("--log-file", default="logs/keep_awake.log")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    state_path = root / args.state_file
    stop_path = root / args.stop_file
    log_path = root / args.log_file
    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_stop)

    stop_path.unlink(missing_ok=True)
    clock = beijing_now if args.timezone == "Asia/Shanghai" else lambda: datetime.now().astimezone()
    now = clock()
    until_dt = datetime.combine(now.date(), args.until, tzinfo=now.tzinfo)

    # A Beijing window can cross midnight in the local timezone (for example
    # 21:00–03:30 in New York).  The scheduled helper therefore treats an
    # earlier end time as the following local day.
    if args.timezone == "Asia/Shanghai":
        until_dt = _beijing_window_end(now, args.start, args.until)
        if until_dt is None:
            _write_json(state_path, {"active": False, "updated_at": now.isoformat(timespec="seconds"), "message": "当前不在北京时间防休眠窗口内"})
            return 0
    elif until_dt <= now:
        until_dt += timedelta(days=1)

    if not _set_awake(True):
        _append_log(log_path, "SetThreadExecutionState调用失败")
        return 1

    started_at = now.isoformat(timespec="seconds")
    _append_log(log_path, f"防休眠已启动，PID={os.getpid()}，保持至{args.until:%H:%M}")

    try:
        while not _STOP:
            now = clock()
            if now >= until_dt or stop_path.exists():
                break
            _set_awake(True)
            _write_json(state_path, {
                "active": True,
                "pid": os.getpid(),
                "started_at": started_at,
                "updated_at": now.isoformat(timespec="seconds"),
                "until": until_dt.isoformat(timespec="seconds"),
                "message": "系统防休眠正在运行，显示器仍可按电源设置关闭",
            })
            time.sleep(max(5, args.interval))
    finally:
        _set_awake(False)
        finished = clock()
        _write_json(state_path, {
            "active": False,
            "pid": os.getpid(),
            "started_at": started_at,
            "updated_at": finished.isoformat(timespec="seconds"),
            "until": until_dt.isoformat(timespec="seconds"),
            "message": "防休眠时段已结束或已手动停用",
        })
        stop_path.unlink(missing_ok=True)
        _append_log(log_path, "防休眠已停止，系统恢复正常电源策略")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
