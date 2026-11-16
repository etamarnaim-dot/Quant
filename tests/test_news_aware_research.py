from __future__ import annotations

from datetime import datetime, timezone

from src.news import ingest_news_articles
from src.research_runner import ResearchConfig, run_research_cycle
from src.strategies import ResearchSignal, StrategyRegistry, StrategySpec


NOW = datetime(2026, 1, 3, 15, 0, tzinfo=timezone.utc)


class Prices:
    def get_price(self, symbol, when):
        return 100.0


def _registry():
    def generator(symbol, history, now):
        return ResearchSignal(
            symbol=symbol,
            signal="BUY",
            confidence=0.7,
            horizon="1d",
            rationale="Price-only base signal.",
            model_name="test",
            price_at_signal=history[-1][1],
        )

    return StrategyRegistry([StrategySpec("test", "fixed test signal", generator)])


def _run(db_path):
    return run_research_cycle(
        db_path=db_path,
        price_provider=Prices(),
        config=ResearchConfig(symbols=("ACME",), strategy_names=("test",), lookback_days=2),
        now=NOW,
        registry=_registry(),
    )


def test_recent_positive_news_adjusts_confidence_and_rationale(tmp_path):
    db_path = tmp_path / "research.db"
    ingest_news_articles(
        db_path,
        [
            {
                "source": "fixture",
                "title": "ACME earnings beat and strong growth",
                "content": "ACME beats earnings with strong growth.",
                "published_at": "2026-01-03T14:00:00Z",
                "symbols": ["ACME"],
                "url": "https://example.com/acme-positive",
            }
        ],
    )

    signal = _run(db_path)[0]

    assert signal["confidence"] > 0.7
    assert "news context" in signal["rationale"].lower()


def test_future_news_does_not_adjust_signal(tmp_path):
    db_path = tmp_path / "research.db"
    ingest_news_articles(
        db_path,
        [
            {
                "source": "fixture",
                "title": "ACME weak results and downgrade",
                "content": "ACME misses and weakens outlook.",
                "published_at": "2026-01-03T16:00:00Z",
                "symbols": ["ACME"],
                "url": "https://example.com/acme-future",
            }
        ],
    )

    signal = _run(db_path)[0]

    assert signal["confidence"] == 0.7
    assert "news context" not in signal["rationale"].lower()