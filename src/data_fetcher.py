from __future__ import annotations

from datetime import datetime, timedelta
import time

import numpy as np
import pandas as pd

from src.models import MarketBundle, MarketSnapshot


class DataFetchError(RuntimeError):
    def __init__(self, message: str, attempts: list[str] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


def fetch_asset_data(asset: dict, config: dict, mode: str = "auto") -> MarketBundle:
    if config.get("mock_mode", True):
        return _mock(asset, config)

    data_cfg = config.get("data", {})
    retry_cfg = data_cfg.get("retry", {})
    delays = retry_cfg.get("delays_seconds", [5, 15, 30])
    max_attempts = int(retry_cfg.get("max_attempts", 3))
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        try:
            return _ak(asset, config, mode=mode)
        except Exception as exc:
            errors.append(f"Attempt {attempt}: {type(exc).__name__}: {exc}")
            if attempt < max_attempts:
                delay = delays[min(attempt - 1, len(delays) - 1)] if delays else 5
                time.sleep(float(delay))

    raise DataFetchError(
        f"Failed to fetch market data for {asset.get('symbol')} after {max_attempts} attempts",
        attempts=errors,
    )


def _market_phase(now: datetime, mode: str) -> str:
    if mode == "close":
        return "CLOSED"
    if mode == "intraday":
        return "INTRADAY"
    if now.weekday() >= 5:
        return "NON_TRADING_DAY"
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60 + 30:
        return "PRE_MARKET"
    if hm <= 15 * 60:
        return "INTRADAY"
    return "CLOSED"


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
    renamed["date"] = pd.to_datetime(renamed["date"])
    for column in [
        "open", "close", "high", "low", "volume", "amount", "daily_change_pct"
    ]:
        if column in renamed:
            renamed[column] = pd.to_numeric(renamed[column], errors="coerce")
    return (
        renamed.dropna(subset=["date", "close"])
        .sort_values("date")
        .tail(history_days)
        .reset_index(drop=True)
    )


def _fetch_spot(ak, asset: dict) -> dict:
    spot = ak.fund_etf_spot_em()
    if spot is None or spot.empty:
        raise RuntimeError("ETF spot endpoint returned no data")
    matches = spot.loc[spot["代码"].astype(str) == str(asset["symbol"])]
    if matches.empty:
        raise RuntimeError(f"ETF {asset['symbol']} not found in spot endpoint")
    row = matches.iloc[0]
    return {
        "timestamp": datetime.now(),
        "price": float(pd.to_numeric(row.get("最新价"), errors="coerce")),
        "daily_change_pct": float(pd.to_numeric(row.get("涨跌幅"), errors="coerce")),
        "open": float(pd.to_numeric(row.get("今开"), errors="coerce")),
        "high": float(pd.to_numeric(row.get("最高"), errors="coerce")),
        "low": float(pd.to_numeric(row.get("最低"), errors="coerce")),
        "previous_close": float(pd.to_numeric(row.get("昨收"), errors="coerce")),
        "volume": float(pd.to_numeric(row.get("成交量"), errors="coerce")),
        "amount": float(pd.to_numeric(row.get("成交额"), errors="coerce")),
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
        return pd.DataFrame(columns=["time", "price", "volume", "amount"])
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
    frame["time"] = pd.to_datetime(frame["time"])
    for col in ["price", "volume", "amount", "open", "high", "low"]:
        if col in frame:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["time", "price"]).sort_values("time").reset_index(drop=True)


def _merge_spot_into_history(history: pd.DataFrame, spot: dict, phase: str) -> pd.DataFrame:
    if phase != "INTRADAY":
        return history
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
        data = pd.concat([data, pd.DataFrame([row])], ignore_index=True)
    return data.sort_values("date").reset_index(drop=True)


def _ak(asset: dict, config: dict, mode: str = "auto") -> MarketBundle:
    import akshare as ak

    now = datetime.now()
    phase = _market_phase(now, mode)
    data_cfg = config["data"]
    history_days = int(data_cfg.get("history_days", 1000))
    end = now
    start = end - timedelta(days=max(history_days * 2, 240))

    hist = ak.fund_etf_hist_em(
        symbol=asset["symbol"],
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust=data_cfg.get("adjust", "qfq"),
    )
    if hist is None or hist.empty:
        raise RuntimeError("Historical ETF endpoint returned no data")
    history = _normalize_history(hist, history_days)

    intraday = pd.DataFrame(columns=["time", "price", "volume", "amount"])
    spot = None
    if phase == "INTRADAY":
        spot = _fetch_spot(ak, asset)
        intraday = _fetch_intraday(ak, asset, now)
        history = _merge_spot_into_history(history, spot, phase)

    last = history.iloc[-1]
    daily_change = (
        float(spot["daily_change_pct"])
        if spot is not None
        else float(last["daily_change_pct"])
        if "daily_change_pct" in history and pd.notna(last["daily_change_pct"])
        else float((last["close"] / history.iloc[-2]["close"] - 1) * 100)
    )
    timestamp = spot["timestamp"] if spot is not None else pd.Timestamp(last["date"]).to_pydatetime()
    latest = MarketSnapshot(
        asset["symbol"],
        asset["name"],
        timestamp,
        float(spot["price"] if spot is not None else last["close"]),
        daily_change,
        float(spot["volume"] if spot is not None else last.get("volume", 0) or 0),
        float(spot["amount"] if spot is not None else last.get("amount", 0) or 0),
        None,
    )
    return MarketBundle(
        history=history,
        latest=latest,
        data_source="AKShare / Eastmoney",
        generated_at=now,
        intraday=intraday,
        data_phase=phase,
        market_timestamp=timestamp,
    )


def _mock(asset: dict, config: dict) -> MarketBundle:
    n = config["data"].get("history_days", 120)
    rng = np.random.default_rng(20260711)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    noise = rng.normal(0.0008, 0.018, n)
    if n >= 10:
        noise[-10:] = [-.012, -.018, -.015, .004, -.020, -.011, -.024, -.016, -.029, -.018]
    close = [1.0]
    for value in noise[1:]:
        close.append(close[-1] * (1 + value))
    df = pd.DataFrame({"date": dates, "close": close})
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df[["open", "close"]].max(axis=1) * 1.006
    df["low"] = df[["open", "close"]].min(axis=1) * .994
    df["volume"] = rng.integers(1_000_000, 9_000_000, n)
    df["amount"] = df["volume"] * df["close"]
    df["daily_change_pct"] = df["close"].pct_change().fillna(0) * 100
    last = df.iloc[-1]
    latest = MarketSnapshot(
        asset["symbol"], asset["name"], pd.Timestamp(last["date"]).to_pydatetime(),
        float(last["close"]), float(last["daily_change_pct"]),
        float(last["volume"]), float(last["amount"]), -35.77,
    )
    return MarketBundle(
        df, latest, "Mock data", datetime.now(),
        intraday=pd.DataFrame(columns=["time", "price", "volume", "amount"]),
        data_phase="CLOSED",
        market_timestamp=latest.timestamp,
    )
