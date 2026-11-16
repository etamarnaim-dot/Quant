from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.market_data import FinnhubDailyPriceRefresher, FinnhubQuoteProvider, default_research_symbols
from src.prices import CsvPriceProvider


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_daily_refresher_persists_historical_candles_and_keeps_existing_rows(tmp_path, monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 3, tzinfo=timezone.utc)
    path = tmp_path / "prices.csv"
    path.write_text(
        "timestamp,symbol,price\n2025-12-31T00:00:00Z,ACME,90\n",
        encoding="utf-8",
    )
    payload = {
        "s": "ok",
        "t": [int(start.timestamp()), int((start + timedelta(days=1)).timestamp())],
        "c": [100.0, 105.0],
    }
    monkeypatch.setattr("src.market_data.request.urlopen", lambda *args, **kwargs: Response(payload))

    added = FinnhubDailyPriceRefresher(api_key="test-key").refresh(
        path,
        ["ACME"],
        start=start,
        end=end,
    )

    provider = CsvPriceProvider(path)
    assert added == 2
    assert provider.get_price("ACME", end) == 105.0
    assert provider.get_price("ACME", start - timedelta(seconds=1)) == 90.0
    assert provider.get_price("ACME", start) == 100.0
    assert "2025-12-31T00:00:00Z,ACME,90" in path.read_text(encoding="utf-8")


def test_quote_provider_returns_timestamped_quote_without_lookahead(monkeypatch):
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    quote_time = now - timedelta(seconds=30)
    payload = {"c": 123.45, "t": int(quote_time.timestamp())}
    monkeypatch.setattr("src.market_data.request.urlopen", lambda *args, **kwargs: Response(payload))

    quote = FinnhubQuoteProvider(api_key="test-key").get_quote("ACME", now)

    assert quote.price == 123.45
    assert quote.timestamp == quote_time


def test_quote_provider_does_not_return_future_quote(monkeypatch):
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    payload = {"c": 123.45, "t": int((now + timedelta(seconds=30)).timestamp())}
    monkeypatch.setattr("src.market_data.request.urlopen", lambda *args, **kwargs: Response(payload))

    quote = FinnhubQuoteProvider(api_key="test-key").get_quote("ACME", now)

    assert quote is None


def test_default_research_symbols_can_be_configured(monkeypatch):
    monkeypatch.setenv("RESEARCH_SYMBOLS", "aapl, msft, NVDA")

    assert default_research_symbols() == ("AAPL", "MSFT", "NVDA")