from src.models import IndicatorResult
from src.strategy import evaluate_strategy


def asset(shares=100):
    return {"position": {"shares": shares}, "strategy": {
        "strategy_type": "CYCLICAL", "rebound_recovery_threshold_pct": 80,
        "deep_drawdown_percentile": 85, "sell_first_percentile": 50,
        "sell_second_percentile": 90, "sell_early_alert_points": 3,
        "normal_add_amount": 10000, "enhanced_add_amount": 15000,
        "stable_days_required": 5,
    }}


def ind(**overrides):
    base = dict(
        daily_change_pct=0, five_day_change_pct=0, ten_day_change_pct=0, twenty_day_change_pct=0,
        one_month_drawdown_pct=-5, three_month_drawdown_pct=-10, six_month_drawdown_pct=-12,
        one_month_high_date="2026-07-01", three_month_high_date="2026-06-01", six_month_high_date="2026-03-01",
        drawdown_from_last_buy_pct=-2, consecutive_down_days=0, consecutive_up_days=0,
        consecutive_trend_pct=0, preceding_down_days=0, rebound_pct=0,
        is_new_5d_low=False, is_new_10d_low=False, stable_days=1, latest_price=1,
        cycle_analysis={}, fund_flow_status="UNAVAILABLE",
    )
    base.update(overrides)
    return IndicatorResult(**base)


def test_new_strategy_only_generates_observe_without_a_confirmed_signal():
    decision = evaluate_strategy(asset(), ind(consecutive_down_days=4))
    assert decision.status == "OBSERVE"
    assert decision.reason_code == "DECLINE_UNCONFIRMED"


def test_single_day_rebound_can_trigger_review_buy_by_recovery_ratio():
    decision = evaluate_strategy(asset(), ind(cycle_analysis={
        "last_confirmed_low": {"date": "2026-07-01"}, "recovery_pct": 82,
        "sample_size": 3,
    }))
    assert decision.status == "REVIEW_BUY"
    assert decision.reason_code == "REVERSAL_RECOVERY"
    assert decision.suggested_amount == 10000


def test_deep_drawdown_needs_stabilization_before_review_buy():
    cycle = {"sample_size": 4, "current_drawdown_percentile": 90, "recovery_pct": 0}
    assert evaluate_strategy(asset(), ind(cycle_analysis=cycle, stable_days=1)).status == "OBSERVE"
    decision = evaluate_strategy(asset(), ind(cycle_analysis=cycle, stable_days=5))
    assert decision.status == "REVIEW_BUY"
    assert decision.reason_code == "DEEP_DRAWDOWN_REVERSAL"


def test_sell_tiers_use_rise_distribution_not_personal_profit():
    cycle = {"sample_size": 4, "current_rise_pct": 21, "rises": [10, 15, 20, 30]}
    decision = evaluate_strategy(asset(), ind(cycle_analysis=cycle))
    assert decision.status == "PARTIAL_SELL"
    assert decision.reason_code == "SELL_REVIEW"
    cycle["current_rise_pct"] = 25
    decision = evaluate_strategy(asset(), ind(cycle_analysis=cycle))
    assert decision.reason_code == "EXIT_REVIEW"


def test_sell_never_triggers_without_shares():
    decision = evaluate_strategy(asset(shares=0), ind(cycle_analysis={"sample_size": 4, "current_rise_pct": 30, "rises": [10, 15, 20, 30]}))
    assert decision.status == "OBSERVE"


def test_trend_assets_skip_median_zone_sell_but_keep_high_zone_exit_review():
    trend_asset = asset()
    trend_asset["strategy"]["strategy_type"] = "TREND"
    cycle = {"sample_size": 4, "current_rise_pct": 21, "rises": [10, 15, 20, 30]}
    assert evaluate_strategy(trend_asset, ind(cycle_analysis=cycle)).status == "OBSERVE"
    cycle["current_rise_pct"] = 25
    assert evaluate_strategy(trend_asset, ind(cycle_analysis=cycle)).reason_code == "EXIT_REVIEW"
