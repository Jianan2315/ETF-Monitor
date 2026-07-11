import json
from pathlib import Path
import pandas as pd

def append_xlsx(path,row,sheet):
 new=pd.DataFrame([row])
 if path.exists():
  try:new=pd.concat([pd.read_excel(path,sheet_name=sheet),new],ignore_index=True)
  except Exception:pass
 with pd.ExcelWriter(path,engine='openpyxl',mode='w') as x:new.to_excel(x,index=False,sheet_name=sheet)

def save_run_result(r,c,root:Path):
 hp=root/c['storage']['history_file']; dp=root/c['storage']['decisions_file']; lp=root/c['storage']['latest_file']; hp.parent.mkdir(parents=True,exist_ok=True)
 i=r['indicators']; a=r['asset']; d=r['decision']
 append_xlsx(hp,{'generated_at':r['generated_at'],'symbol':a['symbol'],'name':a['name'],'price':i['latest_price'],'daily_change_pct':i['daily_change_pct'],'five_day_change_pct':i['five_day_change_pct'],'ten_day_change_pct':i['ten_day_change_pct'],'twenty_day_change_pct':i['twenty_day_change_pct'],'drawdown_from_recent_high_pct':i['drawdown_from_recent_high_pct'],'drawdown_from_last_buy_pct':i['drawdown_from_last_buy_pct'],'consecutive_down_days':i['consecutive_down_days'],'is_new_10d_low':i['is_new_10d_low'],'stable_days':i['stable_days'],'data_source':r['data_source']},'history')
 append_xlsx(dp,{'generated_at':r['generated_at'],'symbol':a['symbol'],'name':a['name'],'status':d['status'],'suggested_amount':d['suggested_amount'],'reasons':'；'.join(d['reasons']),'should_notify':d['should_notify']},'decisions')
 try:latest=json.loads(lp.read_text(encoding='utf-8')) if lp.exists() else {}
 except Exception:latest={}
 latest[a['symbol']]=r; lp.write_text(json.dumps(latest,ensure_ascii=False,indent=2),encoding='utf-8')
