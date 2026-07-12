from __future__ import annotations
import html, os, smtplib
from email.message import EmailMessage


def should_send_email(result: dict, email_config: dict) -> bool:
    if not email_config.get('enabled',False):
        return False
    mode=email_config.get('send_mode','trigger')
    if mode=='always':
        return True
    status=result['decision']['status']
    return bool(email_config.get('triggers',{}).get(status,False))


def _subject(result: dict, email_config: dict) -> str:
    prefix=email_config.get('subject_prefix','ETF Monitor')
    status=result['decision']['status']
    return f'[{prefix}][{status}] {result["asset"]["name"]}'


def _plain_body(result: dict, include_raw: bool=True) -> str:
    i=result['indicators']; d=result['decision']; p=result['position_metrics']; a=result['asset']
    parts=[]
    if result.get('llm_summary'):
        parts.append(f'模型摘要：\n{result["llm_summary"]}')
    parts.append((f"基金：{a['name']}（{a['symbol']}）\n状态：{d['status']}\n策略：{a['strategy'].get('name','')} v{a['strategy'].get('version','')}\n建议人工复核金额：{d['suggested_amount']:.0f} 元\n\n判断原因：\n" + '\n'.join(f'{n+1}. {r}' for n,r in enumerate(d['reasons']))))
    if include_raw:
        parts.append(f"""核心指标：\n今日：{i['daily_change_pct']:.2f}%\n近5日：{i['five_day_change_pct']:.2f}%\n近10日：{i['ten_day_change_pct']:.2f}%\n近20日：{i['twenty_day_change_pct']:.2f}%\n相对上次买入：{i['drawdown_from_last_buy_pct']:.2f}%\n当前总收益率：{p['profit_pct']:.2f}%\n当前总收益金额：{p['profit_amount']:.2f} 元\n已投入成本：{p['invested_amount']:.2f} 元\n目标上限：{a['position'].get('target_amount',0):.2f} 元""")
    parts.append(f'数据源：{result["data_source"]}\n生成时间：{result["generated_at"]}\n\n仅用于人工复核，不构成投资建议。')
    return '\n\n'.join(parts)


def _html_body(result: dict, include_raw: bool=True) -> str:
    i=result['indicators']; d=result['decision']; p=result['position_metrics']; a=result['asset']
    reasons=''.join(f'<li>{html.escape(str(r))}</li>' for r in d['reasons'])
    summary=f'<div style="padding:14px;background:#eef6fb;border-radius:10px;margin-bottom:16px"><b>模型摘要</b><p>{html.escape(result["llm_summary"])}</p></div>' if result.get('llm_summary') else ''
    raw=''
    if include_raw:
        rows=[('今日',f"{i['daily_change_pct']:.2f}%"),('近5日',f"{i['five_day_change_pct']:.2f}%"),('近10日',f"{i['ten_day_change_pct']:.2f}%"),('近20日',f"{i['twenty_day_change_pct']:.2f}%"),('相比上次买入',f"{i['drawdown_from_last_buy_pct']:.2f}%"),('总收益率',f"{p['profit_pct']:.2f}%"),('总收益金额',f"{p['profit_amount']:.2f} 元"),('已投入成本',f"{p['invested_amount']:.2f} 元")]
        raw='<table style="width:100%;border-collapse:collapse">'+''.join(f'<tr><td style="padding:8px;border-bottom:1px solid #eee;color:#64748b">{k}</td><td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-weight:700">{v}</td></tr>' for k,v in rows)+'</table>'
    return f"""<!doctype html><html><body style="font-family:Arial,Microsoft YaHei,sans-serif;color:#172033;background:#f4f6f8;padding:24px"><div style="max-width:680px;margin:auto;background:white;border-radius:16px;padding:24px">{summary}<h2 style="margin:0 0 4px">{html.escape(a['name'])}</h2><p style="color:#64748b">{html.escape(a['symbol'])} · {html.escape(a['strategy'].get('name',''))} v{html.escape(str(a['strategy'].get('version','')))}</p><div style="display:inline-block;padding:8px 12px;background:#fff0cf;border-radius:999px;font-weight:700">{html.escape(d['status'])}</div><p><b>建议人工复核金额：</b>{d['suggested_amount']:.0f} 元</p><h3>判断原因</h3><ol>{reasons}</ol>{raw}<p style="margin-top:20px;color:#64748b;font-size:12px">数据源：{html.escape(result['data_source'])}<br>生成时间：{html.escape(result['generated_at'])}<br>仅用于人工复核，不构成投资建议。</p></div></body></html>"""


def send_notification_email(result: dict, config: dict) -> None:
    email_config=config['email']
    sender=os.getenv('OUTLOOK_EMAIL')
    password=os.getenv('OUTLOOK_PASSWORD')
    recipient=email_config.get('recipient')
    if not sender: raise RuntimeError('缺少 OUTLOOK_EMAIL')
    if not password: raise RuntimeError('缺少 OUTLOOK_PASSWORD')
    if not recipient or recipient=='your_email@outlook.com': raise RuntimeError('请在 config/email.json 中配置 recipient')

    msg=EmailMessage(); msg['From']=sender; msg['To']=recipient; msg['Subject']=_subject(result,email_config)
    include_raw=bool(email_config.get('include_raw_metrics',True))
    msg.set_content(_plain_body(result,include_raw))
    if email_config.get('html',True):
        msg.add_alternative(_html_body(result,include_raw),subtype='html')
    host=os.getenv('OUTLOOK_SMTP_HOST','smtp.office365.com'); port=int(os.getenv('OUTLOOK_SMTP_PORT','587'))
    with smtplib.SMTP(host,port,timeout=30) as smtp:
        smtp.starttls(); smtp.login(sender,password); smtp.send_message(msg)
