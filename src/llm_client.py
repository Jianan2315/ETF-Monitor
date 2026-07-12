import os,requests

def generate_summary(r,c):
 if c.get('provider','deepseek')!='deepseek': raise ValueError('目前只内置 DeepSeek')
 key=os.getenv('DEEPSEEK_API_KEY')
 if not key: raise RuntimeError('未配置 DEEPSEEK_API_KEY')
 i=r['indicators']; d=r['decision']; prompt=f"""请根据以下结构化结果生成不超过{c.get('max_summary_chars',220)}字的中文摘要。不得修改decision，不得给出确定性买卖指令。基金：{r['asset']['name']}；今日{i['daily_change_pct']:.2f}%；近5日{i['five_day_change_pct']:.2f}%；近10日{i['ten_day_change_pct']:.2f}%；近6个月高点回撤{i['six_month_drawdown_pct']:.2f}%；相对上次买入{i['drawdown_from_last_buy_pct']:.2f}%；连续下跌{i['consecutive_down_days']}日；10日新低{i['is_new_10d_low']}；decision={d['status']}；原因：{'；'.join(d['reasons'])}"""
 resp=requests.post('https://api.deepseek.com/chat/completions',headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json={'model':c.get('model','deepseek-chat'),'messages':[{'role':'system','content':'你只负责解释Python的确定性判断，不得修改结论。'},{'role':'user','content':prompt}],'temperature':.2},timeout=30)
 resp.raise_for_status(); return resp.json()['choices'][0]['message']['content'].strip()
