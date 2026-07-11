from src.models import StrategyDecision

def evaluate_strategy(asset,i):
 s=asset['strategy']; reasons=[]; warning=int(s.get('consecutive_down_warning_days',3)); trigger=float(s.get('drop_from_last_buy_trigger_pct',8)); stable=int(s.get('stable_days_required',5))
 if i.consecutive_down_days>=warning: reasons.append(f'已经连续下跌 {i.consecutive_down_days} 个交易日')
 if i.is_new_10d_low: reasons.append('最新价格仍处于近10个交易日低位')
 if i.drawdown_from_last_buy_pct<=-trigger: reasons.append(f'相对上一笔买入价回撤达到 {abs(i.drawdown_from_last_buy_pct):.2f}%')
 if i.stable_days>=stable: reasons.append(f'最近 {i.stable_days} 个交易日波动区间较窄')
 if i.is_new_10d_low and i.consecutive_down_days>=warning:
  return StrategyDecision('WAIT',bool(s.get('notify_on_wait',False)),0,reasons or ['短期下跌尚未稳定'])
 if i.drawdown_from_last_buy_pct<=-trigger and i.stable_days>=stable and not i.is_new_10d_low:
  return StrategyDecision('REVIEW_BUY',bool(s.get('notify_on_review_buy',True)),float(s.get('max_single_add_amount',5000)),reasons)
 return StrategyDecision('WATCH',bool(s.get('notify_on_watch',True)),0,reasons or ['暂未触发明确条件'])
