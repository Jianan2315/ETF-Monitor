from datetime import datetime,timedelta
import numpy as np
import pandas as pd
from src.models import MarketBundle,MarketSnapshot

def fetch_asset_data(asset,config):
 return _mock(asset,config) if config.get('mock_mode',True) else _ak(asset,config)

def _ak(asset,config):
 import akshare as ak
 end=datetime.now(); start=end-timedelta(days=max(config['data'].get('history_days',120)*2,240))
 df=ak.fund_etf_hist_em(symbol=asset['symbol'],period='daily',start_date=start.strftime('%Y%m%d'),end_date=end.strftime('%Y%m%d'),adjust='qfq')
 if df is None or df.empty: raise RuntimeError('未获取到行情')
 df=df.rename(columns={'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume','成交额':'amount','涨跌幅':'daily_change_pct'})
 df['date']=pd.to_datetime(df['date'])
 for c in ['open','close','high','low','volume','amount','daily_change_pct']:
  if c in df: df[c]=pd.to_numeric(df[c],errors='coerce')
 df=df.dropna(subset=['date','close']).sort_values('date').tail(config['data'].get('history_days',120)).reset_index(drop=True)
 r=df.iloc[-1]; ch=float(r['daily_change_pct']) if 'daily_change_pct' in df and pd.notna(r['daily_change_pct']) else float((r['close']/df.iloc[-2]['close']-1)*100)
 latest=MarketSnapshot(asset['symbol'],asset['name'],pd.Timestamp(r['date']).to_pydatetime(),float(r['close']),ch,float(r['volume']) if 'volume' in r else None,float(r['amount']) if 'amount' in r else None,None)
 return MarketBundle(df,latest,'AKShare / Eastmoney adapter',datetime.now())

def _mock(asset,config):
 n=config['data'].get('history_days',120); rng=np.random.default_rng(20260711); dates=pd.bdate_range(end=pd.Timestamp.today().normalize(),periods=n)
 noise=rng.normal(.0008,.018,n)
 if n>=10: noise[-10:]=[-.012,-.018,-.015,.004,-.020,-.011,-.024,-.016,-.029,-.018]
 close=[1.0]
 for x in noise[1:]: close.append(close[-1]*(1+x))
 df=pd.DataFrame({'date':dates,'close':close}); df['open']=df['close'].shift(1).fillna(df['close']); df['high']=df[['open','close']].max(axis=1)*1.006; df['low']=df[['open','close']].min(axis=1)*.994; df['volume']=rng.integers(1_000_000,9_000_000,n); df['amount']=df['volume']*df['close']; df['daily_change_pct']=df['close'].pct_change().fillna(0)*100
 r=df.iloc[-1]; latest=MarketSnapshot(asset['symbol'],asset['name'],pd.Timestamp(r['date']).to_pydatetime(),float(r['close']),float(r['daily_change_pct']),float(r['volume']),float(r['amount']),-35.77)
 return MarketBundle(df,latest,'Mock data',datetime.now())
