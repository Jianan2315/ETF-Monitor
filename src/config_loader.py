from __future__ import annotations
import json
from pathlib import Path
from dotenv import load_dotenv

REQUIRED_SECTIONS = ['assets','data','llm','email','storage','report']


def load_config(path: Path) -> dict:
    """Load split config directory, with legacy single-file fallback."""
    base = path if path.is_dir() else path.parent
    load_dotenv(base.parent / '.env' if path.is_dir() else path.parent / '.env')

    if path.is_dir():
        merged: dict = {}
        files = ['app.json','assets.json','email.json','llm.json','storage.json']
        for name in files:
            file_path = path / name
            if not file_path.exists():
                raise FileNotFoundError(f'配置文件不存在: {file_path}')
            with file_path.open('r',encoding='utf-8') as f:
                part=json.load(f)
            overlap=set(merged).intersection(part)
            if overlap:
                raise ValueError(f'配置一级字段重复: {sorted(overlap)}')
            merged.update(part)
        config=merged
    else:
        with path.open('r',encoding='utf-8') as f:
            config=json.load(f)

    missing=[k for k in REQUIRED_SECTIONS if k not in config]
    if missing:
        raise ValueError(f'配置缺少字段: {missing}')
    validate_email_config(config['email'])
    return config


def validate_email_config(email: dict) -> None:
    if not isinstance(email.get('enabled',False),bool):
        raise ValueError('email.enabled 必须是布尔值')
    triggers=email.get('triggers',{})
    if not isinstance(triggers,dict):
        raise ValueError('email.triggers 必须是对象')
