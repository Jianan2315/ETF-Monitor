from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import time

import numpy as np
import pandas as pd

from src.models import MarketBundle, MarketSnapshot


class DataFetchError(RuntimeError):
    def __init__(self, message: str, attempts: list[str] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


def fetch_asset_data(
    asset: dict,
    config: dict,
    mode: str = "auto",
    project_root: Path | None = None,
) -> MarketBundle:
    if config.get("mock_mode", True):
        return _mock(asset, config)

    root = project_root or Path.cwd()
    return _ak(asset, config, mode=mode, project_root=root)


def _retry_call(operation, retry_cfg: dict, label: str):
    delays = retry_cfg.get("delays_seconds", [5, 15, 30])
    max_attempts = int(retry_cfg.get("max_attempts", 3))
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        try:
            return operation(), errors
        except Exception as exc:
            errors.append(
                f"{label} attempt {attempt}: {type(exc).__name__}: {exc}"
            )
            if attempt < max_attempts:
                delay = (
                    delays[min(attempt - 1, len(delays) - 1)]
                    if delays
                    else 5
                )
                time.sleep(float(delay))

    return None, errors


def _market_phase(now: datetime, mode: str) -> str:
    """
    Determine the real market phase from the current clock.

    `--mode intraday` does not force a realtime request outside market hours.
    `--mode close` always uses completed daily data.
    """
    if mode == "close":
        return "CLOSED"

    if now.weekday() >= 5:
        return "NON_TRADING_DAY"

    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 30:
        return "PRE_MARKET"
    if minutes <= 15 * 60:
        return "INTRADAY"
    return "CLOSED"


def _cache_path(project_root: Path, config: dict, symbol: str) -> Path:
    cache_dir = config.get("data", {}).get(
        "cache_directory",
        "data/cache",
    )
    return project_root / cache_dir / f"{symbol}_daily.json"


def _save_history_cache(
    project_root: Path,
    config: dict,
    symbol: str,
    history: pd.DataFrame,
) -> None:
    path = _cache_path(project_root, config, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)

    records = history.copy()
    records["date"] = records["date"].dt.strftime("%Y-%m-%d")
    payload = {
        "symbol": symbol,
        "cached_at": datetime.now().isoformat(timespec="seconds"),
        "rows": records.to_dict(orient="records"),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_history_cache(
    project_root: Path,
    config: dict,
    symbol: str,
) -> tuple[pd.DataFrame | None, str | None]:
    path = _cache_path(project_root, config, symbol)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("rows", [])
            if rows:
                frame = pd.DataFrame(rows)
                frame["date"] = pd.to_datetime(frame["date"])
                for column in [
                    "open",
                    "close",
                    "high",
                    "low",
                    "volume",
                    "amount",
                    "daily_change_pct",
                ]:
                    if column in frame:
                        frame[column] = pd.to_numeric(
                            frame[column],
                            errors="coerce",
                        )
                frame = (
                    frame.dropna(subset=["date", "close"])
                    .sort_values("date")
                    .reset_index(drop=True)
                )
                if not frame.empty:
                    return frame, payload.get("cached_at")
        except Exception:
            pass

    # V9.2 can also recover from the latest.json written by older versions.
    # This means an existing installation does not need one successful V9.2
    # network run before the cache fallback becomes useful.
    latest_path = (
        project_root
        / config.get("storage", {}).get(
            "latest_file",
            "data/latest.json",
        )
    )
    if latest_path.exists():
        try:
            payload = json.loads(
                latest_path.read_text(encoding="utf-8")
            )
            result = payload.get(str(symbol), {})
            chart_data = result.get("chart_data", [])
            if chart_data:
                frame = pd.DataFrame(chart_data).rename(
                    columns={"price": "close"}
                )
                frame["date"] = pd.to_datetime(frame["date"])
                frame["close"] = pd.to_numeric(
                    frame["close"],
                    errors="coerce",
                )
                frame = (
                    frame.dropna(subset=["date", "close"])
                    .sort_values("date")
                    .reset_index(drop=True)
                )
                if not frame.empty:
                    frame["open"] = frame["close"].shift(1).fillna(
                        frame["close"]
                    )
                    frame["high"] = frame[["open", "close"]].max(
                        axis=1
                    )
                    frame["low"] = frame[["open", "close"]].min(
                        axis=1
                    )
                    frame["volume"] = 0.0
                    frame["amount"] = 0.0
                    frame["daily_change_pct"] = (
                        frame["close"].pct_change().fillna(0) * 100
                    )
                    cached_at = result.get(
                        "generated_at",
                        "legacy latest.json",
                    )
                    return frame, cached_at
        except Exception:
            pass

    return None, None


def _normalize_history(df: pd.DataFrame, history_days: int) -> pd.DataFrame:
    renamed = df.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "daily_change_pct",
        }
    )
    if "date" not in renamed or "close" not in renamed:
        raise RuntimeError("Historical endpoint returned unexpected columns")

    renamed["date"] = pd.to_datetime(renamed["date"])
    for column in [
        "open",
        "close",
        "high",
        "low",
        "volume",
        "amount",
        "daily_change_pct",
    ]:
        if column in renamed:
            renamed[column] = pd.to_numeric(
                renamed[column],
                errors="coerce",
            )

    normalized = (
        renamed.dropna(subset=["date", "close"])
        .sort_values("date")
        .tail(history_days)
        .reset_index(drop=True)
    )
    if normalized.empty:
        raise RuntimeError("Historical endpoint returned no usable rows")
    return normalized


def _fetch_history(ak, asset: dict, config: dict, now: datetime) -> pd.DataFrame:
    data_cfg = config["data"]
    history_days = int(data_cfg.get("history_days", 1000))
    start = now - timedelta(days=max(history_days * 2, 240))

    frame = ak.fund_etf_hist_em(
        symbol=asset["symbol"],
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=now.strftime("%Y%m%d"),
        adjust=data_cfg.get("adjust", "qfq"),
    )
    if frame is None or frame.empty:
        raise RuntimeError("Historical ETF endpoint returned no data")
    return _normalize_history(frame, history_days)


def _fetch_spot(ak, asset: dict) -> dict:
    spot = ak.fund_etf_spot_em()
    if spot is None or spot.empty:
        raise RuntimeError("ETF spot endpoint returned no data")

    matches = spot.loc[
        spot["代码"].astype(str) == str(asset["symbol"])
    ]
    if matches.empty:
        raise RuntimeError(
            f"ETF {asset['symbol']} not found in spot endpoint"
        )

    row = matches.iloc[0]

    def number(key: str) -> float:
        value = pd.to_numeric(row.get(key), errors="coerce")
        if pd.isna(value):
            raise RuntimeError(f"Realtime field {key} is missing")
        return float(value)

    return {
        "timestamp": datetime.now(),
        "price": number("最新价"),
        "daily_change_pct": number("涨跌幅"),
        "open": number("今开"),
        "high": number("最高"),
        "low": number("最低"),
        "previous_close": number("昨收"),
        "volume": number("成交量"),
        "amount": number("成交额"),
    }


def _fetch_intraday(ak, asset: dict, now: datetime) -> pd.DataFrame:
    start = now.strftime("%Y-%m-%d") + " 09:30:00"
    end = now.strftime("%Y-%m-%d") + " 15:00:00"

    frame = ak.fund_etf_hist_min_em(
        symbol=asset["symbol"],
        start_date=start,
        end_date=end,
        period="1",
        adjust="",
    )
    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=["time", "price", "volume", "amount"]
        )

    frame = frame.rename(
        columns={
            "时间": "time",
            "收盘": "price",
            "成交量": "volume",
            "成交额": "amount",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
        }
    )
    if "time" not in frame or "price" not in frame:
        return pd.DataFrame(
            columns=["time", "price", "volume", "amount"]
        )

    frame["time"] = pd.to_datetime(frame["time"])
    for column in [
        "price",
        "volume",
        "amount",
        "open",
        "high",
        "low",
    ]:
        if column in frame:
            frame[column] = pd.to_numeric(
                frame[column],
                errors="coerce",
            )

    return (
        frame.dropna(subset=["time", "price"])
        .sort_values("time")
        .reset_index(drop=True)
    )


def _merge_spot_into_history(
    history: pd.DataFrame,
    spot: dict,
) -> pd.DataFrame:
    data = history.copy()
    today = pd.Timestamp(spot["timestamp"]).normalize()
    same_day = data["date"].dt.normalize() == today

    row = {
        "date": today,
        "open": spot["open"],
        "close": spot["price"],
        "high": spot["high"],
        "low": spot["low"],
        "volume": spot["volume"],
        "amount": spot["amount"],
        "daily_change_pct": spot["daily_change_pct"],
    }

    if same_day.any():
        index = data.index[same_day][-1]
        for key, value in row.items():
            data.loc[index, key] = value
    else:
        data = pd.concat(
            [data, pd.DataFrame([row])],
            ignore_index=True,
        )

    return data.sort_values("date").reset_index(drop=True)


def _build_latest(
    asset: dict,
    history: pd.DataFrame,
    spot: dict | None,
) -> MarketSnapshot:
    last = history.iloc[-1]

    if spot is not None:
        return MarketSnapshot(
            asset["symbol"],
            asset["name"],
            spot["timestamp"],
            spot["price"],
            spot["daily_change_pct"],
            spot["volume"],
            spot["amount"],
            None,
        )

    if (
        "daily_change_pct" in history
        and pd.notna(last.get("daily_change_pct"))
    ):
        daily_change = float(last["daily_change_pct"])
    elif len(history) >= 2:
        daily_change = float(
            (last["close"] / history.iloc[-2]["close"] - 1) * 100
        )
    else:
        daily_change = 0.0

    return MarketSnapshot(
        asset["symbol"],
        asset["name"],
        pd.Timestamp(last["date"]).to_pydatetime(),
        float(last["close"]),
        daily_change,
        float(last.get("volume", 0) or 0),
        float(last.get("amount", 0) or 0),
        None,
    )


def _ak(
    asset: dict,
    config: dict,
    mode: str,
    project_root: Path,
) -> MarketBundle:
    import akshare as ak

    now = datetime.now()
    phase = _market_phase(now, mode)
    retry_cfg = config.get("data", {}).get("retry", {})

    # Historical data and realtime data are intentionally handled separately.
    # A historical endpoint problem may fall back to the last successful local
    # cache. It must not automatically become a realtime DATA_ERROR.
    history, history_errors = _retry_call(
        lambda: _fetch_history(ak, asset, config, now),
        retry_cfg,
        "historical",
    )

    data_source = "AKShare / Eastmoney"
    cache_note = None

    if history is not None:
        _save_history_cache(
            project_root,
            config,
            asset["symbol"],
            history,
        )
    else:
        history, cached_at = _load_history_cache(
            project_root,
            config,
            asset["symbol"],
        )
        if history is None:
            raise DataFetchError(
                (
                    f"Historical data unavailable for {asset['symbol']} "
                    "and no local cache exists"
                ),
                attempts=history_errors,
            )
        data_source = "Local daily cache"
        cache_note = cached_at or "unknown"

    spot = None
    intraday = pd.DataFrame(
        columns=["time", "price", "volume", "amount"]
    )

    if phase == "INTRADAY":
        # During actual market hours, a fresh realtime snapshot is required.
        spot, spot_errors = _retry_call(
            lambda: _fetch_spot(ak, asset),
            retry_cfg,
            "realtime",
        )
        if spot is None:
            raise DataFetchError(
                (
                    f"Realtime data unavailable for {asset['symbol']} "
                    "during market hours"
                ),
                attempts=history_errors + spot_errors,
            )

        history = _merge_spot_into_history(history, spot)

        # Minute data is optional. A failure only leaves the chart empty.
        intraday, _ = _retry_call(
            lambda: _fetch_intraday(ak, asset, now),
            {
                "max_attempts": 1,
                "delays_seconds": [],
            },
            "intraday-chart",
        )
        if intraday is None:
            intraday = pd.DataFrame(
                columns=["time", "price", "volume", "amount"]
            )

    latest = _build_latest(asset, history, spot)

    if data_source == "Local daily cache":
        data_source = (
            f"Local daily cache (last saved {cache_note}; "
            f"AKShare unavailable)"
        )

    return MarketBundle(
        history=history,
        latest=latest,
        data_source=data_source,
        generated_at=now,
        intraday=intraday,
        data_phase=phase,
        market_timestamp=latest.timestamp,
    )


def _mock(asset: dict, config: dict) -> MarketBundle:
    n = config["data"].get("history_days", 120)
    rng = np.random.default_rng(20260711)
    dates = pd.bdate_range(
        end=pd.Timestamp.today().normalize(),
        periods=n,
    )
    noise = rng.normal(0.0008, 0.018, n)
    if n >= 10:
        noise[-10:] = [
            -.012,
            -.018,
            -.015,
            .004,
            -.020,
            -.011,
            -.024,
            -.016,
            -.029,
            -.018,
        ]

    close = [1.0]
    for value in noise[1:]:
        close.append(close[-1] * (1 + value))

    frame = pd.DataFrame({"date": dates, "close": close})
    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
    frame["high"] = frame[["open", "close"]].max(axis=1) * 1.006
    frame["low"] = frame[["open", "close"]].min(axis=1) * .994
    frame["volume"] = rng.integers(1_000_000, 9_000_000, n)
    frame["amount"] = frame["volume"] * frame["close"]
    frame["daily_change_pct"] = (
        frame["close"].pct_change().fillna(0) * 100
    )

    last = frame.iloc[-1]
    latest = MarketSnapshot(
        asset["symbol"],
        asset["name"],
        pd.Timestamp(last["date"]).to_pydatetime(),
        float(last["close"]),
        float(last["daily_change_pct"]),
        float(last["volume"]),
        float(last["amount"]),
        -35.77,
    )

    return MarketBundle(
        frame,
        latest,
        "Mock data",
        datetime.now(),
        intraday=pd.DataFrame(
            columns=["time", "price", "volume", "amount"]
        ),
        data_phase="CLOSED",
        market_timestamp=latest.timestamp,
    )
