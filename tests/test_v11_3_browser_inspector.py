from src.browser_inspector import (
    historical_api_url,
    quote_page_url,
    redact_headers,
)


def test_quote_page_url_for_shanghai_etf():
    assert quote_page_url("561380") == (
        "https://quote.eastmoney.com/sh561380.html"
    )


def test_historical_api_url_uses_shanghai_secid():
    from datetime import datetime

    url = historical_api_url("561380", datetime(2026, 7, 16))
    assert "secid=1.561380" in url
    assert "klt=101" in url
    assert "end=20260716" in url


def test_sensitive_headers_are_redacted():
    headers = redact_headers({
        "User-Agent": "test",
        "Cookie": "secret=1",
        "Authorization": "Bearer secret",
    })
    assert headers["User-Agent"] == "test"
    assert headers["Cookie"] == "<redacted>"
    assert headers["Authorization"] == "<redacted>"
