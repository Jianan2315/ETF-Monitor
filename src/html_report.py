from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import shutil

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
        snapshot_html = html.replace('../static/', '../../static/').replace('href="history.html"', 'href="../history.html"')
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
        f'<tr><td>{date}</td><td>{time}</td><td>{mode}</td><td><a href="history/{name}">查看</a></td></tr>'
        for date,time,mode,name in rows
    )
    index_path.write_text(
        f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>历史报告</title>
        <style>body{{font-family:Arial,Microsoft YaHei,sans-serif;max-width:900px;margin:40px auto;padding:0 20px}}
        table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}</style>
        <h1>ETF Monitor 历史报告</h1><p><a href="index.html">返回最新报告</a></p>
        <table><thead><tr><th>日期</th><th>时点</th><th>类型</th><th>报告</th></tr></thead><tbody>{body}</tbody></table></html>""",
        encoding="utf-8",
    )
