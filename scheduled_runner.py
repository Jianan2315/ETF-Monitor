from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from src.schedule_clock import beijing_now

ROOT = Path(__file__).resolve().parent


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _append_log(message: str) -> None:
    path = ROOT / "logs" / "global" / "scheduler.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[{beijing_now().isoformat(timespec='seconds')}] {message}\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--slot", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--asset", default="")
    args = parser.parse_args()

    status_path = (
        ROOT
        / "data"
        / "global"
        / "schedule_status"
        / f"{args.plan_id}.json"
    )
    started = beijing_now()
    _atomic_write_json(status_path, {
        "plan_id": args.plan_id,
        "plan_name": args.name,
        "status": "RUNNING",
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": None,
        "return_code": None,
        "run_status": None,
        "message": "定时任务正在运行",
        "report_generated": False,
        "email_status": "UNKNOWN",
    })
    _append_log(
        f"Plan {args.plan_id} started | Mode={args.mode} | Slot={args.slot}"
    )

    command = [
        sys.executable,
        str(ROOT / "run.py"),
        "--mode",
        args.mode,
    ]
    if args.slot:
        command.extend(["--slot", args.slot])
    if args.asset:
        command.extend(["--asset", args.asset])

    result_path = status_path.parent / f"run-{uuid.uuid4().hex}.json"
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env={**os.environ, "ETF_MONITOR_RUN_STATUS_FILE": str(result_path), "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=29 * 60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        completed = subprocess.CompletedProcess(command, 1, "", f"{type(exc).__name__}: {exc}")
    run_status = _read_json(result_path, {})
    result_path.unlink(missing_ok=True)
    finished = beijing_now()
    successful = completed.returncode == 0 and run_status.get("status") == "SUCCESS"
    error_detail = next((line for line in reversed(completed.stderr.splitlines()) if line.strip()), "未取得本次运行结果")
    status = {
        "plan_id": args.plan_id,
        "plan_name": args.name,
        "status": (
            "SUCCESS"
            if successful
            else "FAILED"
        ),
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "return_code": completed.returncode,
        "run_status": run_status.get("status"),
        "message": (
            (run_status.get("message") if completed.returncode == 0 else None)
            or (
                "定时运行成功"
                if successful
                else f"定时运行失败：{error_detail[:400]}"
            )
        ),
        "report_generated": bool(
            run_status.get("report_path")
            and successful
        ),
        "email_status": run_status.get("email_status", "UNKNOWN"),
        "steps": run_status.get("steps", []),
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }
    _atomic_write_json(status_path, status)
    _append_log(
        f"Plan {args.plan_id} finished | Status={status['status']} "
        f"| ReturnCode={completed.returncode} | Message={status['message']}"
    )
    return 0 if successful else (completed.returncode or 1)


if __name__ == "__main__":
    raise SystemExit(main())
