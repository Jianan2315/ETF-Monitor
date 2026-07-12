from __future__ import annotations

import logging
from pathlib import Path


def _build_file_handler(path: Path, level: int = logging.INFO) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    return handler


def configure_logging(monitor_path: Path, email_path: Path | None = None) -> logging.Logger:
    """
    Configure two independent loggers:

    - etf_monitor -> logs/monitor.log and console
    - etf_email   -> logs/email.log

    Email logs do not propagate into monitor.log, so SMTP troubleshooting stays
    separate from the market-data and strategy pipeline.
    """
    monitor_path.parent.mkdir(parents=True, exist_ok=True)

    monitor_logger = logging.getLogger("etf_monitor")
    monitor_logger.setLevel(logging.INFO)
    monitor_logger.handlers.clear()
    monitor_logger.propagate = False

    monitor_logger.addHandler(_build_file_handler(monitor_path))

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    monitor_logger.addHandler(console)

    if email_path is not None:
        email_logger = logging.getLogger("etf_email")
        email_logger.setLevel(logging.INFO)
        email_logger.handlers.clear()
        email_logger.propagate = False
        email_logger.addHandler(_build_file_handler(email_path))

    return monitor_logger


def get_email_logger() -> logging.Logger:
    return logging.getLogger("etf_email")
