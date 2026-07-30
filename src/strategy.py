from __future__ import annotations

from src.models import IndicatorResult, StrategyDecision


VALID_STATUSES = {"OBSERVE", "REVIEW_BUY", "REVIEW_SELL", "PARTIAL_SELL"}


def _observe(code: str, reasons: list[str], indicators: IndicatorResult) -> StrategyDecision:
    return StrategyDecision(
        "OBSERVE", False, 0.0, reasons, "NONE", code,
        {"cycle": indicators.cycle_analysis or {}, "fund_flow": indicators.fund_flow_status},
    )


def evaluate_strategy(asset: dict, indicators: IndicatorResult) -> StrategyDecision:
    """Produce one of the three user-visible strategy states.

    Turning-point statistics are calculated from already confirmed historical
    cycles.  The current price is compared with those distributions without
    looking ahead, and fund flow only adjusts confidence rather than becoming a
    mandatory price-signal gate.
    """
    strategy = asset["strategy"]
    strategy_type = str(strategy.get("strategy_type", "CYCLICAL")).upper()
    if strategy_type not in {"TREND", "CYCLICAL"}:
        strategy_type = "CYCLICAL"
    cycle = indicators.cycle_analysis or {}
    shares = float(asset.get("position", {}).get("shares", 0) or 0)
    recovery_required = float(strategy.get("rebound_recovery_threshold_pct", 80.0))
    deep_percentile = float(strategy.get("deep_drawdown_percentile", 85.0))
    sell_first_percentile = float(strategy.get("sell_first_percentile", 50.0))
    sell_second_percentile = float(strategy.get("sell_second_percentile", 90.0))
    early_points = float(strategy.get("sell_early_alert_points", 3.0))
    normal_add = float(strategy.get("normal_add_amount") or 0)
    enhanced_add = float(strategy.get("enhanced_add_amount") or 0)
    downtrend_watch_percentile = float(strategy.get("downtrend_watch_percentile", 38.0))
    downtrend_risk_percentile = float(strategy.get("downtrend_risk_percentile", 62.0))
    sample_size = int(cycle.get("sample_size", 0) or 0)
    recovery = cycle.get("recovery_pct")
    drawdown_rank = cycle.get("current_drawdown_percentile")
    rise = cycle.get("current_rise_pct")
    rises = cycle.get("rises", []) or []
    downtrend_active = cycle.get("active_phase") == "DOWN"
    downtrend_samples = int(cycle.get("drawdown_sample_size") or sample_size or 0)

    # Selling is based on the move from the confirmed low and historical rise
    # distribution, never on a personal cost basis or account return.
    if shares > 0 and sample_size and rise is not None and rises:
        from src.market_cycles import percentile_value

        first_line = percentile_value(rises, sell_first_percentile)
        second_line = percentile_value(rises, sell_second_percentile)
        if second_line is not None and rise >= second_line - early_points:
            return StrategyDecision(
                "PARTIAL_SELL", True, 0.0,
                [
                    f"本轮自确认低点上涨 {rise:.2f}%",
                    f"接近历史上涨 {sell_second_percentile:.0f}% 分位参考线 {second_line:.2f}%（提前 {early_points:.1f} 个百分点提醒）",
                    "已进入历史高位区间，建议重点人工复核继续减仓或退出。",
                    indicators.fund_flow_impact,
                ],
                "SELL", "EXIT_REVIEW",
                {"cycle": cycle, "tier": 2, "trigger_line_pct": second_line - early_points, "strategy_type": strategy_type},
            )
        # A trend asset keeps its normal position through ordinary advances and
        # only asks for a high-zone exit review.  A cyclical asset also gets
        # the median-zone staged-sale review.
        if strategy_type == "CYCLICAL" and first_line is not None and rise >= first_line - early_points:
            return StrategyDecision(
                "PARTIAL_SELL", True, 0.0,
                [
                    f"本轮自确认低点上涨 {rise:.2f}%",
                    f"接近历史上涨 {sell_first_percentile:.0f}% 分位参考线 {first_line:.2f}%（提前 {early_points:.1f} 个百分点提醒）",
                    "已进入历史常见的上涨兑现区间，建议人工复核分批减仓。",
                    indicators.fund_flow_impact,
                ],
                "SELL", "SELL_REVIEW",
                {"cycle": cycle, "tier": 1, "trigger_line_pct": first_line - early_points, "strategy_type": strategy_type},
            )

    # A confirmed downward phase is not an automatic sell. It first blocks
    # premature buy suggestions and only escalates to manual exit review.
    if downtrend_active and downtrend_samples and drawdown_rank is not None:
        common_reasons = [
            f"当前仍处于确认后的下行阶段，回撤位于历史下跌幅度的 {drawdown_rank:.2f}% 分位",
            f"重点观察阈值为 {downtrend_watch_percentile:.2f}% 分位；下行风险阈值为 {downtrend_risk_percentile:.2f}% 分位",
            "尚未出现从阶段低点反弹达到拐点确认幅度的止跌确认，应由人工结合基本面判断等待、减仓或继续持有。",
            indicators.fund_flow_impact,
        ]
        if shares > 0 and drawdown_rank >= downtrend_risk_percentile:
            return StrategyDecision(
                "REVIEW_SELL", True, 0.0,
                common_reasons + ["当前下行风险已达到人工复核级别；不自动卖出。"],
                "SELL", "DOWNTREND_RISK_REVIEW",
                {
                    "cycle": cycle,
                    "watch_percentile": downtrend_watch_percentile,
                    "risk_percentile": downtrend_risk_percentile,
                    "strategy_type": strategy_type,
                },
            )
        if drawdown_rank >= downtrend_watch_percentile:
            return _observe("DOWNTREND_WATCH", common_reasons, indicators)

    # A large one-day rebound can qualify: recovery, not consecutive up days,
    # is the confirmation condition.
    if cycle.get("last_confirmed_low") and recovery is not None and recovery >= recovery_required:
        return StrategyDecision(
            "REVIEW_BUY", True, normal_add,
            [
                f"本轮反弹已修复此前下跌的 {recovery:.2f}%",
                f"达到反弹修复确认阈值 {recovery_required:.2f}%",
                (f"建议人工复核加仓金额：{normal_add:.0f} 元。" if normal_add > 0 else "加仓金额由人工决定。"),
                indicators.fund_flow_impact,
            ],
            "BUY", "REVERSAL_RECOVERY",
            {"cycle": cycle, "recovery_threshold_pct": recovery_required, "strategy_type": strategy_type},
        )

    # A deep historical-percentile drawdown needs evidence of stabilization or
    # rebound; depth alone never triggers a buy review.
    stabilized = indicators.stable_days >= int(strategy.get("stable_days_required", 5)) or (recovery is not None and recovery > 0)
    if sample_size and drawdown_rank is not None and drawdown_rank >= deep_percentile and stabilized:
        return StrategyDecision(
            "REVIEW_BUY", True, enhanced_add,
            [
                f"当前回撤位于历史回撤的 {drawdown_rank:.2f}% 分位",
                f"达到深度回撤参考阈值 {deep_percentile:.2f}% 分位，且已出现止跌或反弹确认",
                (f"建议人工复核加仓金额：{enhanced_add:.0f} 元。" if enhanced_add > 0 else "加仓金额由人工决定。"),
                indicators.fund_flow_impact,
            ],
            "BUY", "DEEP_DRAWDOWN_REVERSAL",
            {"cycle": cycle, "drawdown_percentile_threshold": deep_percentile, "strategy_type": strategy_type},
        )

    if indicators.consecutive_down_days:
        return _observe("DECLINE_UNCONFIRMED", [
            f"当前连续下跌 {indicators.consecutive_down_days} 个交易日，尚未形成明确反转。",
            indicators.fund_flow_impact,
        ], indicators)
    if recovery is not None and recovery < recovery_required:
        return _observe("RECOVERY_INSUFFICIENT", [
            f"本轮反弹修复 {recovery:.2f}%，尚未达到 {recovery_required:.2f}% 的确认阈值。",
            indicators.fund_flow_impact,
        ], indicators)
    return _observe("NO_CLEAR_SIGNAL", [
        "当前没有形成需要加仓或减仓的明确信号，维持现有仓位并继续观察。",
        indicators.fund_flow_impact,
    ], indicators)
