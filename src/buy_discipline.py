"""Non-blocking buy-discipline facts derived from an asset's trade ledger."""
from __future__ import annotations

from typing import Any


_PENDING_STATUSES = {"PENDING", "PENDING_CLOSE"}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _known_buys(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            item for item in transactions
            if str(item.get("type", "")).upper() == "BUY"
            and str(item.get("status", "")).upper() not in _PENDING_STATUSES
            and _number(item.get("price")) > 0
        ],
        key=lambda item: (str(item.get("date", "")), str(item.get("created_at", ""))),
    )


def summarize_buy_discipline(
    strategy: dict[str, Any],
    transactions: list[dict[str, Any]],
    cycle: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return advisory-only same-band and plan-reference facts.

    A plan reference is intentionally not a position limit: every value here is
    informative, and a sale never restores the reference amount automatically.
    """
    cycle = cycle or {}
    band_pct = max(_number(strategy.get("same_price_band_pct")) or 3.0, 0.0)
    max_buys = max(int(_number(strategy.get("same_price_band_max_buys")) or 5), 1)
    plan_reference = _number(strategy.get("planned_investment_reference"))
    known_buys = _known_buys(transactions)
    pending_buys = [
        item for item in transactions
        if str(item.get("type", "")).upper() == "BUY"
        and str(item.get("status", "")).upper() in _PENDING_STATUSES
    ]
    latest_buy = known_buys[-1] if known_buys else None
    reference_price = _number((latest_buy or {}).get("price"))
    cycle_start = str((cycle.get("last_confirmed_low") or {}).get("date") or "")
    buys_in_cycle = [
        item for item in known_buys
        if not cycle_start or str(item.get("date", "")) >= cycle_start
    ]
    same_band = [
        item for item in buys_in_cycle
        if reference_price > 0
        and abs(_number(item.get("price")) / reference_price - 1) <= band_pct / 100
    ]
    cumulative_buys = sum(_number(item.get("amount")) for item in transactions if str(item.get("type", "")).upper() == "BUY")
    pending_amount = sum(_number(item.get("amount")) for item in pending_buys)
    known_buy_amount = cumulative_buys - pending_amount
    planned_used = cumulative_buys
    return {
        "same_price_band_pct": round(band_pct, 4),
        "same_price_band_max_buys": max_buys,
        "reference_buy_price": round(reference_price, 8) if reference_price > 0 else None,
        "reference_buy_date": str((latest_buy or {}).get("date") or ""),
        "cycle_start_date": cycle_start,
        "same_price_band_buy_count": len(same_band),
        "same_price_band_attention": bool(reference_price > 0 and len(same_band) >= max_buys),
        "unpriced_pending_buy_count": len(pending_buys),
        "pending_buy_amount": round(pending_amount, 2),
        "known_cumulative_buy_amount": round(known_buy_amount, 2),
        "cumulative_buy_amount": round(cumulative_buys, 2),
        "planned_investment_reference": round(plan_reference, 2) if plan_reference > 0 else None,
        "planned_reference_used_amount": round(planned_used, 2) if plan_reference > 0 else None,
        "planned_reference_remaining_amount": round(plan_reference - planned_used, 2) if plan_reference > 0 else None,
        "planned_reference_over_amount": round(max(planned_used - plan_reference, 0), 2) if plan_reference > 0 else 0,
        "sales_do_not_restore_reference": True,
    }


def buy_discipline_reasons(summary: dict[str, Any]) -> list[str]:
    """Describe the advisory facts without suppressing a strategy signal."""
    reasons: list[str] = []
    reference_price = summary.get("reference_buy_price")
    if reference_price is not None:
        reasons.append(
            "同价区参考最近一次已有价格的买入："
            f"{reference_price:.4f}；当前确认周期内 ±{summary['same_price_band_pct']:.2f}% 区间已有 "
            f"{summary['same_price_band_buy_count']} 次买入。"
        )
    if summary.get("same_price_band_attention"):
        reasons.append(
            f"同价区买入次数已达到参考值 {summary['same_price_band_max_buys']} 次；信号不被阻断，但应加强人工复核。"
        )
    if summary.get("unpriced_pending_buy_count"):
        reasons.append(
            f"另有 {summary['unpriced_pending_buy_count']} 笔待确认买入、合计 {summary['pending_buy_amount']:.2f} 元；尚无成交价，未计入同价区次数。"
        )
    plan = summary.get("planned_investment_reference")
    if plan is not None:
        remaining = summary["planned_reference_remaining_amount"]
        reasons.append(
            f"计划投入参考 {plan:.2f} 元；累计买入（含待确认）{summary['planned_reference_used_amount']:.2f} 元；"
            + (f"参考余量 {remaining:.2f} 元。" if remaining >= 0 else f"已超出参考值 {-remaining:.2f} 元。")
        )
    return reasons
