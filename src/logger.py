import logging
from pathlib import Path

def configure_logging(path:Path):
 path.parent.mkdir(parents=True,exist_ok=True)
 logging.basicConfig(level=logging.INFO,format='%(asctime)s | %(levelname)s | %(message)s',handlers=[logging.FileHandler(path,encoding='utf-8'),logging.StreamHandler()],force=True)
 return logging.getLogger('etf_monitor')
