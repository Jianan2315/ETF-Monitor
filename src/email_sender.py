import os
import smtplib
from email.message import EmailMessage


def body(result: dict) -> str:
    indicators = result["indicators"]
    decision = result["decision"]
    reasons = "\n".join(
        f"{index + 1}. {reason}"
        for index, reason in enumerate(decision["reasons"])
    )
    summary = (
        f"模型摘要：\n{result['llm_summary']}\n\n"
        if result.get("llm_summary")
        else ""
    )

    return f"""{summary}基金：{result['asset']['name']}（{result['asset']['symbol']}）
状态：{decision['status']}
建议人工复核金额：{decision['suggested_amount']:.0f} 元

今日：{indicators['daily_change_pct']:.2f}%
近5日：{indicators['five_day_change_pct']:.2f}%
近10日：{indicators['ten_day_change_pct']:.2f}%
近20日：{indicators['twenty_day_change_pct']:.2f}%
近期高点回撤：{indicators['drawdown_from_recent_high_pct']:.2f}%
相对上次买入：{indicators['drawdown_from_last_buy_pct']:.2f}%
连续下跌：{indicators['consecutive_down_days']} 日
10日新低：{indicators['is_new_10d_low']}
稳定天数：{indicators['stable_days']}

判断原因：
{reasons}

仅用于人工复核，不构成投资建议。
"""


def send_notification_email(result: dict, config: dict) -> None:
    sender = os.getenv("OUTLOOK_EMAIL")
    password = os.getenv("OUTLOOK_PASSWORD")
    recipient = config["email"].get("recipient")

    if not sender or not password or not recipient:
        raise RuntimeError("邮箱配置不完整")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = (
        f"[ETF Monitor] {result['asset']['name']} "
        f"{result['decision']['status']}"
    )
    message.set_content(body(result))

    host = os.getenv("OUTLOOK_SMTP_HOST", "smtp.office365.com")
    port = int(os.getenv("OUTLOOK_SMTP_PORT", "587"))

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.send_message(message)
