from pathlib import Path

from src.config_loader import load_config
from src.data_fetcher import fetch_asset_data
from src.indicators import calculate_indicators
from src.strategy import evaluate_strategy
from src.storage import save_run_result
from src.html_report import generate_html_report
from src.llm_client import generate_summary
from src.email_sender import send_notification_email
from src.logger import configure_logging

ROOT = Path(__file__).resolve().parent


def build_chart_data(history, days=126):
    chart_history = history.tail(days).copy()
    base_price = float(chart_history.iloc[0]["close"])
    return [
        {
            "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
            "price": round(float(row["close"]), 4),
            "change_pct": round((float(row["close"]) / base_price - 1) * 100, 2),
        }
        for _, row in chart_history.iterrows()
    ]


def main():
    logger = configure_logging(ROOT / "logs" / "monitor.log")
    config = load_config(ROOT / "config.json")
    results = []

    for asset in config["assets"]:
        if not asset.get("enabled", True):
            continue

        try:
            bundle = fetch_asset_data(asset, config)
            indicators = calculate_indicators(asset, bundle.history, bundle.latest)
            decision = evaluate_strategy(asset, indicators)
            result = {
                "asset": asset,
                "latest": bundle.latest.to_dict(),
                "indicators": indicators.to_dict(),
                "decision": decision.to_dict(),
                "data_source": bundle.data_source,
                "generated_at": bundle.generated_at.isoformat(),
                "chart_data": build_chart_data(bundle.history),
                "llm_summary": None,
            }

            save_run_result(result, config, ROOT)

            if config["llm"].get("enabled") and decision.should_notify:
                result["llm_summary"] = generate_summary(result, config["llm"])

            if config["email"].get("enabled") and (
                decision.should_notify
                or not config["email"].get("send_only_when_triggered", True)
            ):
                send_notification_email(result, config)

            results.append(result)
        except Exception:
            logger.exception("处理 %s 失败", asset.get("symbol"))

    if results:
        generate_html_report(results, config, ROOT)

    logger.info("ETF Monitor finished")


if __name__ == "__main__":
    main()
