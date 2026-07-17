from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config_loader import load_config
from src.data_fetcher import DataFetchError, fetch_asset_data
from src.email_sender import (
    evaluate_email_delivery,
    send_data_error_email,
    send_notification_email,
)
from src.html_report import (
    generate_html_report,
    mark_existing_report_stale,
)
from src.indicators import calculate_indicators
from src.llm_client import generate_summary
from src.logger import configure_logging, get_email_logger
from src.market_clock import (
    SESSION_PHASES,
    market_now,
    validate_run_mode,
)
from src.notification_guard import (
    data_error_email_decision,
    record_data_error_email_sent,
)
from src.position import calculate_position
from src.storage import save_run_result
from src.strategy import evaluate_strategy
from src.transactions import (
    load_transactions,
    reconcile_pending_transactions,
)


ROOT = Path(__file__).resolve().parent

STATUS_LABELS = {
    "WAIT": "下跌未止，继续等待",
    "HOLD": "维持持仓，暂无操作",
    "WATCH": "观察反转确认",
    "REVIEW_BUY": "建议人工复核是否加仓",
    "PARTIAL_SELL": "建议人工复核是否部分卖出",
}


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["auto", "intraday", "close", "manual"],
        default="auto",
    )
    parser.add_argument("--slot", default="")
    parser.add_argument("--asset", default="", help="Only run one configured asset id/code")
    return parser.parse_args(argv)


def build_chart_data(history):
    return [
        {
            "date": row["date"].strftime("%Y-%m-%d"),
            "price": round(float(row["close"]), 4),
        }
        for _, row in history.iterrows()
    ]


def build_intraday_data(frame):
    if frame is None or frame.empty:
        return []
    return [
        {
            "time": row["time"].strftime("%H:%M"),
            "price": round(float(row["price"]), 4),
        }
        for _, row in frame.iterrows()
    ]


def format_market_time(timestamp, data_phase: str) -> str:
    if timestamp is None:
        return "未知"
    if data_phase in SESSION_PHASES or (
        data_phase == "AFTER_CLOSE"
        and timestamp.time() != datetime.min.time()
    ):
        return timestamp.strftime("%Y-%m-%d %H:%M:%S")
    return timestamp.strftime("%Y-%m-%d")


def format_run_time(timestamp) -> str:
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")


def _step(
    key: str,
    label: str,
    status: str,
    message: str,
    details: Any = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "message": message,
        "details": details,
        "time": datetime.now().isoformat(timespec="seconds"),
    }


def _run_status_path() -> Path:
    return ROOT / "data" / "run_status" / "latest_run.json"


def _write_run_status(
    *,
    status: str,
    message: str,
    mode: str,
    phase: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    report_path: str | None = None,
    errors: list[str] | None = None,
    steps: list[dict[str, Any]] | None = None,
    data_status: dict[str, Any] | None = None,
    email_status: str = "UNKNOWN",
) -> None:
    path = _run_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "status": status,
        "message": message,
        "mode": mode,
        "phase": phase,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": (
            finished_at or market_now()
        ).isoformat(timespec="seconds"),
        "report_path": report_path,
        "errors": errors or [],
        "steps": steps or [],
        "data_status": data_status or {},
        "email_status": email_status,
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _data_steps(data_status: dict[str, Any]) -> list[dict[str, Any]]:
    labels = {
        "history": "历史日线",
        "realtime": "实时快照",
        "intraday": "今日一分钟分时",
        "previous_intraday": "昨日走势",
    }
    success_statuses = {
        "SUCCESS",
        "CACHE",
        "CACHE_CIRCUIT",
        "CACHE_PROVISIONAL_CLOSE",
        "CACHE_FALLBACK",
        "FALLBACK_FROM_MINUTE",
        "APPROXIMATE",
        "NOT_REQUIRED",
    }
    steps = []
    for key in ["history", "realtime", "intraday", "previous_intraday"]:
        item = data_status.get(key, {})
        raw_status = str(item.get("status", "UNKNOWN"))
        step_status = (
            "SUCCESS"
            if raw_status in success_statuses
            else "FAILED"
        )
        if raw_status in {"APPROXIMATE", "CACHE_FALLBACK", "CACHE_CIRCUIT"}:
            step_status = "WARNING"
        steps.append(
            _step(
                f"data_{key}",
                labels[key],
                step_status,
                item.get("message", raw_status),
                item,
            )
        )
    return steps


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logger = configure_logging(
        ROOT / "logs" / "monitor.log",
        ROOT / "logs" / "email.log",
    )
    email_logger = get_email_logger()
    steps: list[dict[str, Any]] = []
    email_status = "NOT_EVALUATED"
    combined_data_status: dict[str, Any] = {}

    try:
        config = load_config(ROOT / "config")
        steps.append(
            _step(
                "config",
                "读取配置",
                "SUCCESS",
                "配置加载成功",
            )
        )
    except Exception as exc:
        logger.exception("配置加载失败")
        steps.append(
            _step(
                "config",
                "读取配置",
                "FAILED",
                f"{type(exc).__name__}: {exc}",
            )
        )
        now = datetime.now()
        _write_run_status(
            status="CONFIG_ERROR",
            message="配置加载失败。",
            mode=args.mode,
            phase="UNKNOWN",
            started_at=now,
            errors=[f"{type(exc).__name__}: {exc}"],
            steps=steps,
            email_status=email_status,
        )
        return 3

    started_at = market_now(config)
    validation = validate_run_mode(args.mode, started_at)
    steps.append(
        _step(
            "market_validation",
            "检查运行时段",
            "SUCCESS" if validation.allowed else "FAILED",
            validation.message,
            validation.context.to_dict(),
        )
    )
    logger.info(
        "ETF Monitor started | Mode=%s | Slot=%s | Phase=%s | Time=%s",
        args.mode,
        args.slot or "manual",
        validation.context.phase,
        started_at.strftime("%Y-%m-%d %H:%M:%S"),
    )

    if not validation.allowed:
        _write_run_status(
            status="INVALID_MODE",
            message=validation.message,
            mode=args.mode,
            phase=validation.context.phase,
            started_at=started_at,
            steps=steps,
            email_status=email_status,
        )
        return 2

    results = []
    run_errors: list[str] = []
    transaction_path = (
        ROOT / config["storage"]["transactions_file"]
    )

    for configured_asset in config["assets"]:
        if not configured_asset.get("enabled", True):
            continue
        asset_id = str(configured_asset.get("id") or configured_asset.get("asset_code") or configured_asset.get("symbol") or "")
        if args.asset and args.asset not in {asset_id, str(configured_asset.get("asset_code", "")), str(configured_asset.get("tracking_code", "")), str(configured_asset.get("symbol", ""))}:
            continue
        tracking_code = str(configured_asset.get("tracking_code") or configured_asset.get("symbol") or "").strip()
        market_asset = {
            **configured_asset,
            "symbol": tracking_code,
            "name": configured_asset.get("tracking_name") or configured_asset.get("name"),
        }

        try:
            bundle = fetch_asset_data(
                market_asset,
                config,
                mode=args.mode,
                project_root=ROOT,
                logger=logger,
                now=started_at,
            )
            combined_data_status = bundle.data_status or {}
            steps.extend(_data_steps(combined_data_status))
        except DataFetchError as exc:
            run_time = market_now(config)
            run_errors.extend(exc.attempts or [str(exc)])
            steps.append(
                _step(
                    "data_fetch",
                    "获取行情数据",
                    "FAILED",
                    str(exc),
                    {
                        "last_available_date": exc.last_available_date,
                        "expected_date": exc.expected_date,
                        "attempts": exc.attempts,
                    },
                )
            )
            logger.error(
                "数据获取失败 | Asset=%s | LastAvailable=%s | Expected=%s | Errors=%s",
                configured_asset.get("symbol"),
                exc.last_available_date,
                exc.expected_date,
                exc.attempts,
            )
            try:
                marked = mark_existing_report_stale(
                    ROOT,
                    config,
                    asset=configured_asset,
                    run_time=run_time,
                    last_available_date=exc.last_available_date,
                    expected_date=exc.expected_date,
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
                    should_send, reason, fingerprint = (
                        data_error_email_decision(
                            project_root=ROOT,
                            config=config,
                            asset=configured_asset,
                            errors=exc.attempts,
                            last_available_date=exc.last_available_date,
                            now=run_time,
                        )
                    )
                    if should_send:
                        send_data_error_email(
                            asset=configured_asset,
                            errors=exc.attempts,
                            config=config,
                            project_root=ROOT,
                            run_label=run_time.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            last_available_date=exc.last_available_date,
                            expected_date=exc.expected_date,
                            logger=email_logger,
                        )
                        record_data_error_email_sent(
                            project_root=ROOT,
                            config=config,
                            fingerprint=fingerprint,
                            now=run_time,
                        )
                        email_status = "DATA_ERROR_SENT"
                    else:
                        email_status = f"DATA_ERROR_SKIPPED: {reason}"
                else:
                    email_status = "DISABLED"
            except Exception as email_exc:
                email_status = (
                    f"DATA_ERROR_SEND_FAILED: {email_exc}"
                )
                logger.exception("DATA_ERROR 邮件发送失败")
            continue
        except Exception as exc:
            run_errors.append(f"{type(exc).__name__}: {exc}")
            steps.append(
                _step(
                    "data_fetch",
                    "获取行情数据",
                    "FAILED",
                    f"{type(exc).__name__}: {exc}",
                )
            )
            logger.exception(
                "处理 %s 失败",
                configured_asset.get("symbol"),
            )
            continue

        try:
            transactions = reconcile_pending_transactions(
                transaction_path,
                str(configured_asset.get("asset_code") or configured_asset.get("id") or configured_asset.get("symbol")),
                bundle.history,
            )
            steps.append(
                _step(
                    "transactions",
                    "交易记录与待确认处理",
                    "SUCCESS",
                    f"已读取{len(transactions)}条交易记录；可用收盘数据会自动更新估算记录",
                )
            )
        except Exception as exc:
            logger.exception("交易记录自动处理失败")
            transactions = load_transactions(
                transaction_path,
                str(configured_asset.get("asset_code") or configured_asset.get("id") or configured_asset.get("symbol")),
            )
            steps.append(
                _step(
                    "transactions",
                    "交易记录与待确认处理",
                    "WARNING",
                    f"自动处理失败，已继续使用原记录：{exc}",
                )
            )

        try:
            position_metrics = calculate_position(
                transactions,
                bundle.latest.price,
            )
            asset = {
                **configured_asset,
                "symbol": tracking_code,
                "asset_code": str(configured_asset.get("asset_code") or configured_asset.get("id") or tracking_code),
                "tracking_code": tracking_code,
                "tracking_type": configured_asset.get("tracking_type", "ETF"),
                "asset_type": configured_asset.get("asset_type", "ETF"),
                "is_estimated_tracking": str(configured_asset.get("asset_code") or tracking_code) != tracking_code,
                "position": {
                    **configured_asset.get("position", {}),
                    **position_metrics,
                },
            }
            steps.append(
                _step(
                    "position",
                    "计算当前仓位",
                    "SUCCESS",
                    (
                        f"持有{position_metrics['shares']:.4f}份，"
                        f"投入成本{position_metrics['invested_amount']:.2f}元"
                    ),
                )
            )

            indicators = calculate_indicators(
                asset,
                bundle.history,
                bundle.latest,
            )
            steps.append(
                _step(
                    "indicators",
                    "计算行情指标",
                    "SUCCESS",
                    "涨跌幅、回撤、连续趋势和横盘指标计算完成",
                )
            )

            decision = evaluate_strategy(
                asset,
                indicators,
            )
            steps.append(
                _step(
                    "strategy",
                    "执行策略规则",
                    "SUCCESS",
                    f"策略状态：{decision.status}",
                    decision.to_dict(),
                )
            )
        except Exception as exc:
            run_errors.append(f"Calculation: {type(exc).__name__}: {exc}")
            steps.append(
                _step(
                    "calculation",
                    "仓位、指标与策略",
                    "FAILED",
                    f"{type(exc).__name__}: {exc}",
                )
            )
            logger.exception("指标或策略计算失败")
            continue

        result = {
            "asset": asset,
            "latest": bundle.latest.to_dict(),
            "indicators": indicators.to_dict(),
            "position_metrics": position_metrics,
            "decision": decision.to_dict(),
            "status_label": STATUS_LABELS.get(
                decision.status,
                decision.status,
            ),
            "data_source": bundle.data_source,
            "data_phase": bundle.data_phase,
            "data_phase_label": (
                bundle.phase_label or bundle.data_phase
            ),
            "data_warnings": bundle.data_warnings or [],
            "data_status": bundle.data_status or {},
            "market_timestamp": format_market_time(
                bundle.market_timestamp,
                bundle.data_phase,
            ),
            "generated_at": format_run_time(
                bundle.generated_at
            ),
            "run_time": format_run_time(
                market_now(config)
            ),
            "run_mode": args.mode,
            "run_slot": args.slot,
            "using_local_cache": bundle.using_local_cache,
            "cache_date": bundle.cache_date,
            "chart_data": build_chart_data(
                bundle.history
            ),
            "intraday_chart_data": build_intraday_data(
                bundle.intraday
            ),
            "previous_intraday_chart_data": build_intraday_data(
                bundle.previous_intraday
            ),
            "operation_history": list(
                reversed(transactions[-10:])
            ),
            "llm_summary": None,
        }

        try:
            save_run_result(
                result,
                config,
                ROOT,
            )
            steps.append(
                _step(
                    "storage",
                    "保存运行数据",
                    "SUCCESS",
                    "latest.json、历史指标和决策记录已更新",
                )
            )
        except Exception as exc:
            logger.exception("保存运行数据失败")
            steps.append(
                _step(
                    "storage",
                    "保存运行数据",
                    "WARNING",
                    f"{type(exc).__name__}: {exc}",
                )
            )

        delivery = evaluate_email_delivery(
            result,
            config["email"],
        )
        if delivery.should_send:
            if (
                config["email"].get(
                    "include_llm_summary",
                    True,
                )
                and config["llm"].get("enabled", False)
            ):
                try:
                    result["llm_summary"] = generate_summary(
                        result,
                        config["llm"],
                        logger=logger,
                    )
                    steps.append(
                        _step(
                            "llm",
                            "生成本地模型摘要",
                            "SUCCESS",
                            "模型摘要生成成功",
                        )
                    )
                except Exception as exc:
                    steps.append(
                        _step(
                            "llm",
                            "生成本地模型摘要",
                            "WARNING",
                            f"模型摘要失败，继续发送无摘要邮件：{exc}",
                        )
                    )
                    logger.exception(
                        "生成模型摘要失败，继续发送无摘要邮件"
                    )
            else:
                steps.append(
                    _step(
                        "llm",
                        "生成本地模型摘要",
                        "SKIPPED",
                        "本次邮件不需要模型摘要",
                    )
                )

            try:
                send_notification_email(
                    result,
                    config,
                    project_root=ROOT,
                    forced=delivery.forced,
                    logger=email_logger,
                )
                email_status = (
                    "TEST_SENT"
                    if delivery.forced
                    else "SENT"
                )
                steps.append(
                    _step(
                        "email",
                        "发送邮件",
                        "SUCCESS",
                        (
                            "测试邮件已发送"
                            if delivery.forced
                            else "邮件已发送"
                        ),
                    )
                )
            except Exception as exc:
                email_status = f"SEND_FAILED: {exc}"
                steps.append(
                    _step(
                        "email",
                        "发送邮件",
                        "FAILED",
                        f"{type(exc).__name__}: {exc}",
                    )
                )
                logger.exception("邮件发送失败")
        else:
            email_status = f"SKIPPED: {delivery.reason}"
            steps.append(
                _step(
                    "email",
                    "发送邮件",
                    "SKIPPED",
                    delivery.reason,
                )
            )

        results.append(result)

    if results:
        try:
            path = generate_html_report(
                results,
                config,
                ROOT,
                run_mode=args.mode,
                run_slot=args.slot,
            )
            report_path = str(
                ROOT / config["report"]["output_file"]
            )
            steps.append(
                _step(
                    "html_report",
                    "生成网页报告",
                    "SUCCESS",
                    f"报告已生成：{report_path}",
                )
            )
            logger.info(
                "HTML report generated | Snapshot=%s",
                path,
            )

            history_state = combined_data_status.get(
                "history",
                {},
            ).get("status", "UNKNOWN")
            realtime_state = combined_data_status.get(
                "realtime",
                {},
            ).get("status", "UNKNOWN")
            intraday_state = combined_data_status.get(
                "intraday",
                {},
            ).get("status", "UNKNOWN")
            previous_intraday_state = combined_data_status.get(
                "previous_intraday",
                {},
            ).get("status", "UNKNOWN")
            message = (
                "报告已生成。"
                f"历史日线={history_state}；"
                f"实时快照={realtime_state}；"
                f"一分钟分时={intraday_state}；"
                f"昨日走势={previous_intraday_state}；"
                f"邮件={email_status}。"
            )
            _write_run_status(
                status="SUCCESS",
                message=message,
                mode=args.mode,
                phase=validation.context.phase,
                started_at=started_at,
                report_path=report_path,
                steps=steps,
                data_status=combined_data_status,
                email_status=email_status,
            )
            logger.info("ETF Monitor finished")
            return 0
        except Exception as exc:
            logger.exception("生成 HTML 报告失败")
            run_errors.append(
                f"HTML report: {type(exc).__name__}: {exc}"
            )
            steps.append(
                _step(
                    "html_report",
                    "生成网页报告",
                    "FAILED",
                    f"{type(exc).__name__}: {exc}",
                )
            )

    logger.warning(
        "No successful results; previous HTML report was preserved"
    )
    _write_run_status(
        status="DATA_ERROR",
        message=(
            "未能生成新的报告。请根据步骤列表查看具体失败环节，"
            "而不是仅依赖笼统的DATA_ERROR提示。"
        ),
        mode=args.mode,
        phase=validation.context.phase,
        started_at=started_at,
        errors=run_errors,
        steps=steps,
        data_status=combined_data_status,
        email_status=email_status,
    )
    logger.info("ETF Monitor finished")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
