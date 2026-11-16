from __future__ import annotations

import json

from src.news_sources import FeedRegistry, FinnhubNewsSource, NewsSource, build_default_news_sources


class FakeNewsSource(NewsSource):
    def fetch_articles(self):
        return [
            {
                "source": "mock",
                "title": "ACME earnings beat expectations",
                "content": "ACME reported strong earnings and beat expectations.",
                "published_at": "2026-11-13T12:00:00Z",
                "symbols": ["ACME"],
                "url": "https://example.com/mock-1",
            }
        ]


def test_registry_supports_custom_provider():
    registry = FeedRegistry([FakeNewsSource()])
    articles = registry.fetch_articles()

    assert len(articles) == 1
    assert articles[0]["symbol"] == "ACME"
    assert articles[0]["source"] == "mock"


def test_default_registry_uses_finnhub_key(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "demo-key")
    registry = build_default_news_sources(symbols=["AAPL", "MSFT"])

    assert any(isinstance(source, FinnhubNewsSource) for source in registry._sources)


def test_finnhub_general_news_uses_related_configured_symbols_only(monkeypatch):
    payload = [
        {"headline": "AAPL and TSLA announce a partnership", "related": "AAPL,TSLA", "datetime": 1794787740},
        {"headline": "Microsoft reports results", "related": "MSFT", "datetime": 1794787740},
        {"headline": "Alphabet announces a product", "related": "GOOG", "datetime": 1794787740},
    ]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr("src.news_sources.request.urlopen", lambda *args, **kwargs: Response())
    source = FinnhubNewsSource(api_key="offline-test-key", symbols=("AAPL", "MSFT"))

    articles = source.fetch_articles()

    assert [article["symbols"] for article in articles] == [["AAPL"], ["MSFT"]]
