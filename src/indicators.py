import pandas as pd
from src.models import IndicatorResult

def period_return(c,d): return float((c.iloc[-1]/c.iloc[-d-1]-1)*100)
def consecutive_down(c):
 n=0
 for x in reversed(c.diff().iloc[1:].tolist()):
  if x<0:n+=1
  else:break
 return n
def new_low(c,w):
 r=c.tail(w); return bool(r.iloc[-1]<=r.min())
def stable_days(c,range_pct,max_days=20):
 best=1
 for d in range(2,min(max_days,len(c))+1):
  r=c.tail(d); p=(r.max()/r.min()-1)*100
  if p<=range_pct: best=d
  else: break
 return best

def calculate_indicators(asset,history,latest):
 c=pd.to_numeric(history['close'],errors='coerce').dropna()
 if len(c)<21: raise ValueError('至少需要21个交易日数据')
 s=asset['strategy']; high=float(c.tail(max(20,s.get('new_low_window_days',10))).max()); last=float(asset['position']['last_buy_price'])
 return IndicatorResult(float(latest.daily_change_pct),period_return(c,5),period_return(c,10),period_return(c,20),(latest.price/high-1)*100,(latest.price/last-1)*100,consecutive_down(c),new_low(c,5),new_low(c,10),stable_days(c,float(s.get('stable_range_pct',3))),high,float(latest.price))
