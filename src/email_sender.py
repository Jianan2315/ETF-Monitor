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
from src.status import normalize_status


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
    status = normalize_status(result.get("decision", {}).get("status", "OBSERVE"))
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

    trigger_map = email_config.get("triggers", {})
    if not isinstance(trigger_map, dict):
        trigger_map = {}
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

def _status_icon(status: str) -> str:
    return {
        "OBSERVE": "🔵",
        "REVIEW_BUY": "🟠",
        "REVIEW_SELL": "🟣",
        "PARTIAL_SELL": "🔴",
    }.get(status, "⚪")


def _suggested_amount_text(decision: dict[str, Any]) -> str:
    amount = float(decision.get("suggested_amount") or 0)
    return (
        f"建议人工复核金额：{amount:.0f} 元"
        if amount > 0
        else "加仓或减仓金额：由人工决定"
    )


def _subject(result: dict, email_config: dict, forced: bool = False) -> str:
    prefix = email_config.get("subject_prefix", "ETF Monitor")
    status = normalize_status(result["decision"]["status"])
    test_tag = "[TEST]" if forced else ""
    run_tag = ""
    if result.get("run_slot"):
        phase_map = {
            "PRE_OPEN": "开盘前",
            "AM_SESSION": "上午盘中",
            "MIDDAY_BREAK": "午间休市",
            "PM_SESSION": "下午盘中",
            "AFTER_CLOSE": "收盘后",
            "WEEKEND": "周末",
        }
        phase = phase_map.get(result.get("data_phase"), "报告")
        run_tag = f"[{result['run_slot']}{phase}]"
    return (
        f"{_status_icon(status)} [{prefix}]{test_tag}{run_tag}[{status}] "
        f"{result['asset']['name']}"
    )


def _plain_body(result: dict, include_raw: bool = True, forced: bool = False) -> str:
    indicators = result["indicators"]
    decision = result["decision"]
    display_status = normalize_status(decision.get("status"))
    position = result["position_metrics"]
    asset = result["asset"]
    cycle = indicators.get("cycle_analysis", {}) or {}
    sell_reminder = result.get("sell_reminder", {}) or {}
    reminder_state = sell_reminder.get("state", {}) or {}

    parts: list[str] = []

    metadata = (
        f"数据源：{result.get('data_source', '未知')}\n"
        f"数据阶段：{result.get('data_phase_label', result.get('data_phase', 'UNKNOWN'))}（{result.get('data_phase', 'UNKNOWN')}）\n"
        f"行情时间：{result.get('market_timestamp', '未知')}\n"
        f"实际运行时间：{result.get('run_time', result.get('generated_at', '未知'))}\n"
        f"计划时点：{result.get('run_slot') or '手动运行'}"
    )
    parts.append(metadata)

    data_status = result.get("data_status", {}) or {}
    status_lines = []
    for key, label in [
        ("history", "历史日线"),
        ("realtime", "实时快照"),
        ("intraday", "一分钟分时"),
    ]:
        item = data_status.get(key, {}) or {}
        status_lines.append(
            f"{label}：{item.get('status', 'UNKNOWN')}｜"
            f"{item.get('message', '无说明')}"
        )
    if status_lines:
        parts.append("数据状态：\n" + "\n".join(status_lines))

    if forced:
        parts.append(
            "邮件测试模式已开启：本邮件绕过状态触发条件，用于验证 Microsoft Graph 通知。"
        )

    if result.get("llm_summary"):
        parts.append(f"模型摘要：\n{result['llm_summary']}")

    news = result.get("news") or {}
    if news.get("status") == "SUCCESS" and str(news.get("summary") or "").strip():
        parts.append(
            "近期资讯摘要（仅供人工复核，不参与策略判断）：\n"
            f"{news['summary']}\n"
            f"检索时间：{news.get('generated_at', '未知')}"
        )

    parts.append(
        f"基金：{asset['name']}（{asset['symbol']}）\n"
        f"状态：{display_status}（{result.get('status_label', display_status)}）\n"
        f"数据阶段：{result.get('data_phase_label', result.get('data_phase', 'UNKNOWN'))}（{result.get('data_phase', 'UNKNOWN')}）\n"
        f"行情时间：{result.get('market_timestamp', '未知')}\n"
        f"运行时点：{result.get('run_slot') or result.get('run_mode', 'manual')}\n"
        f"{_suggested_amount_text(decision)}\n\n"
        f"内部原因码：{decision.get('reason_code', 'NO_ACTION')}\n"
        f"当前周期：{cycle.get('current_cycle_id') or '尚未确认'}\n"
        f"确认高点：{(cycle.get('last_confirmed_high') or {}).get('date', '无')}（确认 {(cycle.get('last_confirmed_high') or {}).get('confirmed_date', '无')}）\n"
        f"确认低点：{(cycle.get('last_confirmed_low') or {}).get('date', '无')}（确认 {(cycle.get('last_confirmed_low') or {}).get('confirmed_date', '无')}）\n"
        f"历史样本：回撤 {cycle.get('drawdown_sample_size', 0)} / 上涨 {cycle.get('rise_sample_size', 0)}\n"
        f"当前回撤：{cycle.get('current_drawdown_pct', '不适用')}%\n"
        f"当前上涨：{cycle.get('current_rise_pct', '不适用')}%\n"
        f"回撤历史分位：{cycle.get('current_drawdown_percentile', '不适用')}%\n"
        f"上涨历史分位：{cycle.get('current_rise_percentile', '不适用')}%\n"
        f"反弹修复：{cycle.get('recovery_pct') if cycle.get('recovery_pct') is not None else '不适用'}%\n"
        f"资金流：{indicators.get('fund_flow_status', 'UNAVAILABLE')}｜{indicators.get('fund_flow_impact', '中性')}\n\n"
        "判断原因：\n"
        + "\n".join(
            f"{index + 1}. {reason}"
            for index, reason in enumerate(decision["reasons"])
        )
    )

    if decision.get("status") == "PARTIAL_SELL":
        parts.append(
            "减仓提醒进度：\n"
            f"是否可发送：{sell_reminder.get('allowed', True)}；原因：{sell_reminder.get('reason', '无')}\n"
            f"参考份额：{reminder_state.get('reference_shares', '不适用')}\n"
            f"当前份额：{reminder_state.get('current_shares', position.get('shares', 0))}\n"
            f"已减仓：{reminder_state.get('reduced_pct', 0)}%\n"
            f"剩余仓位：{reminder_state.get('remaining_pct', 0)}%\n"
            f"第一档已提醒：{len((reminder_state.get('first') or {}).get('dates', []))} 次\n"
            f"第二档已提醒：{len((reminder_state.get('second') or {}).get('dates', []))} 次"
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
            f"持有份额：{position.get('shares', 0):.4f}"
        )

    research = result.get("research") or {}
    if research:
        manual_items = research.get("manual_review_items", [])[:3]
        research_lines = [
            f"研究数据状态：{research.get('research_data_state', 'UNKNOWN')}",
            f"数据源验证：{(research.get('source_reliability') or {}).get('state', 'UNVERIFIED')}",
            "长期逻辑：仅供人工复核，不自动改变交易状态。",
        ]
        if manual_items:
            research_lines.append("待人工复核：" + "；".join(str(item) for item in manual_items))
        parts.append("行业研究提示：\n" + "\n".join(research_lines))

    parts.append("仅用于人工复核，不构成投资建议。")
    return "\n\n".join(parts)


def _html_body(result: dict, include_raw: bool = True, forced: bool = False) -> str:
    indicators = result["indicators"]
    decision = result["decision"]
    display_status = normalize_status(decision.get("status"))
    position = result["position_metrics"]
    asset = result["asset"]
    cycle = indicators.get("cycle_analysis", {}) or {}
    sell_reminder = result.get("sell_reminder", {}) or {}
    reminder_state = sell_reminder.get("state", {}) or {}

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

    data_status = result.get("data_status", {}) or {}
    data_status_rows = "".join(
        (
            '<div style="padding:6px 0;border-bottom:1px solid #edf2f7">'
            f'<b>{html.escape(label)}：</b>'
            f'{html.escape(str((data_status.get(key) or {}).get("status", "UNKNOWN")))}'
            '<br>'
            f'<span style="color:#64748b">'
            f'{html.escape(str((data_status.get(key) or {}).get("message", "无说明")))}'
            '</span></div>'
        )
        for key, label in [
            ("history", "历史日线"),
            ("realtime", "实时快照"),
            ("intraday", "一分钟分时"),
        ]
    )
    data_status_html = (
        '<div style="padding:14px 16px;background:#fff;border:1px solid #e2e8f0;'
        'border-radius:10px;margin-bottom:16px">'
        '<b>数据状态</b>'
        f'{data_status_rows}'
        '</div>'
    )

    research = result.get("research") or {}
    research_html = ""
    if research:
        manual_items = research.get("manual_review_items", [])[:3]
        manual_html = "".join(
            f"<li>{html.escape(str(item))}</li>" for item in manual_items
        )
        research_html = (
            '<div style="padding:14px 16px;background:#f8fbfd;border:1px solid #d8e9f3;'
            'border-radius:10px;margin-bottom:16px">'
            f'<b>行业研究提示：{html.escape(str(research.get("research_data_state", "UNKNOWN")))}</b>'
            f'<p style="margin:8px 0;color:#475569">数据源验证：{html.escape(str((research.get("source_reliability") or {}).get("state", "UNVERIFIED")))}</p>'
            '<p style="margin:8px 0;color:#475569">长期逻辑仅供人工复核，不自动改变交易状态。</p>'
            + (f'<ul style="margin:8px 0 0;padding-left:20px">{manual_html}</ul>' if manual_html else "")
            + '</div>'
        )

    news = result.get("news") or {}
    news_html = ""
    if news.get("status") == "SUCCESS" and str(news.get("summary") or "").strip():
        news_text = html.escape(str(news["summary"])).replace("\n", "<br>")
        news_html = (
            '<div style="padding:14px 16px;background:#f8fbfd;border:1px solid #d8e9f3;'
            'border-radius:10px;margin-bottom:16px">'
            '<b>近期资讯摘要（仅供人工复核）</b>'
            f'<p style="margin:8px 0;color:#475569">{news_text}</p>'
            f'<p style="margin:8px 0 0;color:#64748b;font-size:12px">检索时间：{html.escape(str(news.get("generated_at", "未知")))}</p>'
            '</div>'
        )

    metadata = f"""
    <div style="padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;margin-bottom:16px;line-height:1.8">
      <div><b>数据源：</b>{html.escape(str(result.get('data_source', '未知')))}</div>
      <div><b>数据阶段：</b>{html.escape(str(result.get('data_phase_label', result.get('data_phase', 'UNKNOWN'))))}（{html.escape(str(result.get('data_phase', 'UNKNOWN')))}）</div>
      <div><b>行情时间：</b>{html.escape(str(result.get('market_timestamp', '未知')))}</div>
      <div><b>实际运行时间：</b>{html.escape(str(result.get('run_time', result.get('generated_at', '未知'))))}</div>
      <div><b>计划时点：</b>{html.escape(str(result.get('run_slot') or '手动运行'))}</div>
    </div>
    """

    return f"""<!doctype html>
<html>
<body style="font-family:Arial,Microsoft YaHei,sans-serif;color:#172033;background:#f4f6f8;padding:24px">
  <div style="max-width:680px;margin:auto;background:white;border-radius:16px;padding:24px">
    {test_banner}
    <h2 style="margin:0 0 4px">{html.escape(asset['name'])}</h2>
    {summary}
    <p style="color:#64748b">{html.escape(asset['symbol'])}</p>
    <div style="display:inline-block;padding:8px 12px;background:#fff0cf;border-radius:999px;font-weight:700">
      {html.escape(display_status)}
    </div>
    <p><b>状态说明：</b>{html.escape(str(result.get('status_label', display_status)))}</p>
    <p><b>{html.escape(_suggested_amount_text(decision))}</b></p>
    <p><b>内部原因码：</b>{html.escape(str(decision.get('reason_code', 'NO_ACTION')))}</p>
    <p><b>当前周期：</b>{html.escape(str(cycle.get('current_cycle_id') or '尚未确认'))}</p>
    <p><b>历史样本：</b>回撤 {cycle.get('drawdown_sample_size', 0)} / 上涨 {cycle.get('rise_sample_size', 0)}</p>
    <p><b>确认高/低点：</b>{html.escape(str((cycle.get('last_confirmed_high') or {}).get('date', '无')))} / {html.escape(str((cycle.get('last_confirmed_low') or {}).get('date', '无')))}（确认日：{html.escape(str((cycle.get('last_confirmed_high') or {}).get('confirmed_date', '无')))} / {html.escape(str((cycle.get('last_confirmed_low') or {}).get('confirmed_date', '无')))}）</p>
    <p><b>当前回撤：</b>{cycle.get('current_drawdown_pct', '不适用')}%</p>
    <p><b>当前上涨：</b>{cycle.get('current_rise_pct', '不适用')}%</p>
    <p><b>回撤历史分位：</b>{cycle.get('current_drawdown_percentile', '不适用')}%</p>
    <p><b>上涨历史分位：</b>{cycle.get('current_rise_percentile', '不适用')}%</p>
    <p><b>反弹修复：</b>{cycle.get('recovery_pct') if cycle.get('recovery_pct') is not None else '不适用'}%</p>
    <p><b>资金流：</b>{html.escape(str(indicators.get('fund_flow_status', 'UNAVAILABLE')))}（{html.escape(str(indicators.get('fund_flow_impact', '中性')))}）</p>
    <h3>判断原因</h3>
    <ol>{reasons}</ol>
    {metadata}
    {data_status_html}
    {research_html}
    {news_html}
    {('<div><p><b>减仓提醒：</b>' + html.escape(str(sell_reminder.get('reason', '无'))) + '</p><p><b>参考份额：</b>' + html.escape(str(reminder_state.get('reference_shares', '不适用'))) + '</p><p><b>当前份额：</b>' + html.escape(str(reminder_state.get('current_shares', position.get('shares', 0)))) + '</p><p><b>已减仓：</b>' + html.escape(str(reminder_state.get('reduced_pct', 0))) + '%</p><p><b>剩余仓位：</b>' + html.escape(str(reminder_state.get('remaining_pct', 0))) + '%</p><p><b>第一档已提醒：</b>' + html.escape(str(len((reminder_state.get('first') or {}).get('dates', [])))) + ' 次</p><p><b>第二档已提醒：</b>' + html.escape(str(len((reminder_state.get('second') or {}).get('dates', [])))) + ' 次</p></div>') if decision.get('status') == 'PARTIAL_SELL' else ''}
    {raw}
    <p style="margin-top:20px;color:#64748b;font-size:12px">
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


def _compact_error_for_email(error: object, *, max_chars: int = 260) -> str:
    """Keep the diagnostic beginning and ending while removing bulky middles.

    Data-provider exceptions sometimes embed an entire DataFrame or response
    body.  The original exception remains in logs and run status; email only
    needs a phone-readable diagnostic with both the error type and final clue.
    """
    text = " ".join(str(error).split())
    if len(text) <= max_chars:
        return text
    head_chars = 150
    tail_chars = 80
    omitted = len(text) - head_chars - tail_chars
    return (
        f"{text[:head_chars]} … [中间已省略 {omitted} 个字符，完整记录已写入本地日志] … "
        f"{text[-tail_chars:]}"
    )


def send_data_error_email(
    *,
    asset: dict,
    errors: list[str],
    config: dict,
    project_root: Path,
    run_label: str,
    last_available_date: str | None = None,
    expected_date: str | None = None,
    logger: logging.Logger | None = None,
) -> None:
    email_config = config["email"]
    email_logger = logger or logging.getLogger("etf_email")
    settings = validate_graph_environment(email_config)
    token = acquire_graph_access_token(settings, project_root, email_logger)
    recipients = _parse_recipients(email_config.get("recipient"))
    escaped_errors = "".join(
        '<li style="margin-bottom:8px;word-break:break-word">'
        f"{html.escape(_compact_error_for_email(item))}</li>"
        for item in errors
    )
    subject = f"🔴 [{email_config.get('subject_prefix','ETF Monitor')}][DATA_ERROR][{run_label}] {asset.get('name', asset.get('symbol',''))}"
    content = f"""<html><body style="font-family:Arial,Microsoft YaHei,sans-serif">
    <h2>行情数据获取失败</h2>
    <p><b>标的：</b>{html.escape(str(asset.get('name','')))}（{html.escape(str(asset.get('symbol','')))})</p>
    <p><b>实际运行时间：</b>{html.escape(run_label)}</p>
    <p><b>本地最后可用行情：</b>{html.escape(str(last_available_date or '无'))}</p>
    <p><b>程序期望行情日期：</b>{html.escape(str(expected_date or '未知'))}</p>
    <p>程序已完成重试，但仍未取得足以生成新报告的数据。本次没有生成新的策略结论；如果已有旧报告，页面会明确标记为旧数据。</p>
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
