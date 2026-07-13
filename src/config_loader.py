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
    if not isinstance(email.get("enabled", False), bool):
        raise ValueError("email.enabled 必须是布尔值")

    provider = str(email.get("provider", "microsoft_graph")).lower()
    if provider not in {"microsoft_graph", "outlook_graph", "graph"}:
        raise ValueError("email.provider 目前只支持 microsoft_graph")

    mode = email.get("send_mode", "trigger")
    if mode not in {"trigger", "always"}:
        raise ValueError("email.send_mode 只能是 trigger 或 always")

    triggers = email.get("triggers", {})
    if not isinstance(triggers, dict):
        raise ValueError("email.triggers 必须是对象")

    debug = email.get("debug", {})
    if not isinstance(debug, dict):
        raise ValueError("email.debug 必须是对象")

    for key in ("enabled", "force_send_email", "verbose_logging"):
        if key in debug and not isinstance(debug[key], bool):
            raise ValueError(f"email.debug.{key} 必须是布尔值")

    graph = email.get("graph", {})
    if not isinstance(graph, dict):
        raise ValueError("email.graph 必须是对象")

    scopes = graph.get("scopes", ["Mail.Send"])
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("email.graph.scopes 必须是非空数组")
    if not all(isinstance(scope, str) and scope.strip() for scope in scopes):
        raise ValueError("email.graph.scopes 中的每一项都必须是非空字符串")

    for key in ("authority", "endpoint", "token_cache_file"):
        value = graph.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"email.graph.{key} 必须是非空字符串")

    timeout = graph.get("timeout_seconds", 30)
    if not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("email.graph.timeout_seconds 必须是正整数")
