import json
from pathlib import Path
from dotenv import load_dotenv

def load_config(path:Path)->dict:
 load_dotenv(path.parent/'.env')
 with path.open('r',encoding='utf-8') as f: c=json.load(f)
 for k in ['assets','data','llm','email','storage','report']:
  if k not in c: raise ValueError(f'配置缺少字段: {k}')
 return c
