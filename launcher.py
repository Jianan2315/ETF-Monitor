from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PORT = 8765
HOST = "127.0.0.1"
LOG_DIR = ROOT / "logs"
BOOT_LOG = LOG_DIR / "launcher.log"
SERVER_LOG = LOG_DIR / "control_server_console.log"
REQUIREMENTS_FILE = ROOT / "requirements.txt"


def print_header() -> None:
    print("=" * 62)
    print("                 ETF Monitor Launcher V13.2")
    print("=" * 62)
    print(f"Project folder: {ROOT}")
    print(f"Python: {sys.executable}")
    print()


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except OSError as exc:
        print(f"[WARNING] Could not write log file {path}: {exc}")


def port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, PORT)) == 0


def detect_existing_version() -> str | None:
    try:
        with urlopen(f"http://{HOST}:{PORT}/api/version", timeout=1.5) as response:
            text = response.read().decode("utf-8", errors="replace")
            return text
    except (URLError, OSError):
        return None


def find_port_pid() -> int | None:
    if os.name != "nt":
        return None

    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=15,
    )
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if f":{PORT}" not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return None


def stop_existing_server() -> bool:
    pid = find_port_pid()
    if pid is None:
        print("[ERROR] Port 8765 is in use, but the owning PID could not be found.")
        return False

    print(f"Existing listener PID: {pid}")
    answer = input("Stop it and start this version? [Y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        print("Existing service was left running.")
        return False

    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    if completed.returncode != 0:
        print("[ERROR] Could not stop the existing process.")
        print(completed.stdout)
        print(completed.stderr)
        return False

    print("Existing service stopped.")
    append_log(BOOT_LOG, f"Stopped existing listener PID {pid}")
    for _ in range(20):
        if not port_is_open():
            return True
        time.sleep(0.25)

    print("[ERROR] Port 8765 is still in use after stopping the process.")
    return False


def package_missing(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is None


def ensure_dependencies() -> bool:
    required_modules = ["flask", "pandas", "requests", "akshare", "msal", "playwright"]
    missing = [name for name in required_modules if package_missing(name)]
    if not missing:
        print("[2/4] Dependencies are ready.")
        return True

    print(f"[2/4] Missing packages: {', '.join(missing)}")
    print("Installing requirements...")
    append_log(BOOT_LOG, f"Installing dependencies: {', '.join(missing)}")

    completed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
        cwd=ROOT,
        text=True,
    )
    if completed.returncode != 0:
        print("[ERROR] Dependency installation failed.")
        append_log(BOOT_LOG, "Dependency installation failed")
        return False

    still_missing = [name for name in required_modules if package_missing(name)]
    if still_missing:
        print(f"[ERROR] Packages are still missing: {', '.join(still_missing)}")
        append_log(BOOT_LOG, f"Packages still missing: {', '.join(still_missing)}")
        return False

    print("Dependencies installed successfully.")
    return True


def validate_server_file() -> bool:
    print("[3/4] Checking control_server.py...")
    completed = subprocess.run(
        [sys.executable, "-m", "py_compile", str(ROOT / "control_server.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if completed.returncode != 0:
        print("[ERROR] control_server.py failed the compile check.")
        print(completed.stdout)
        print(completed.stderr)
        append_log(BOOT_LOG, "control_server.py compile check failed")
        return False
    print("Control server check passed.")
    return True



def open_server_log():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return SERVER_LOG.open("a", encoding="utf-8"), SERVER_LOG
    except OSError as exc:
        fallback = LOG_DIR / (
            "control_server_console_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".log"
        )
        print(
            f"[WARNING] Could not open {SERVER_LOG}: {exc}\n"
            f"Using fallback log: {fallback}"
        )
        return fallback.open("a", encoding="utf-8"), fallback


def run_server() -> int:
    print("[4/4] Starting ETF Monitor control server...")
    print(f"Browser address: http://{HOST}:{PORT}/")
    print("Keep this window open while using ETF Monitor.")
    print("Press Ctrl+C to stop the server.")
    print(f"Preferred console log: {SERVER_LOG}")
    print()

    append_log(BOOT_LOG, "Starting control_server.py")
    log_handle, active_log_path = open_server_log()
    print(f"Active console log: {active_log_path}")
    print()

    process = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "control_server.py")],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    with log_handle:
        log_handle.write("\n" + "=" * 62 + "\n")
        log_handle.write(
            f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
            "Starting ETF Monitor Control Server V13.2\n"
        )
        log_handle.write("=" * 62 + "\n")
        log_handle.flush()

        try:
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="")
                log_handle.write(line)
                log_handle.flush()
        except KeyboardInterrupt:
            print("\nStopping ETF Monitor...")
            append_log(BOOT_LOG, "Keyboard interrupt received")
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()

    return process.wait()


def main() -> int:
    print_header()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    append_log(BOOT_LOG, "Launcher V13.2 started")

    print("[1/4] Checking port 8765...")
    if port_is_open():
        version_text = detect_existing_version()
        print("[NOTICE] Port 8765 is already in use.")
        if version_text:
            print(f"Existing service response: {version_text}")
        if not stop_existing_server():
            print()
            print("The current launcher will not start a second server.")
            return 1
    else:
        print("Port 8765 is available.")

    if not ensure_dependencies():
        return 1

    if not validate_server_file():
        return 1

    exit_code = run_server()

    print()
    print("=" * 62)
    print(f"ETF Monitor stopped. Exit code: {exit_code}")
    print("=" * 62)
    append_log(BOOT_LOG, f"Control server stopped with exit code {exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
