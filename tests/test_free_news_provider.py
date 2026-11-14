from __future__ import annotations

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
