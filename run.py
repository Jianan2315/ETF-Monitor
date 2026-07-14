from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from src.config_loader import load_config
from src.data_fetcher import DataFetchError, fetch_asset_data
from src.email_sender import evaluate_email_delivery, send_data_error_email, send_notification_email
from src.html_report import generate_html_report, mark_existing_report_stale
from src.indicators import calculate_indicators
from src.llm_client import generate_summary
from src.logger import configure_logging, get_email_logger
from src.position import calculate_position, load_transactions
from src.storage import save_run_result
from src.strategy import evaluate_strategy

ROOT = Path(__file__).resolve().parent

STATUS_LABELS = {
    "WAIT": "下跌未止，继续等待",
    "HOLD": "维持持仓，暂无操作",
    "WATCH": "观察反转确认",
    "REVIEW_BUY": "建议人工复核是否加仓",
    "PARTIAL_SELL": "建议人工复核是否部分卖出",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "intraday", "close", "manual"], default="auto")
    parser.add_argument("--slot", default="")
    return parser.parse_args()


def build_chart_data(history):
    return [{"date": row["date"].strftime("%Y-%m-%d"), "price": round(float(row["close"]), 4)}
            for _, row in history.iterrows()]


def build_intraday_data(frame):
    if frame is None or frame.empty:
        return []
    return [{"time": row["time"].strftime("%H:%M"), "price": round(float(row["price"]), 4)}
            for _, row in frame.iterrows()]



def format_market_time(timestamp, data_phase: str) -> str:
    if timestamp is None:
        return "未知"
    if data_phase == "INTRADAY":
        return timestamp.strftime("%Y-%m-%d %H:%M:%S")
    return timestamp.strftime("%Y-%m-%d")


def format_run_time(timestamp) -> str:
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    args = parse_args()
    logger = configure_logging(ROOT/"logs"/"monitor.log", ROOT/"logs"/"email.log")
    email_logger = get_email_logger()
    logger.info("ETF Monitor started | Mode=%s | Slot=%s", args.mode, args.slot or "auto")

    try:
        config = load_config(ROOT/"config")
    except Exception:
        logger.exception("配置加载失败")
        return 1

    results=[]
    transaction_path=ROOT/config["storage"]["transactions_file"]
    for configured_asset in config["assets"]:
        if not configured_asset.get("enabled", True):
            continue
        try:
            bundle=fetch_asset_data(configured_asset, config, mode=args.mode, project_root=ROOT)
        except DataFetchError as exc:
            run_time = datetime.now()
            logger.error(
                "数据获取失败 | Asset=%s | LastAvailable=%s | Errors=%s",
                configured_asset.get("symbol"),
                exc.last_available_date,
                exc.attempts,
            )
            try:
                expected_date = None
                if exc.last_available_date:
                    from src.data_fetcher import _market_phase, _expected_history_date
                    phase = _market_phase(run_time, args.mode)
                    expected_date = _expected_history_date(
                        run_time,
                        phase,
                    ).strftime("%Y-%m-%d")
                marked = mark_existing_report_stale(
                    ROOT,
                    config,
                    asset=configured_asset,
                    run_time=run_time,
                    last_available_date=exc.last_available_date,
                    expected_date=expected_date,
                    errors=exc.attempts,
                )
                logger.warning(
                    "Previous report stale warning injected=%s",
                    marked,
                )
            except Exception:
                logger.exception("旧报告过期警告写入失败")
            try:
                if config["email"].get("enabled", False):
                    send_data_error_email(
                        asset=configured_asset,
                        errors=exc.attempts,
                        config=config,
                        project_root=ROOT,
                        run_label=run_time.strftime("%Y-%m-%d %H:%M:%S"),
                        logger=email_logger,
                    )
            except Exception:
                logger.exception("DATA_ERROR 邮件发送失败")
            continue
        except Exception:
            logger.exception("处理 %s 失败", configured_asset.get("symbol"))
            continue

        transactions=load_transactions(transaction_path, configured_asset["symbol"])
        position_metrics=calculate_position(transactions,bundle.latest.price)
        asset={**configured_asset,"position":{**configured_asset.get("position",{}),**position_metrics}}
        indicators=calculate_indicators(asset,bundle.history,bundle.latest)
        decision=evaluate_strategy(asset,indicators)
        result={
            "asset":asset,
            "latest":bundle.latest.to_dict(),
            "indicators":indicators.to_dict(),
            "position_metrics":position_metrics,
            "decision":decision.to_dict(),
            "status_label":STATUS_LABELS.get(decision.status, decision.status),
            "data_source":bundle.data_source,
            "data_phase":bundle.data_phase,
            "market_timestamp":format_market_time(
                bundle.market_timestamp,
                bundle.data_phase,
            ),
            "generated_at":format_run_time(bundle.generated_at),
            "run_time":format_run_time(datetime.now()),
            "run_mode":args.mode,
            "run_slot":args.slot,
            "using_local_cache":bundle.using_local_cache,
            "cache_date":bundle.cache_date,
            "chart_data":build_chart_data(bundle.history),
            "intraday_chart_data":build_intraday_data(bundle.intraday),
            "operation_history":list(reversed(transactions[-10:])),
            "llm_summary":None,
        }
        save_run_result(result,config,ROOT)

        delivery=evaluate_email_delivery(result,config["email"])
        if delivery.should_send:
            if config["email"].get("include_llm_summary",True) and config["llm"].get("enabled",False):
                try:
                    result["llm_summary"]=generate_summary(result,config["llm"],logger=logger)
                except Exception:
                    logger.exception("生成模型摘要失败，继续发送无摘要邮件")
            try:
                send_notification_email(result,config,project_root=ROOT,forced=delivery.forced,logger=email_logger)
            except Exception:
                logger.exception("邮件发送失败")
        results.append(result)

    if results:
        try:
            path=generate_html_report(results,config,ROOT,run_mode=args.mode,run_slot=args.slot)
            logger.info("HTML report generated | Snapshot=%s", path)
        except Exception:
            logger.exception("生成 HTML 报告失败")
    else:
        logger.warning("No successful results; previous HTML report was preserved")

    logger.info("ETF Monitor finished")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
