from dataclasses import dataclass,asdict
from datetime import datetime
import pandas as pd

@dataclass
class MarketSnapshot:
 symbol:str; name:str; timestamp:datetime; price:float; daily_change_pct:float
 volume:float|None=None; amount:float|None=None; main_fund_flow:float|None=None
 def to_dict(self):
  d=asdict(self); d['timestamp']=self.timestamp.isoformat(); return d

@dataclass
class MarketBundle:
 history:pd.DataFrame; latest:MarketSnapshot; data_source:str; generated_at:datetime

@dataclass
class IndicatorResult:
 daily_change_pct:float; five_day_change_pct:float; ten_day_change_pct:float; twenty_day_change_pct:float
 drawdown_from_recent_high_pct:float; drawdown_from_last_buy_pct:float; consecutive_down_days:int
 is_new_5d_low:bool; is_new_10d_low:bool; stable_days:int; recent_high_price:float; latest_price:float
 def to_dict(self): return asdict(self)

@dataclass
class StrategyDecision:
 status:str; should_notify:bool; suggested_amount:float; reasons:list[str]
 def to_dict(self): return asdict(self)
