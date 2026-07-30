from src.buy_discipline import buy_discipline_reasons, summarize_buy_discipline


def test_same_price_band_is_scoped_to_the_confirmed_low_cycle():
    transactions = [
        {"type": "BUY", "date": "2026-01-01", "price": 10, "amount": 100, "status": "CONFIRMED"},
        {"type": "BUY", "date": "2026-02-01", "price": 10, "amount": 100, "status": "ESTIMATED"},
        {"type": "BUY", "date": "2026-02-02", "price": 10.1, "amount": 100, "status": "CONFIRMED"},
        {"type": "BUY", "date": "2026-02-03", "price": 8.5, "amount": 100, "status": "CONFIRMED"},
        {"type": "BUY", "date": "2026-02-04", "price": 0, "amount": 250, "status": "PENDING_CLOSE"},
        {"type": "SELL", "date": "2026-02-05", "price": 11, "amount": 500, "status": "CONFIRMED"},
    ]
    summary = summarize_buy_discipline(
        {"same_price_band_pct": 3, "same_price_band_max_buys": 2, "planned_investment_reference": 500},
        transactions,
        {"last_confirmed_low": {"date": "2026-02-01"}},
    )

    assert summary["reference_buy_price"] == 8.5
    assert summary["same_price_band_buy_count"] == 1
    assert summary["same_price_band_attention"] is False
    assert summary["pending_buy_amount"] == 250
    assert summary["planned_reference_used_amount"] == 650
    assert summary["planned_reference_remaining_amount"] == -150
    assert summary["sales_do_not_restore_reference"] is True


def test_same_price_attention_is_advisory_and_pending_is_explained():
    transactions = [
        {"type": "BUY", "date": "2026-02-01", "price": 10, "amount": 100, "status": "CONFIRMED"},
        {"type": "BUY", "date": "2026-02-02", "price": 10.1, "amount": 100, "status": "ESTIMATED"},
        {"type": "BUY", "date": "2026-02-03", "price": 10.2, "amount": 100, "status": "CONFIRMED"},
        {"type": "BUY", "date": "2026-02-04", "price": 0, "amount": 80, "status": "PENDING"},
    ]
    summary = summarize_buy_discipline(
        {"same_price_band_pct": 3, "same_price_band_max_buys": 3},
        transactions,
        {"last_confirmed_low": {"date": "2026-02-01"}},
    )

    assert summary["same_price_band_buy_count"] == 3
    assert summary["same_price_band_attention"] is True
    reasons = buy_discipline_reasons(summary)
    assert any("不被阻断" in reason for reason in reasons)
    assert any("尚无成交价" in reason for reason in reasons)
