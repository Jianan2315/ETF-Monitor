from __future__ import annotations

from src.models import IndicatorResult, StrategyDecision

VALID_STATUSES = {"WAIT", "WATCH", "REVIEW_BUY", "HOLD", "PARTIAL_SELL"}


def evaluate_strategy(asset: dict, indicators: IndicatorResult) -> StrategyDecision:
    s = asset["strategy"]
    reasons: list[str] = []
    down_warning = int(s.get("consecutive_down_warning_days", 3))
    drop_trigger = float(s.get("drop_from_last_buy_trigger_pct", 8.0))
    stable_required = int(s.get("stable_days_required", 5))
    reversal_days = int(s.get("reversal_confirmation_days", 3))
    reversal_watch_days = int(s.get("reversal_watch_days", 2))
    minimum_rebound = float(s.get("minimum_rebound_pct", 8.0))
    normal_add = float(s.get("normal_add_amount", 10000))
    enhanced_add = float(s.get("enhanced_add_amount", 15000))

    # 1. 明显连续下跌且仍创新低：不接下跌中的刀子。
    if indicators.consecutive_down_days >= down_warning and indicators.is_new_10d_low:
        reasons.extend([
            f"连续下跌 {indicators.consecutive_down_days} 个交易日",
            "最新价格仍处于近10个交易日低位",
            "短期下跌尚未停止，默认不加仓",
        ])
        return StrategyDecision("WAIT", bool(s.get("notify_on_wait", False)), 0.0, reasons, "NONE")

    # 2. 下跌后的反转确认入口：不要求一定横盘。
    if indicators.preceding_down_days >= down_warning and indicators.consecutive_up_days >= reversal_days:
        reasons.extend([
            f"此前连续下跌 {indicators.preceding_down_days} 日",
            f"随后连续上涨 {indicators.consecutive_up_days} 日",
            f"本轮反弹累计 {indicators.rebound_pct:.2f}%",
        ])
        if indicators.rebound_pct >= minimum_rebound:
            reasons.append("达到反转确认阈值，进入正常额度人工复核")
            return StrategyDecision("REVIEW_BUY", bool(s.get("notify_on_review_buy", True)), normal_add, reasons, "REVERSAL_BUY")

    # 3. 反转尚未完全确认：只观察，不追第一天或第二天的反弹。
    if indicators.preceding_down_days >= down_warning and 1 <= indicators.consecutive_up_days <= reversal_watch_days:
        reasons.extend([
            f"此前连续下跌 {indicators.preceding_down_days} 日",
            f"目前仅连续上涨 {indicators.consecutive_up_days} 日",
            f"本轮反弹累计 {indicators.rebound_pct:.2f}%",
            "反转时间仍短，暂不追涨",
        ])
        return StrategyDecision("WATCH", bool(s.get("notify_on_watch", True)), 0.0, reasons, "NONE")

    # 4. 深度回撤并止跌横盘：使用增强额度复核。
    if indicators.drawdown_from_last_buy_pct <= -drop_trigger and indicators.stable_days >= stable_required and not indicators.is_new_10d_low:
        reasons.extend([
            f"相对上一笔买入价回撤 {abs(indicators.drawdown_from_last_buy_pct):.2f}%",
            f"最近 {indicators.stable_days} 个交易日波动收窄",
            "未继续创10日新低，满足止跌横盘入口",
        ])
        return StrategyDecision("REVIEW_BUY", bool(s.get("notify_on_review_buy", True)), enhanced_add, reasons, "STABILIZATION_BUY")

    # 5. 其他情况：持有现有底仓并继续观察。
    if indicators.drawdown_from_last_buy_pct <= -drop_trigger:
        reasons.append(f"相对上一笔买入价已回撤 {abs(indicators.drawdown_from_last_buy_pct):.2f}%")
        reasons.append("但尚未出现横盘或反转确认")
    else:
        reasons.append("尚未触发8%回撤加仓条件")
    return StrategyDecision("HOLD", False, 0.0, reasons, "NONE")
