from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time as clock_time
from zoneinfo import ZoneInfo


SESSION_PHASES = {"AM_SESSION", "MIDDAY_BREAK", "PM_SESSION"}

PHASE_LABELS = {
    "PRE_OPEN": "开盘前",
    "AM_SESSION": "上午交易时段",
    "MIDDAY_BREAK": "午间休市",
    "PM_SESSION": "下午交易时段",
    "AFTER_CLOSE": "收盘后",
    "WEEKEND": "周末休市",
}


@dataclass(frozen=True)
class MarketContext:
    now: datetime
    phase: str
    phase_label: str
    close_ready: bool
    can_intraday: bool
    can_close: bool

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["now"] = self.now.strftime("%Y-%m-%d %H:%M:%S")
        return payload


@dataclass(frozen=True)
class ModeValidation:
    allowed: bool
    message: str
    context: MarketContext


def market_now(config: dict | None = None) -> datetime:
    timezone_name = "Asia/Shanghai"
    if config:
        timezone_name = config.get("schedule", {}).get("timezone") or timezone_name
    aware = datetime.now(ZoneInfo(timezone_name))
    # The rest of this project uses naive datetimes. Keep the market-local wall
    # clock while removing tzinfo to avoid mixing aware and naive pandas values.
    return aware.replace(tzinfo=None)


def classify_market_phase(now: datetime) -> str:
    if now.weekday() >= 5:
        return "WEEKEND"

    current = now.time()
    if current < clock_time(9, 30):
        return "PRE_OPEN"
    if current < clock_time(11, 30):
        return "AM_SESSION"
    if current < clock_time(13, 0):
        return "MIDDAY_BREAK"
    if current < clock_time(15, 0):
        return "PM_SESSION"
    return "AFTER_CLOSE"


def build_market_context(now: datetime) -> MarketContext:
    phase = classify_market_phase(now)
    close_ready = (
        phase == "AFTER_CLOSE"
        and now.weekday() < 5
        and now.time() >= clock_time(15, 5)
    )
    return MarketContext(
        now=now,
        phase=phase,
        phase_label=PHASE_LABELS[phase],
        close_ready=close_ready,
        can_intraday=phase in SESSION_PHASES,
        can_close=close_ready,
    )


def validate_run_mode(mode: str, now: datetime) -> ModeValidation:
    context = build_market_context(now)
    normalized = mode.lower().strip()

    if normalized in {"auto", "manual"}:
        return ModeValidation(True, "已按当前市场阶段运行。", context)

    if normalized == "intraday":
        if context.can_intraday:
            return ModeValidation(True, "可以生成盘中报告。", context)
        if context.phase == "PRE_OPEN":
            message = "市场尚未开盘。盘中报告仅可在09:30—15:00之间生成。"
        elif context.phase == "AFTER_CLOSE":
            message = "市场已经收盘，请使用“生成收盘报告”。"
        else:
            message = "当前为周末休市，不能生成盘中报告。"
        return ModeValidation(False, message, context)

    if normalized == "close":
        if context.can_close:
            return ModeValidation(True, "可以生成收盘报告。", context)
        if context.phase == "WEEKEND":
            message = "当前为周末休市，不能生成当日收盘报告。"
        else:
            message = (
                f"当前时间为{now:%H:%M}，市场收盘数据尚未就绪。"
                "收盘报告最早可在15:05后生成。"
            )
        return ModeValidation(False, message, context)

    return ModeValidation(False, f"不支持的运行模式：{mode}", context)


def should_request_realtime(mode: str, context: MarketContext) -> bool:
    normalized = mode.lower().strip()
    if normalized == "intraday":
        return True
    if normalized in {"auto", "manual"}:
        if context.phase in SESSION_PHASES:
            return True
        # 15:00—15:05 之间，日线接口可能还没更新。AUTO 使用最终实时
        # 快照更可靠，但仍明确标记为 AFTER_CLOSE，而不是正式收盘确认。
        if context.phase == "AFTER_CLOSE" and not context.close_ready:
            return True
    return False


def expected_history_date(now: datetime, context: MarketContext):
    import pandas as pd

    today = pd.Timestamp(now.date())
    if context.phase in {
        "PRE_OPEN",
        "AM_SESSION",
        "MIDDAY_BREAK",
        "PM_SESSION",
        "WEEKEND",
    }:
        return today - pd.tseries.offsets.BDay(1)
    if context.phase == "AFTER_CLOSE" and not context.close_ready:
        return today - pd.tseries.offsets.BDay(1)
    return today
