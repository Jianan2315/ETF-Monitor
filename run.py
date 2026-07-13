from __future__ import annotations

from pathlib import Path

from src.config_loader import load_config
from src.data_fetcher import fetch_asset_data
from src.email_sender import (
    evaluate_email_delivery,
    send_notification_email,
)
from src.html_report import generate_html_report
from src.indicators import calculate_indicators
from src.llm_client import generate_summary
from src.logger import configure_logging, get_email_logger
from src.position import calculate_position, load_transactions
from src.storage import save_run_result
from src.strategy import evaluate_strategy


ROOT = Path(__file__).resolve().parent


def build_chart_data(history):
    return [
        {
            "date": (
                row["date"].strftime("%Y-%m-%d")
                if hasattr(row["date"], "strftime")
                else str(row["date"])
            ),
            "price": round(float(row["close"]), 4),
        }
        for _, row in history.iterrows()
    ]


def main() -> int:
    logger = configure_logging(
        ROOT / "logs" / "monitor.log",
        ROOT / "logs" / "email.log",
    )
    email_logger = get_email_logger()

    logger.info("ETF Monitor started")

    try:
        config = load_config(ROOT / "config")
    except Exception:
        logger.exception("配置加载失败")
        return 1

    results = []
    transaction_path = ROOT / config["storage"]["transactions_file"]
    email_config = config["email"]

    email_logger.info("Email subsystem initialized")
    email_logger.info(
        "Email enabled=%s | Send mode=%s | Debug enabled=%s | Force send=%s",
        email_config.get("enabled", False),
        email_config.get("send_mode", "trigger"),
        (email_config.get("debug", {}) or {}).get("enabled", False),
        (email_config.get("debug", {}) or {}).get("force_send_email", False),
    )

    for configured_asset in config["assets"]:
        if not configured_asset.get("enabled", True):
            logger.info(
                "Skipping disabled asset: %s",
                configured_asset.get("symbol"),
            )
            continue

        try:
            bundle = fetch_asset_data(configured_asset, config)
            transactions = load_transactions(
                transaction_path,
                configured_asset["symbol"],
            )
            position_metrics = calculate_position(
                transactions,
                bundle.latest.price,
            )

            asset = {
                **configured_asset,
                "position": {
                    **configured_asset.get("position", {}),
                    **position_metrics,
                },
            }
            indicators = calculate_indicators(
                asset,
                bundle.history,
                bundle.latest,
            )
            decision = evaluate_strategy(asset, indicators)

            result = {
                "asset": asset,
                "latest": bundle.latest.to_dict(),
                "indicators": indicators.to_dict(),
                "position_metrics": position_metrics,
                "decision": decision.to_dict(),
                "data_source": bundle.data_source,
                "generated_at": bundle.generated_at.isoformat(),
                "chart_data": build_chart_data(bundle.history),
                "operation_history": list(reversed(transactions[-10:])),
                "llm_summary": None,
            }

            # Persist data before any optional external-service call.
            save_run_result(result, config, ROOT)

            delivery = evaluate_email_delivery(result, email_config)
            email_logger.info(
                "Email decision | Asset=%s | Status=%s | Should send=%s | Reason=%s",
                configured_asset["symbol"],
                delivery.status,
                delivery.should_send,
                delivery.reason,
            )

            if delivery.should_send:
                if (
                    email_config.get("include_llm_summary", True)
                    and config["llm"].get("enabled", False)
                ):
                    try:
                        logger.info(
                            "Generating LLM summary for email: %s",
                            configured_asset["symbol"],
                        )
                        result["llm_summary"] = generate_summary(
                            result,
                            config["llm"],
                        )
                    except Exception:
                        logger.exception(
                            "生成模型摘要失败，继续发送无摘要邮件"
                        )
                else:
                    email_logger.info(
                        "LLM summary skipped | include_llm_summary=%s | llm.enabled=%s",
                        email_config.get("include_llm_summary", True),
                        config["llm"].get("enabled", False),
                    )

                try:
                    send_notification_email(
                        result,
                        config,
                        project_root=ROOT,
                        forced=delivery.forced,
                        logger=email_logger,
                    )
                except Exception:
                    # The email module already writes the full traceback to email.log.
                    logger.error(
                        "邮件发送失败，详情见 logs/email.log；数据、Excel 与网页仍继续生成"
                    )
            else:
                email_logger.info(
                    "Email skipped | Asset=%s | Reason=%s",
                    configured_asset["symbol"],
                    delivery.reason,
                )

            results.append(result)

        except Exception:
            logger.exception(
                "处理 %s 失败",
                configured_asset.get("symbol"),
            )

    if results:
        try:
            generate_html_report(results, config, ROOT)
            logger.info("HTML report generated")
        except Exception:
            logger.exception("生成 HTML 报告失败")

    logger.info("ETF Monitor finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
