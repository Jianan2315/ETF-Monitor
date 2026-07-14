from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.microsoft_graph_auth import (
    MicrosoftGraphAuthError,
    acquire_graph_access_token,
    validate_graph_environment,
)


class MicrosoftGraphEmailError(RuntimeError):
    """Raised when Microsoft Graph rejects a sendMail request."""


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
    """Backward-compatible boolean helper used by tests and callers."""
    return evaluate_email_delivery(result, email_config).should_send


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
    run_tag = ""
    if result.get("run_slot"):
        phase = "盘中" if result.get("data_phase") == "INTRADAY" else "收盘"
        run_tag = f"[{result['run_slot']}{phase}]"
    return (
        f"{_status_icon(status)} [{prefix}]{test_tag}{run_tag}[{status}] "
        f"{result['asset']['name']}"
    )


def _plain_body(result: dict, include_raw: bool = True, forced: bool = False) -> str:
    indicators = result["indicators"]
    decision = result["decision"]
    position = result["position_metrics"]
    asset = result["asset"]

    parts: list[str] = []

    if forced:
        parts.append(
            "邮件测试模式已开启：本邮件绕过状态触发条件，用于验证 Microsoft Graph 通知。"
        )

    if result.get("llm_summary"):
        parts.append(f"模型摘要：\n{result['llm_summary']}")

    parts.append(
        f"基金：{asset['name']}（{asset['symbol']}）\n"
        f"状态：{decision['status']}（{result.get('status_label', decision['status'])}）\n"
        f"数据阶段：{result.get('data_phase', 'UNKNOWN')}\n"
        f"行情时间：{result.get('market_timestamp', '未知')}\n"
        f"运行时点：{result.get('run_slot') or result.get('run_mode', 'manual')}\n"
        f"策略：{asset['strategy'].get('name', '')} "
        f"v{asset['strategy'].get('version', '')}\n"
        f"建议人工复核金额：{decision['suggested_amount']:.0f} 元\n\n"
        "判断原因：\n"
        + "\n".join(
            f"{index + 1}. {reason}"
            for index, reason in enumerate(decision["reasons"])
        )
    )

    if include_raw:
        parts.append(
            "核心指标：\n"
            f"今日：{indicators['daily_change_pct']:.2f}%\n"
            f"近5日：{indicators['five_day_change_pct']:.2f}%\n"
            f"近10日：{indicators['ten_day_change_pct']:.2f}%\n"
            f"近20日：{indicators['twenty_day_change_pct']:.2f}%\n"
            f"相比上次买入：{indicators['drawdown_from_last_buy_pct']:.2f}%\n"
            f"当前总收益率：{position['profit_pct']:.2f}%\n"
            f"当前总收益金额：{position['profit_amount']:.2f} 元\n"
            f"已投入成本：{position['invested_amount']:.2f} 元\n"
            f"目标上限：{asset['position'].get('target_amount', 0):.2f} 元"
        )

    parts.append(
        f"数据源：{result['data_source']}\n"
        f"生成时间：{result['generated_at']}\n\n"
        "仅用于人工复核，不构成投资建议。"
    )
    return "\n\n".join(parts)


def _html_body(result: dict, include_raw: bool = True, forced: bool = False) -> str:
    indicators = result["indicators"]
    decision = result["decision"]
    position = result["position_metrics"]
    asset = result["asset"]

    reasons = "".join(
        f"<li>{html.escape(str(reason))}</li>"
        for reason in decision["reasons"]
    )

    summary = ""
    if result.get("llm_summary"):
        summary_text = html.escape(str(result["llm_summary"])).replace("\n", "<br>")
        summary = (
            '<div style="padding:14px;background:#eef6fb;border-radius:10px;'
            'margin-bottom:16px"><b>模型摘要</b>'
            f"<p>{summary_text}</p></div>"
        )

    test_banner = ""
    if forced:
        test_banner = (
            '<div style="padding:12px 14px;background:#fff4dd;'
            'border:1px solid #f0d69d;border-radius:10px;margin-bottom:16px;'
            'color:#6d4b0b"><b>测试邮件</b><br>'
            "本邮件绕过状态触发条件，仅用于验证 Microsoft Graph 通知。</div>"
        )

    raw = ""
    if include_raw:
        rows = [
            ("今日", f"{indicators['daily_change_pct']:.2f}%"),
            ("近5日", f"{indicators['five_day_change_pct']:.2f}%"),
            ("近10日", f"{indicators['ten_day_change_pct']:.2f}%"),
            ("近20日", f"{indicators['twenty_day_change_pct']:.2f}%"),
            ("相比上次买入", f"{indicators['drawdown_from_last_buy_pct']:.2f}%"),
            ("总收益率", f"{position['profit_pct']:.2f}%"),
            ("总收益金额", f"{position['profit_amount']:.2f} 元"),
            ("已投入成本", f"{position['invested_amount']:.2f} 元"),
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
    <h2 style="margin:0 0 4px">{html.escape(asset['name'])}</h2>
    <p style="color:#64748b">
      {html.escape(asset['symbol'])} ·
      {html.escape(asset['strategy'].get('name', ''))}
      v{html.escape(str(asset['strategy'].get('version', '')))}
    </p>
    <div style="display:inline-block;padding:8px 12px;background:#fff0cf;border-radius:999px;font-weight:700">
      {html.escape(decision['status'])}
    </div>
    <p><b>状态说明：</b>{html.escape(str(result.get('status_label', decision['status'])))}</p>
    <p><b>数据阶段：</b>{html.escape(str(result.get('data_phase', 'UNKNOWN')))}　
       <b>行情时间：</b>{html.escape(str(result.get('market_timestamp', '未知')))}　
       <b>运行时点：</b>{html.escape(str(result.get('run_slot') or result.get('run_mode', 'manual')))}</p>
    <p><b>建议人工复核金额：</b>{decision['suggested_amount']:.0f} 元</p>
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


def build_graph_message_payload(
    result: dict,
    email_config: dict,
    *,
    forced: bool = False,
) -> dict[str, Any]:
    """Build the JSON body expected by Microsoft Graph /me/sendMail."""
    recipients = _parse_recipients(email_config.get("recipient"))
    include_raw = bool(email_config.get("include_raw_metrics", True))
    use_html = bool(email_config.get("html", True))

    body_content = (
        _html_body(result, include_raw, forced=forced)
        if use_html
        else _plain_body(result, include_raw, forced=forced)
    )

    return {
        "message": {
            "subject": _subject(result, email_config, forced=forced),
            "body": {
                "contentType": "HTML" if use_html else "Text",
                "content": body_content,
            },
            "toRecipients": [
                {"emailAddress": {"address": recipient}}
                for recipient in recipients
            ],
        },
        "saveToSentItems": bool(
            (email_config.get("graph", {}) or {}).get("save_to_sent_items", True)
        ),
    }


def send_notification_email(
    result: dict,
    config: dict,
    *,
    project_root: Path,
    forced: bool = False,
    logger: logging.Logger | None = None,
) -> None:
    """Send an ETF notification through Microsoft Graph delegated OAuth."""
    email_config = config["email"]
    email_logger = logger or logging.getLogger("etf_email")
    provider = str(email_config.get("provider", "microsoft_graph")).lower()

    email_logger.info("Email sending started")
    email_logger.info(
        "Status=%s | Forced=%s | Provider=%s",
        result["decision"]["status"],
        forced,
        provider,
    )

    if provider not in {"microsoft_graph", "outlook_graph", "graph"}:
        raise MicrosoftGraphEmailError(
            f"Unsupported email provider: {provider}. Use microsoft_graph."
        )

    try:
        settings = validate_graph_environment(email_config)
        email_logger.info(
            "Microsoft Graph configuration validated | Recipient=%s | Authority=%s",
            settings["recipient"],
            settings["authority"],
        )
        email_logger.info(
            "Graph endpoint=%s | Timeout=%ss | Token cache=%s",
            settings["endpoint"],
            settings["timeout_seconds"],
            settings["cache_file"],
        )

        token = acquire_graph_access_token(settings, project_root, email_logger)
        email_logger.info(
            "Microsoft token ready | Account=%s | Silent=%s",
            token.account_username or "unknown",
            token.acquired_silently,
        )

        payload = build_graph_message_payload(
            result,
            email_config,
            forced=forced,
        )
        email_logger.info("Calling Microsoft Graph sendMail")
        response = requests.post(
            settings["endpoint"],
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings["timeout_seconds"],
        )

        request_id = response.headers.get("request-id") or response.headers.get(
            "client-request-id"
        )
        email_logger.info(
            "Microsoft Graph response | Status=%s | Request ID=%s",
            response.status_code,
            request_id or "not provided",
        )

        if response.status_code != 202:
            detail = _safe_graph_error(response)
            raise MicrosoftGraphEmailError(
                f"Microsoft Graph sendMail failed with HTTP {response.status_code}: "
                f"{detail}"
            )

        email_logger.info(
            "Email sent successfully through Microsoft Graph | Recipient=%s | Subject=%s",
            settings["recipient"],
            payload["message"]["subject"],
        )

    except MicrosoftGraphAuthError:
        email_logger.exception("Microsoft Graph authentication failed")
        raise
    except requests.Timeout:
        email_logger.exception(
            "Microsoft Graph request timed out. Check network access and timeout settings."
        )
        raise
    except requests.RequestException:
        email_logger.exception("Microsoft Graph HTTP request failed")
        raise
    except Exception:
        email_logger.exception("Unexpected Microsoft Graph email failure")
        raise


def _parse_recipients(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [item.strip() for item in value.replace(";", ",").split(",")]
    elif isinstance(value, list):
        candidates = [str(item).strip() for item in value]
    else:
        candidates = []

    recipients = [item for item in candidates if item]
    if not recipients:
        raise MicrosoftGraphEmailError(
            "No valid email recipient is configured in config/email.json"
        )
    return recipients


def _safe_graph_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:1000] or "empty response"

    if isinstance(payload, dict):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            code = error.get("code", "unknown")
            message = error.get("message", "")
            return f"{code}: {message}".strip()
    return str(payload)[:1000]


def send_data_error_email(
    *,
    asset: dict,
    errors: list[str],
    config: dict,
    project_root: Path,
    run_label: str,
    logger: logging.Logger | None = None,
) -> None:
    email_config = config["email"]
    email_logger = logger or logging.getLogger("etf_email")
    settings = validate_graph_environment(email_config)
    token = acquire_graph_access_token(settings, project_root, email_logger)
    recipients = _parse_recipients(email_config.get("recipient"))
    escaped_errors = "".join(f"<li>{html.escape(item)}</li>" for item in errors)
    subject = f"🔴 [{email_config.get('subject_prefix','ETF Monitor')}][DATA_ERROR][{run_label}] {asset.get('name', asset.get('symbol',''))}"
    content = f"""<html><body style="font-family:Arial,Microsoft YaHei,sans-serif">
    <h2>行情数据获取失败</h2>
    <p><b>标的：</b>{html.escape(str(asset.get('name','')))}（{html.escape(str(asset.get('symbol','')))})</p>
    <p><b>运行时点：</b>{html.escape(run_label)}</p>
    <p>程序已完成重试，但仍未取得有效数据。本次没有生成新的策略结论，也没有覆盖上一份成功网页。</p>
    <h3>失败记录</h3><ol>{escaped_errors}</ol>
    <p><b>请人工查看行情并决定是否需要操作。</b></p>
    </body></html>"""
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": content},
            "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
        },
        "saveToSentItems": bool((email_config.get("graph", {}) or {}).get("save_to_sent_items", True)),
    }
    response = requests.post(
        settings["endpoint"],
        headers={"Authorization": f"Bearer {token.access_token}", "Content-Type": "application/json"},
        json=payload,
        timeout=settings["timeout_seconds"],
    )
    if response.status_code != 202:
        raise MicrosoftGraphEmailError(
            f"Microsoft Graph DATA_ERROR send failed with HTTP {response.status_code}: {_safe_graph_error(response)}"
        )
    email_logger.info("DATA_ERROR email sent successfully | Asset=%s | Slot=%s", asset.get("symbol"), run_label)
