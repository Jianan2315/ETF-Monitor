from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def safe_asset_id(value: str) -> str:
    value = str(value or '').strip()
    value = re.sub(r'[^0-9A-Za-z._-]+', '-', value).strip('-._')
    if not value:
        raise ValueError('资产ID不能为空')
    return value


def ensure_layout(root: Path) -> None:
    for path in [
        root / 'config' / 'global',
        root / 'config' / 'assets',
        root / 'config' / 'private',
        root / 'data' / 'assets',
        root / 'data' / 'global' / 'run_status',
        root / 'data' / 'global' / 'notifications',
        root / 'data' / 'global' / 'scheduled_tasks',
        root / 'data' / 'global' / 'schedule_status',
        root / 'output' / 'portfolio' / 'latest',
        root / 'output' / 'portfolio' / 'history',
        root / 'output' / 'assets',
        root / 'logs' / 'global',
        root / 'logs' / 'assets',
    ]:
        path.mkdir(parents=True, exist_ok=True)


def asset_config_dir(root: Path) -> Path:
    ensure_layout(root)
    return root / 'config' / 'assets'


def load_assets(root: Path) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for path in sorted(asset_config_dir(root).glob('*.json')):
        if path.name.endswith('.strategy.json'):
            continue
        doc = _read_json(path, {})
        asset = doc.get('asset', doc)
        if not isinstance(asset, dict):
            continue
        item = dict(asset)
        item.setdefault('id', path.stem)
        item.setdefault('asset_code', item.get('id', path.stem))
        item.setdefault('tracking_code', item.get('symbol', ''))
        item['symbol'] = str(item.get('tracking_code') or item.get('symbol') or '')
        strategy_path = asset_config_dir(root) / f'{path.stem}.strategy.json'
        strategy_doc = _read_json(strategy_path, {})
        item['strategy'] = dict(strategy_doc.get('strategy', item.get('strategy', {})))
        assets.append(item)
    return assets


def save_asset(root: Path, asset: dict[str, Any], *, old_id: str | None = None) -> dict[str, Any]:
    item = dict(asset)
    asset_id = safe_asset_id(item.get('id') or item.get('asset_code'))
    item['id'] = asset_id
    item['asset_code'] = str(item.get('asset_code') or asset_id).strip()
    item['tracking_code'] = str(item.get('tracking_code') or item.get('symbol') or '').strip()
    item['symbol'] = item['tracking_code']
    strategy = dict(item.pop('strategy', {}))
    path = asset_config_dir(root) / f'{asset_id}.json'
    _atomic_write(path, {'asset': item})
    if strategy:
        _atomic_write(asset_config_dir(root) / f'{asset_id}.strategy.json', {
            'asset_id': asset_id,
            'strategy': strategy,
        })
    if old_id and safe_asset_id(old_id) != asset_id:
        old_path = asset_config_dir(root) / f'{safe_asset_id(old_id)}.json'
        old_path.unlink(missing_ok=True)
        old_strategy_path = asset_config_dir(root) / f'{safe_asset_id(old_id)}.strategy.json'
        new_strategy_path = asset_config_dir(root) / f'{asset_id}.strategy.json'
        if old_strategy_path.exists() and not new_strategy_path.exists():
            old_strategy_path.rename(new_strategy_path)
        old_data = root / 'data' / 'assets' / safe_asset_id(old_id)
        new_data = root / 'data' / 'assets' / asset_id
        if old_data.exists() and not new_data.exists():
            old_data.rename(new_data)
        old_output = root / 'output' / 'assets' / safe_asset_id(old_id)
        new_output = root / 'output' / 'assets' / asset_id
        if old_output.exists() and not new_output.exists():
            old_output.rename(new_output)
        old_logs = root / 'logs' / 'assets' / safe_asset_id(old_id)
        new_logs = root / 'logs' / 'assets' / asset_id
        if old_logs.exists() and not new_logs.exists():
            old_logs.rename(new_logs)
    ensure_asset_runtime(root, asset_id)
    return {**item, 'strategy': strategy}


def delete_asset(root: Path, asset_id: str, *, purge_runtime: bool = True) -> None:
    key = safe_asset_id(asset_id)
    (asset_config_dir(root) / f'{key}.json').unlink(missing_ok=True)
    (asset_config_dir(root) / f'{key}.strategy.json').unlink(missing_ok=True)
    if purge_runtime:
        shutil.rmtree(root / 'data' / 'assets' / key, ignore_errors=True)
        shutil.rmtree(root / 'output' / 'assets' / key, ignore_errors=True)
        shutil.rmtree(root / 'logs' / 'assets' / key, ignore_errors=True)


def ensure_asset_runtime(root: Path, asset_id: str) -> None:
    key = safe_asset_id(asset_id)
    for path in [
        root / 'data' / 'assets' / key / 'cache',
        root / 'data' / 'assets' / key / 'diagnostics',
        root / 'data' / 'assets' / key / 'notifications',
        root / 'data' / 'assets' / key / 'run_status',
        root / 'output' / 'assets' / key / 'latest',
        root / 'output' / 'assets' / key / 'history',
        root / 'logs' / 'assets' / key,
    ]:
        path.mkdir(parents=True, exist_ok=True)
    tx = root / 'data' / 'assets' / key / 'transactions.json'
    if not tx.exists():
        _atomic_write(tx, {})


def asset_data_dir(root: Path, asset_id: str) -> Path:
    ensure_asset_runtime(root, asset_id)
    return root / 'data' / 'assets' / safe_asset_id(asset_id)


def asset_output_dir(root: Path, asset_id: str) -> Path:
    ensure_asset_runtime(root, asset_id)
    return root / 'output' / 'assets' / safe_asset_id(asset_id)


def asset_transactions_path(root: Path, asset_id: str) -> Path:
    return asset_data_dir(root, asset_id) / 'transactions.json'


def asset_cache_dir(root: Path, asset_id: str) -> Path:
    return asset_data_dir(root, asset_id) / 'cache'


def asset_latest_path(root: Path, asset_id: str) -> Path:
    return asset_data_dir(root, asset_id) / 'latest.json'


def asset_report_path(root: Path, asset_id: str) -> Path:
    return asset_output_dir(root, asset_id) / 'latest' / 'report.html'


def asset_history_dir(root: Path, asset_id: str) -> Path:
    return asset_output_dir(root, asset_id) / 'history'


def asset_history_index_path(root: Path, asset_id: str) -> Path:
    return asset_output_dir(root, asset_id) / 'history.html'
