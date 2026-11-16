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


def test_ingest_news_articles_accepts_epoch_publication_time(tmp_path):
    db_path = tmp_path / "research.db"
    inserted = ingest_news_articles(
        db_path,
        [
            {
                "source": "finnhub",
                "title": "ACME beats earnings",
                "content": "ACME beats earnings estimates.",
                "published_at": 1794787740,
                "url": "https://example.com/acme-epoch",
                "symbols": ["ACME"],
            }
        ],
    )

    assert len(inserted) == 1
    assert fetch_news_events(db_path)[0]["published_at"] == "2026-11-16T00:09:00+00:00"


def test_reingesting_existing_article_does_not_duplicate_across_cycles(tmp_path):
    db_path = tmp_path / "research.db"
    article = {
        "source": "fixture",
        "title": "ACME announces earnings",
        "content": "ACME announces quarterly earnings.",
        "published_at": "2026-01-03T14:00:00Z",
        "url": "https://example.com/acme-recurring",
        "symbols": ["ACME"],
    }

    assert len(ingest_news_articles(db_path, [article])) == 1
    assert ingest_news_articles(db_path, [article]) == []
    assert len(fetch_news_events(db_path)) == 1
