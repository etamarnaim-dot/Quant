from __future__ import annotations

from src.news import fetch_news_events, ingest_news_articles


def test_ingest_news_articles_creates_structured_events(tmp_path):
    db_path = tmp_path / "research.db"

    inserted = ingest_news_articles(
        db_path,
        [
            {
                "source": "demo",
                "title": "ACME raises guidance after strong earnings",
                "content": "ACME reported strong earnings and raised guidance for the next quarter.",
                "published_at": "2026-11-13T10:00:00Z",
                "url": "https://example.com/acme-guidance",
                "symbols": ["ACME"],
            }
        ],
    )

    assert len(inserted) == 1
    events = fetch_news_events(db_path)
    assert len(events) == 1
    assert events[0]["symbol"] == "ACME"
    assert events[0]["event_type"] == "guidance"
    assert 0.0 <= float(events[0]["sentiment"]) <= 1.0
    assert float(events[0]["relevance_score"]) > 0.5


def test_ingest_news_articles_deduplicates_same_url(tmp_path):
    db_path = tmp_path / "research.db"
    articles = [
        {
            "source": "demo",
            "title": "ACME raises guidance after strong earnings",
            "content": "ACME reported strong earnings and raised guidance for the next quarter.",
            "published_at": "2026-11-13T10:00:00Z",
            "url": "https://example.com/acme-guidance",
            "symbols": ["ACME"],
        },
        {
            "source": "demo",
            "title": "ACME raises guidance after strong earnings",
            "content": "ACME reported strong earnings and raised guidance for the next quarter.",
            "published_at": "2026-11-13T10:00:00Z",
            "url": "https://example.com/acme-guidance",
            "symbols": ["ACME"],
        },
    ]

    inserted = ingest_news_articles(db_path, articles)

    assert len(inserted) == 1
    assert len(fetch_news_events(db_path)) == 1
