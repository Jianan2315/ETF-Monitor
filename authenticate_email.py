"""Run a one-time, visible Microsoft Graph device-login flow for email."""
from __future__ import annotations

from pathlib import Path

from src.config_loader import load_config
from src.logger import configure_logging, get_email_logger
from src.microsoft_graph_auth import acquire_graph_access_token, validate_graph_environment


ROOT = Path(__file__).resolve().parent


def main() -> int:
    configure_logging(ROOT / "logs" / "global" / "application.log", ROOT / "logs" / "global" / "mail.log")
    logger = get_email_logger()
    try:
        config = load_config(ROOT / "config")
        settings = validate_graph_environment(config["email"])
        token = acquire_graph_access_token(
            settings,
            ROOT,
            logger,
            allow_interactive=True,
        )
    except Exception as exc:
        print(f"Microsoft 邮件授权失败：{type(exc).__name__}: {exc}")
        return 1
    print(f"Microsoft 邮件授权已完成：{token.account_username or '已登录账户'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
