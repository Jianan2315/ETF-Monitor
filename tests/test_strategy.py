from src.models import IndicatorResult
from src.strategy import evaluate_strategy


def asset():
    return {"strategy": {
        "drop_from_last_buy_trigger_pct": 8.0,
        "stable_days_required": 5,
        "consecutive_down_warning_days": 3,
        "reversal_confirmation_days": 3,
        "reversal_watch_days": 2,
        "minimum_rebound_pct": 8.0,
        "normal_add_amount": 10000,
        "enhanced_add_amount": 15000,
        "notify_on_wait": False,
        "notify_on_watch": True,
        "notify_on_review_buy": True,
    }}


def ind(**overrides):
    base=dict(daily_change_pct=0,five_day_change_pct=0,ten_day_change_pct=0,twenty_day_change_pct=0,
      one_month_drawdown_pct=-5,three_month_drawdown_pct=-10,six_month_drawdown_pct=-12,
      one_month_high_date='2026-07-01',three_month_high_date='2026-06-01',six_month_high_date='2026-03-01',
      drawdown_from_last_buy_pct=-2,consecutive_down_days=0,consecutive_up_days=0,consecutive_trend_pct=0,preceding_down_days=0,
      rebound_pct=0,is_new_5d_low=False,is_new_10d_low=False,stable_days=1,latest_price=1)
    base.update(overrides)
    return IndicatorResult(**base)


def test_wait_during_active_decline():
    assert evaluate_strategy(asset(), ind(consecutive_down_days=4,is_new_10d_low=True)).status=='WAIT'


def test_watch_early_rebound():
    d=evaluate_strategy(asset(), ind(consecutive_up_days=2,preceding_down_days=4,rebound_pct=8))
    assert d.status=='WATCH'


def test_review_after_reversal_confirmation():
    d=evaluate_strategy(asset(), ind(consecutive_up_days=3,preceding_down_days=4,rebound_pct=13))
    assert d.status=='REVIEW_BUY' and d.suggested_amount==10000


def test_enhanced_buy_after_stabilization():
    d=evaluate_strategy(asset(), ind(drawdown_from_last_buy_pct=-9,stable_days=6,is_new_10d_low=False))
    assert d.status=='REVIEW_BUY' and d.suggested_amount==15000
