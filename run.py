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


def build_chart_data(history):
    """Return every available history point; the browser chooses the visible range."""
    chart_history = history.copy()
    return [
        {
            "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
            "price": round(float(row["close"]), 4),
        }
        for _, row in chart_history.iterrows()
    ]


def build_position_metrics(asset, latest_price):
    """Calculate position-level values from cost basis and average cost.

    `invested_amount` is treated as the current cost basis.  For accurate
    profit figures, keep `average_cost` updated after every purchase or sale.
    """
    position = asset["position"]
    cost_basis = float(position.get("invested_amount", 0.0))
    average_cost = float(position.get("average_cost", position.get("last_buy_price", 0.0)))

    if cost_basis <= 0 or average_cost <= 0:
        return {
            "average_cost": average_cost,
            "estimated_shares": 0.0,
            "current_value": 0.0,
            "profit_amount": 0.0,
            "profit_pct": 0.0,
            "price_vs_average_cost_pct": 0.0,
        }

    shares = cost_basis / average_cost
    current_value = shares * latest_price
    profit_amount = current_value - cost_basis
    profit_pct = (current_value / cost_basis - 1) * 100

    return {
        "average_cost": round(average_cost, 4),
        "estimated_shares": round(shares, 4),
        "current_value": round(current_value, 2),
        "profit_amount": round(profit_amount, 2),
        "profit_pct": round(profit_pct, 2),
        "price_vs_average_cost_pct": round((latest_price / average_cost - 1) * 100, 2),
    }


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
                "position_metrics": build_position_metrics(asset, bundle.latest.price),
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
