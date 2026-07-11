from src.models import IndicatorResult
from src.strategy import evaluate_strategy

def asset(): return {'strategy':{'drop_from_last_buy_trigger_pct':8,'stable_days_required':5,'consecutive_down_warning_days':3,'max_single_add_amount':5000,'notify_on_wait':False,'notify_on_watch':True,'notify_on_review_buy':True}}
def test_wait():
 i=IndicatorResult(-2,-8,-12,-15,-18,-10,4,True,True,1,1.2,.98)
 assert evaluate_strategy(asset(),i).status=='WAIT'
def test_review():
 i=IndicatorResult(.3,-1,-4,-10,-13,-9,0,False,False,6,1.2,.91)
 d=evaluate_strategy(asset(),i); assert d.status=='REVIEW_BUY' and d.suggested_amount==5000
