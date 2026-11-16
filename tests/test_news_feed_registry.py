from __future__ import annotations

import json

from src.news_sources import FeedRegistry, JsonFeedSource


def test_json_feed_source_reads_articles(tmp_path):
    data_path = tmp_path / "articles.json"
    data_path.write_text(
        json.dumps(
            [
                {
                    "source": "fixture",
                    "title": "ACME launches new product",
                    "content": "ACME launched a new product line for customers.",
                    "published_at": "2026-11-13T12:00:00Z",
                    "url": "https://example.com/acme-product",
                    "symbols": ["ACME"],
                }
            ]
        ),
        encoding="utf-8",
    )

    source = JsonFeedSource(data_path)
    articles = source.fetch_articles()

    assert len(articles) == 1
    assert articles[0]["symbol"] == "ACME"
    assert articles[0]["title"].startswith("ACME")


def test_registry_fetches_all_sources(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps([{"source": "fixture", "title": "One", "content": "One", "symbols": ["ACME"]}]), encoding="utf-8")
    second.write_text(json.dumps([{"source": "fixture", "title": "Two", "content": "Two", "symbols": ["BETA"]}]), encoding="utf-8")

    registry = FeedRegistry()
    registry.register(JsonFeedSource(first))
    registry.register(JsonFeedSource(second))
    articles = registry.fetch_articles()

    assert len(articles) == 2
    symbols = {item["symbol"] for item in articles}
    assert {"ACME", "BETA"} <= symbols
