import json
from pathlib import Path

import pandas as pd


def append_xlsx(path, row, sheet):
    new = pd.DataFrame([row])
    if path.exists():
        try:
            old = pd.read_excel(path, sheet_name=sheet)
            new = pd.concat([old, new], ignore_index=True)
        except Exception:
            pass
    with pd.ExcelWriter(path, engine="openpyxl", mode="w") as writer:
        new.to_excel(writer, index=False, sheet_name=sheet)


def save_run_result(result, config, root: Path):
    history_path = root / config["storage"]["history_file"]
    decisions_path = root / config["storage"]["decisions_file"]
    latest_path = root / config["storage"]["latest_file"]
    history_path.parent.mkdir(parents=True, exist_ok=True)

    i = result["indicators"]
    a = result["asset"]
    d = result["decision"]

    append_xlsx(
        history_path,
        {
            "generated_at": result["generated_at"],
            "symbol": a["symbol"],
            "name": a["name"],
            "price": i["latest_price"],
            "daily_change_pct": i["daily_change_pct"],
            "five_day_change_pct": i["five_day_change_pct"],
            "ten_day_change_pct": i["ten_day_change_pct"],
            "twenty_day_change_pct": i["twenty_day_change_pct"],
            "one_month_drawdown_pct": i["one_month_drawdown_pct"],
            "three_month_drawdown_pct": i["three_month_drawdown_pct"],
            "six_month_drawdown_pct": i["six_month_drawdown_pct"],
            "drawdown_from_last_buy_pct": i["drawdown_from_last_buy_pct"],
            "consecutive_down_days": i["consecutive_down_days"],
            "consecutive_up_days": i["consecutive_up_days"],
            "rebound_pct": i["rebound_pct"],
            "is_new_10d_low": i["is_new_10d_low"],
            "stable_days": i["stable_days"],
            "data_source": result["data_source"],
        },
        "history",
    )

    append_xlsx(
        decisions_path,
        {
            "generated_at": result["generated_at"],
            "symbol": a["symbol"],
            "name": a["name"],
            "status": d["status"],
            "action_type": d.get("action_type", "NONE"),
            "suggested_amount": d["suggested_amount"],
            "reasons": "；".join(d["reasons"]),
            "should_notify": d["should_notify"],
        },
        "decisions",
    )

    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else {}
    except Exception:
        latest = {}

    latest[a["symbol"]] = result
    latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
