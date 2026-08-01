from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import shutil
import re
from html import escape
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape


EMAIL_APP_REGISTRATION_URL = (
    "https://portal.azure.com/#view/Microsoft_AAD_IAM/"
    "ActiveDirectoryMenuBlade/~/RegisteredApps"
)
RUN_MODE_LABELS = {
    "manual": "手动触发",
    "auto": "定时：按当前时间判断",
    "intraday": "定时：盘中",
    "close": "定时：收盘后",
}


def _run_mode_label(mode: str) -> str:
    return RUN_MODE_LABELS.get(str(mode).lower(), str(mode))


def _environment(template_path: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(["html"]),
    )


def generate_html_report(
    results,
    config,
    root: Path,
    *,
    run_mode: str,
    run_slot: str,
    generated_at: datetime | None = None,
) -> Path:
    report = config["report"]
    template_path = root / report["template_file"]
    output_path = root / report["output_file"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generated_at = generated_at or datetime.now()

    html = _environment(template_path).get_template(template_path.name).render(
        monitor_name=config.get("monitor_name", "ETF Monitor"),
        results=results,
        run_mode=run_mode,
        run_mode_label=_run_mode_label(run_mode),
        run_slot=run_slot,
        report_generated_at=generated_at.isoformat(timespec="seconds"),
        asset_id=str(report.get("asset_id", "")),
        email_app_registration_url=EMAIL_APP_REGISTRATION_URL,
    )
    output_path.write_text(html, encoding="utf-8")

    snapshot_path = output_path
    if report.get("keep_history", True):
        history_dir = root / report.get("history_directory", "output/history")
        history_dir.mkdir(parents=True, exist_ok=True)
        snapshot_stem = f"{generated_at:%Y-%m-%d_%H%M%S%f}_{run_mode}"
        snapshot_path = history_dir / f"{snapshot_stem}.html"
        collision = 1
        while snapshot_path.exists():
            snapshot_path = history_dir / f"{snapshot_stem}_{collision}.html"
            collision += 1
        snapshot_html = html.replace('../static/', '../../static/')
        snapshot_path.write_text(snapshot_html, encoding="utf-8")
        _cleanup_history(history_dir, report)
        first_result = results[0] if results else {}
        first_asset = first_result.get("asset", {}) if isinstance(first_result, dict) else getattr(first_result, "asset", {})
        asset_name = _field(first_asset, "name") or "当前资产"
        asset_code = _field(first_asset, "asset_code")
        generate_history_index(
            root,
            asset_id=str(report.get("asset_id", "")),
            asset_name=str(asset_name),
            asset_code=str(asset_code or ""),
            history_dir=history_dir,
            index_path=root / report.get("history_index_file", "output/history.html"),
        )

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


def _field(value, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


_HISTORY_SNAPSHOT_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{12})_(?P<mode>[^_]+)(?:_\d+)?$"
)
_REPORT_GENERATED_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _stored_report_timestamp(path: Path) -> datetime | None:
    try:
        match = _REPORT_GENERATED_AT_RE.search(path.read_text(encoding="utf-8")[:65536])
        return datetime.fromisoformat(match.group(0)) if match else None
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _history_snapshot_details(path: Path) -> dict[str, object] | None:
    match = _HISTORY_SNAPSHOT_RE.match(path.stem)
    if not match:
        return None
    date = match.group("date")
    time_text = match.group("time")
    display_time = f"{time_text[:2]}:{time_text[2:4]}:{time_text[4:6]}"
    sortable = datetime.strptime(f"{date} {time_text}", "%Y-%m-%d %H%M%S%f")
    stored_timestamp = _stored_report_timestamp(path)
    if stored_timestamp:
        date = stored_timestamp.strftime("%Y-%m-%d")
        display_time = stored_timestamp.strftime("%H:%M:%S")
        sortable = stored_timestamp
    return {
        "date": date,
        "time": display_time,
        "mode": match.group("mode"),
        "mode_label": _run_mode_label(match.group("mode")),
        "name": path.name,
        "sort_key": sortable,
    }


def generate_history_index(
    root: Path,
    *,
    asset_id: str,
    asset_name: str,
    asset_code: str = "",
    history_dir: Path | None = None,
    index_path: Path | None = None,
) -> Path:
    history_dir = history_dir or root / "output" / "assets" / asset_id / "history"
    index_path = index_path or root / "output" / "assets" / asset_id / "history.html"
    details = [
        item for path in history_dir.glob("*.html")
        if (item := _history_snapshot_details(path)) is not None
    ]
    rows = sorted(
        details,
        key=lambda row: (row["sort_key"], row["name"]),
        reverse=True,
    )
    safe_asset_id = quote(asset_id, safe="")
    title = f"过往报告记录 — {asset_name}"
    subtitle = "查看当前资产过去生成的报告。"
    if asset_code:
        subtitle += f" 持有代码：{asset_code}。"
    if rows:
        body = "".join(
            "<tr>"
            f"<td>{escape(str(row['date']))}</td>"
            f"<td>{escape(str(row['time']))}</td>"
            f"<td>{escape(str(row['mode_label']))}</td>"
            f"<td><a class=\"secondary-button history-view-link\" href=\"/history/{safe_asset_id}/{quote(str(row['name']))}\">查看</a></td>"
            "</tr>"
            for row in rows
        )
        list_content = (
            "<div class=\"table-wrapper\"><table class=\"history-table\">"
            "<thead><tr><th>日期</th><th>生成时间</th><th>触发方式</th><th>报告</th></tr></thead>"
            f"<tbody>{body}</tbody></table></div>"
        )
    else:
        list_content = (
            "<div class=\"history-empty\"><h3>暂无过往报告</h3>"
            "<p>生成当前资产报告后，历史记录将显示在这里。</p></div>"
        )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><link rel="stylesheet" href="/control-static/control.css"></head><body><main class="shell"><header class="hero compact"><div><p class="eyebrow">REPORT HISTORY</p><h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div><div class="button-row"><a class="secondary-button" href="/report?asset_id={safe_asset_id}">返回最新报告</a><a class="secondary-button" href="/asset/manage?asset_id={safe_asset_id}">返回资产管理页面</a><a class="secondary-button" href="/">返回控制台</a></div></header><section class="card"><div class="section-title-row"><div><h2>报告列表</h2><p>按生成时间从新到旧排列。</p></div></div>{list_content}</section></main></body></html>""",
        encoding="utf-8",
    )
    return index_path



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
document.addEventListener('DOMContentLoaded', function() {{
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
