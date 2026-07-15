from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import shutil
import re
from html import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _environment(template_path: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(["html"]),
    )


def generate_html_report(results, config, root: Path, *, run_mode: str, run_slot: str) -> Path:
    report = config["report"]
    template_path = root / report["template_file"]
    output_path = root / report["output_file"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now()
    stamp = generated_at.strftime("%Y-%m-%d_%H%M%S")
    label = run_slot.replace(":", "") if run_slot else generated_at.strftime("%H%M")
    snapshot_name = f"{generated_at:%Y-%m-%d}_{label}_{run_mode}.html"

    html = _environment(template_path).get_template(template_path.name).render(
        monitor_name=config.get("monitor_name", "ETF Monitor"),
        results=results,
        run_mode=run_mode,
        run_slot=run_slot,
        report_generated_at=generated_at.isoformat(timespec="seconds"),
    )
    output_path.write_text(html, encoding="utf-8")

    snapshot_path = output_path
    if report.get("keep_history", True):
        history_dir = root / report.get("history_directory", "output/history")
        history_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = history_dir / snapshot_name
        snapshot_html = html.replace('../static/', '../../static/')
        snapshot_path.write_text(snapshot_html, encoding="utf-8")
        _cleanup_history(history_dir, report)
        _generate_history_index(root, report, history_dir)

    return snapshot_path


def _cleanup_history(history_dir: Path, report: dict) -> None:
    files = sorted(history_dir.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    max_files = int(report.get("max_history_files", 600))
    retention_days = int(report.get("retention_days", 180))
    cutoff = datetime.now() - timedelta(days=retention_days)
    for index, path in enumerate(files):
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        if index >= max_files or modified < cutoff:
            path.unlink(missing_ok=True)


def _generate_history_index(root: Path, report: dict, history_dir: Path) -> None:
    index_path = root / report.get("history_index_file", "output/history.html")
    rows = []
    for path in sorted(history_dir.glob("*.html"), reverse=True):
        parts = path.stem.split("_")
        date = parts[0] if parts else ""
        time = parts[1] if len(parts) > 1 else ""
        mode = parts[2] if len(parts) > 2 else ""
        rows.append((date, time, mode, path.name))
    body = "".join(
        f'<tr><td>{date}</td><td>{time}</td><td>{mode}</td><td><a href="/history/{name}">查看</a></td></tr>'
        for date,time,mode,name in rows
    )
    index_path.write_text(
        f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>历史报告</title>
        <style>body{{font-family:Arial,Microsoft YaHei,sans-serif;max-width:900px;margin:40px auto;padding:0 20px}}
        table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}</style>
        <h1>ETF Monitor 历史报告</h1><p><a href="/report">返回最新报告</a> · <a href="/">返回控制台</a></p>
        <table><thead><tr><th>日期</th><th>时点</th><th>类型</th><th>报告</th></tr></thead><tbody>{body}</tbody></table></html>""",
        encoding="utf-8",
    )



def mark_existing_report_stale(
    root: Path,
    config: dict,
    *,
    asset: dict,
    run_time: datetime,
    last_available_date: str | None,
    expected_date: str | None,
    errors: list[str],
) -> bool:
    """
    Preserve the previous successful report content, but inject a blocking
    warning modal and red banner so old data cannot be mistaken for a fresh run.
    """
    output_path = root / config["report"]["output_file"]
    if not output_path.exists():
        return False

    html = output_path.read_text(encoding="utf-8")
    # Remove an older injected warning before inserting the newest one.
    html = re.sub(
        r"<!-- DATA_STALE_WARNING_START -->[\s\S]*?<!-- DATA_STALE_WARNING_END -->",
        "",
        html,
    )

    error_items = "".join(
        f"<li>{escape(item)}</li>" for item in errors[-3:]
    )
    latest_text = escape(last_available_date or "未知")
    expected_text = escape(expected_date or "当前交易日所需数据")
    run_text = escape(run_time.strftime("%Y-%m-%d %H:%M:%S"))
    asset_text = escape(
        f"{asset.get('name', '')}（{asset.get('symbol', '')}）"
    )

    warning = f"""
<!-- DATA_STALE_WARNING_START -->
<style>
.data-stale-banner {{
  position: sticky; top: 0; z-index: 9998;
  background: #b42318; color: #fff; padding: 12px 18px;
  font-weight: 700; text-align: center;
  box-shadow: 0 3px 12px rgba(0,0,0,.22);
}}
.data-stale-modal {{
  position: fixed; inset: 0; z-index: 9999;
  display: flex; align-items: center; justify-content: center;
  background: rgba(15,23,42,.66); padding: 20px;
}}
.data-stale-dialog {{
  width: min(620px, 100%); background: white; color: #172033;
  border-radius: 16px; padding: 24px; box-shadow: 0 18px 55px rgba(0,0,0,.3);
}}
.data-stale-dialog h2 {{ color: #b42318; margin-top: 0; }}
.data-stale-dialog button {{
  border: 0; border-radius: 9px; padding: 10px 16px;
  background: #172033; color: white; cursor: pointer;
}}
.data-source-stale {{ color: #b42318 !important; font-weight: 700 !important; }}
</style>
<div class="data-stale-banner">
  数据更新失败：当前页面保留的是旧报告，不能视为本次运行结果。
</div>
<div class="data-stale-modal" id="data-stale-modal" role="dialog" aria-modal="true">
  <section class="data-stale-dialog">
    <h2>行情数据未能更新</h2>
    <p><b>标的：</b>{asset_text}</p>
    <p><b>本次运行时间：</b>{run_text}</p>
    <p><b>本地最后可用行情日期：</b>{latest_text}</p>
    <p><b>程序期望的最新行情日期：</b>{expected_text}</p>
    <p>页面主体仍是上一次成功生成的报告。请不要把其中的状态、涨跌幅或操作建议当作本次运行的新结果。</p>
    <details><summary>查看数据获取错误</summary><ol>{error_items}</ol></details>
    <p><button type="button" onclick="document.getElementById('data-stale-modal').style.display='none'">我已知晓，查看旧报告</button></p>
  </section>
</div>
<script>
document.querySelectorAll('.data-source-line, .muted').forEach(function(node) {{
  if (
    node.classList.contains('data-source-line') ||
    node.textContent.includes('数据阶段') ||
    node.textContent.includes('行情时间') ||
    node.textContent.includes('本次运行时间')
  ) {{
    node.classList.add('data-source-stale');
  }}
}});
</script>
<!-- DATA_STALE_WARNING_END -->
"""
    if "<body" in html:
        body_end = html.find(">", html.find("<body")) + 1
        html = html[:body_end] + warning + html[body_end:]
    else:
        html = warning + html

    output_path.write_text(html, encoding="utf-8")
    return True
