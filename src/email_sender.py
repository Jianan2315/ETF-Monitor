from __future__ import annotations

import html
import logging
import os
import smtplib
import socket
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any


@dataclass(frozen=True)
class EmailDecision:
    should_send: bool
    reason: str
    status: str
    forced: bool = False


def evaluate_email_delivery(result: dict, email_config: dict) -> EmailDecision:
    """Return both the decision and a human-readable reason for logging."""
    status = str(result.get("decision", {}).get("status", "UNKNOWN"))
    debug = email_config.get("debug", {}) or {}

    if not email_config.get("enabled", False):
        return EmailDecision(False, "email.enabled is false", status)

    if debug.get("enabled", False) and debug.get("force_send_email", False):
        return EmailDecision(
            True,
            "debug.force_send_email is enabled; trigger rules are bypassed",
            status,
            forced=True,
        )

    mode = str(email_config.get("send_mode", "trigger")).lower()
    if mode == "always":
        return EmailDecision(True, "email.send_mode is always", status)

    if mode != "trigger":
        return EmailDecision(
            False,
            f"unsupported email.send_mode: {mode}",
            status,
        )

    trigger_map = email_config.get("triggers", {}) or {}
    trigger_enabled = bool(trigger_map.get(status, False))
    if trigger_enabled:
        return EmailDecision(
            True,
            f"status {status} is enabled in email.triggers",
            status,
        )

    return EmailDecision(
        False,
        f"status {status} is disabled or missing in email.triggers",
        status,
    )


def should_send_email(result: dict, email_config: dict) -> bool:
    """Backward-compatible boolean helper used by existing tests/callers."""
    return evaluate_email_delivery(result, email_config).should_send


def validate_email_environment(email_config: dict) -> dict[str, Any]:
    sender = (os.getenv("OUTLOOK_EMAIL") or "").strip()
    password = os.getenv("OUTLOOK_PASSWORD") or ""
    recipient = str(email_config.get("recipient", "")).strip()

    smtp_cfg = email_config.get("smtp", {}) or {}
    host_env = smtp_cfg.get("host_env", "OUTLOOK_SMTP_HOST")
    port_env = smtp_cfg.get("port_env", "OUTLOOK_SMTP_PORT")
    default_host = smtp_cfg.get("default_host", "smtp.office365.com")
    default_port = int(smtp_cfg.get("default_port", 587))
    timeout_seconds = int(smtp_cfg.get("timeout_seconds", 30))

    host = (os.getenv(host_env) or default_host).strip()
    raw_port = (os.getenv(port_env) or str(default_port)).strip()

    missing: list[str] = []
    if not sender:
        missing.append("OUTLOOK_EMAIL")
    if not password:
        missing.append("OUTLOOK_PASSWORD")
    if not recipient or recipient == "your_email@outlook.com":
        missing.append("config/email.json -> email.recipient")

    if missing:
        raise RuntimeError("Missing email configuration: " + ", ".join(missing))

    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"Invalid SMTP port: {raw_port}") from exc

    return {
        "sender": sender,
        "password": password,
        "recipient": recipient,
        "host": host,
        "port": port,
        "timeout_seconds": timeout_seconds,
    }


def _status_icon(status: str) -> str:
    return {
        "WAIT": "🟢",
        "WATCH": "🟡",
        "HOLD": "🔵",
        "REVIEW_BUY": "🟠",
        "PARTIAL_SELL": "🔴",
    }.get(status, "⚪")


def _subject(result: dict, email_config: dict, forced: bool = False) -> str:
    prefix = email_config.get("subject_prefix", "ETF Monitor")
    status = result["decision"]["status"]
    test_tag = "[TEST]" if forced else ""
    return (
        f"{_status_icon(status)} [{prefix}]{test_tag}[{status}] "
        f"{result['asset']['name']}"
    )


def _plain_body(result: dict, include_raw: bool = True, forced: bool = False) -> str:
    i = result["indicators"]
    d = result["decision"]
    p = result["position_metrics"]
    a = result["asset"]

    parts: list[str] = []

    if forced:
        parts.append(
            "邮件测试模式已开启：本邮件绕过了状态触发条件，用于验证 SMTP 配置。"
        )

    if result.get("llm_summary"):
        parts.append(f"模型摘要：\n{result['llm_summary']}")

    parts.append(
        f"基金：{a['name']}（{a['symbol']}）\n"
        f"状态：{d['status']}\n"
        f"策略：{a['strategy'].get('name', '')} "
        f"v{a['strategy'].get('version', '')}\n"
        f"建议人工复核金额：{d['suggested_amount']:.0f} 元\n\n"
        "判断原因：\n"
        + "\n".join(f"{n + 1}. {reason}" for n, reason in enumerate(d["reasons"]))
    )

    if include_raw:
        parts.append(
            "核心指标：\n"
            f"今日：{i['daily_change_pct']:.2f}%\n"
            f"近5日：{i['five_day_change_pct']:.2f}%\n"
            f"近10日：{i['ten_day_change_pct']:.2f}%\n"
            f"近20日：{i['twenty_day_change_pct']:.2f}%\n"
            f"相比上次买入：{i['drawdown_from_last_buy_pct']:.2f}%\n"
            f"当前总收益率：{p['profit_pct']:.2f}%\n"
            f"当前总收益金额：{p['profit_amount']:.2f} 元\n"
            f"已投入成本：{p['invested_amount']:.2f} 元\n"
            f"目标上限：{a['position'].get('target_amount', 0):.2f} 元"
        )

    parts.append(
        f"数据源：{result['data_source']}\n"
        f"生成时间：{result['generated_at']}\n\n"
        "仅用于人工复核，不构成投资建议。"
    )
    return "\n\n".join(parts)


def _html_body(result: dict, include_raw: bool = True, forced: bool = False) -> str:
    i = result["indicators"]
    d = result["decision"]
    p = result["position_metrics"]
    a = result["asset"]

    reasons = "".join(
        f"<li>{html.escape(str(reason))}</li>" for reason in d["reasons"]
    )

    summary = ""
    if result.get("llm_summary"):
        summary = (
            '<div style="padding:14px;background:#eef6fb;border-radius:10px;'
            'margin-bottom:16px"><b>模型摘要</b>'
            f"<p>{html.escape(result['llm_summary'])}</p></div>"
        )

    test_banner = ""
    if forced:
        test_banner = (
            '<div style="padding:12px 14px;background:#fff4dd;'
            'border:1px solid #f0d69d;border-radius:10px;margin-bottom:16px;'
            'color:#6d4b0b"><b>测试邮件</b><br>'
            "本邮件绕过状态触发条件，仅用于验证 SMTP 配置。</div>"
        )

    raw = ""
    if include_raw:
        rows = [
            ("今日", f"{i['daily_change_pct']:.2f}%"),
            ("近5日", f"{i['five_day_change_pct']:.2f}%"),
            ("近10日", f"{i['ten_day_change_pct']:.2f}%"),
            ("近20日", f"{i['twenty_day_change_pct']:.2f}%"),
            ("相比上次买入", f"{i['drawdown_from_last_buy_pct']:.2f}%"),
            ("总收益率", f"{p['profit_pct']:.2f}%"),
            ("总收益金额", f"{p['profit_amount']:.2f} 元"),
            ("已投入成本", f"{p['invested_amount']:.2f} 元"),
        ]
        raw = (
            '<table style="width:100%;border-collapse:collapse">'
            + "".join(
                '<tr><td style="padding:8px;border-bottom:1px solid #eee;'
                f'color:#64748b">{html.escape(key)}</td>'
                '<td style="padding:8px;border-bottom:1px solid #eee;'
                f'text-align:right;font-weight:700">{html.escape(value)}</td></tr>'
                for key, value in rows
            )
            + "</table>"
        )

    return f"""<!doctype html>
<html>
<body style="font-family:Arial,Microsoft YaHei,sans-serif;color:#172033;background:#f4f6f8;padding:24px">
  <div style="max-width:680px;margin:auto;background:white;border-radius:16px;padding:24px">
    {test_banner}
    {summary}
    <h2 style="margin:0 0 4px">{html.escape(a['name'])}</h2>
    <p style="color:#64748b">
      {html.escape(a['symbol'])} ·
      {html.escape(a['strategy'].get('name', ''))}
      v{html.escape(str(a['strategy'].get('version', '')))}
    </p>
    <div style="display:inline-block;padding:8px 12px;background:#fff0cf;border-radius:999px;font-weight:700">
      {html.escape(d['status'])}
    </div>
    <p><b>建议人工复核金额：</b>{d['suggested_amount']:.0f} 元</p>
    <h3>判断原因</h3>
    <ol>{reasons}</ol>
    {raw}
    <p style="margin-top:20px;color:#64748b;font-size:12px">
      数据源：{html.escape(result['data_source'])}<br>
      生成时间：{html.escape(result['generated_at'])}<br>
      仅用于人工复核，不构成投资建议。
    </p>
  </div>
</body>
</html>"""


def send_notification_email(
    result: dict,
    config: dict,
    *,
    forced: bool = False,
    logger: logging.Logger | None = None,
) -> None:
    email_config = config["email"]
    email_logger = logger or logging.getLogger("etf_email")
    verbose = bool((email_config.get("debug", {}) or {}).get("verbose_logging", False))

    email_logger.info("Email sending started")
    email_logger.info(
        "Status=%s | Forced=%s | Provider=%s",
        result["decision"]["status"],
        forced,
        email_config.get("provider", "outlook"),
    )

    try:
        settings = validate_email_environment(email_config)
        email_logger.info(
            "Email configuration validated | Sender=%s | Recipient=%s",
            settings["sender"],
            settings["recipient"],
        )
        email_logger.info(
            "SMTP target=%s:%s | Timeout=%ss",
            settings["host"],
            settings["port"],
            settings["timeout_seconds"],
        )

        message = EmailMessage()
        message["From"] = settings["sender"]
        message["To"] = settings["recipient"]
        message["Subject"] = _subject(result, email_config, forced=forced)

        include_raw = bool(email_config.get("include_raw_metrics", True))
        message.set_content(_plain_body(result, include_raw, forced=forced))
        if email_config.get("html", True):
            message.add_alternative(
                _html_body(result, include_raw, forced=forced),
                subtype="html",
            )

        email_logger.info("Connecting to SMTP server")
        with smtplib.SMTP(
            settings["host"],
            settings["port"],
            timeout=settings["timeout_seconds"],
        ) as smtp:
            if verbose:
                smtp.set_debuglevel(1)

            email_logger.info("SMTP connected")
            email_logger.info("Starting TLS")
            smtp.starttls()
            email_logger.info("TLS established")

            email_logger.info("Logging in to SMTP account")
            smtp.login(settings["sender"], settings["password"])
            email_logger.info("SMTP login succeeded")

            email_logger.info("Sending email message")
            smtp.send_message(message)
            email_logger.info(
                "Email sent successfully | Recipient=%s | Subject=%s",
                settings["recipient"],
                message["Subject"],
            )

    except smtplib.SMTPAuthenticationError:
        email_logger.exception(
            "SMTP authentication failed. Check the account password/app password "
            "and whether SMTP AUTH is enabled."
        )
        raise
    except (socket.timeout, TimeoutError):
        email_logger.exception(
            "SMTP connection timed out. Check network access, host, port, and firewall."
        )
        raise
    except smtplib.SMTPException:
        email_logger.exception("SMTP operation failed")
        raise
    except Exception:
        email_logger.exception("Unexpected email sending failure")
        raise
