from __future__ import annotations

from datetime import datetime, timezone

from src.research_runner import (
    ResearchConfig,
    run_research_cycle,
    summarize_strategy_performance,
)


class FakePriceProvider:
    def __init__(self) -> None:
        self._prices = {
            "ACME": [
                (datetime(2026, 1, 1, tzinfo=timezone.utc), 90.0),
                (datetime(2026, 1, 5, tzinfo=timezone.utc), 95.0),
                (datetime(2026, 1, 10, tzinfo=timezone.utc), 102.0),
                (datetime(2026, 1, 15, tzinfo=timezone.utc), 110.0),
            ],
            "BETA": [
                (datetime(2026, 1, 1, tzinfo=timezone.utc), 200.0),
                (datetime(2026, 1, 5, tzinfo=timezone.utc), 205.0),
                (datetime(2026, 1, 10, tzinfo=timezone.utc), 190.0),
                (datetime(2026, 1, 15, tzinfo=timezone.utc), 185.0),
            ],
        }

    def get_price(self, symbol: str, when: datetime):
        prices = self._prices.get(symbol, [])
        last = None
        for timestamp, price in prices:
            if timestamp <= when:
                last = price
            else:
                break
        return last


def test_summarize_strategy_performance_ranks_models(tmp_path):
    db_path = tmp_path / "research.db"

    connection = __import__("sqlite3").connect(db_path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY, signal TEXT, confidence REAL, model_name TEXT, created_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS outcomes (id INTEGER PRIMARY KEY, signal_id INTEGER, return_pct REAL, no_trade INTEGER)"
    )
    connection.execute(
        "INSERT INTO signals (signal, confidence, model_name, created_at) VALUES (?, ?, ?, ?)",
        ("BUY", 0.80, "momentum", "2026-01-01T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO signals (signal, confidence, model_name, created_at) VALUES (?, ?, ?, ?)",
        ("BUY", 0.72, "momentum", "2026-01-02T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO signals (signal, confidence, model_name, created_at) VALUES (?, ?, ?, ?)",
        ("SELL", 0.65, "mean_reversion", "2026-01-03T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO outcomes (signal_id, return_pct, no_trade) VALUES (?, ?, ?)",
        (1, 0.12, 0),
    )
    connection.execute(
        "INSERT INTO outcomes (signal_id, return_pct, no_trade) VALUES (?, ?, ?)",
        (2, -0.04, 0),
    )
    connection.execute(
        "INSERT INTO outcomes (signal_id, return_pct, no_trade) VALUES (?, ?, ?)",
        (3, 0.07, 0),
    )
    connection.commit()
    connection.close()

    ranking = summarize_strategy_performance(db_path)

    assert len(ranking) >= 2
    assert ranking[0]["model_name"] == "momentum"
    assert ranking[0]["signal_count"] == 2
    assert ranking[0]["average_confidence"] > 0.7


def test_run_research_cycle_returns_strategy_summary(tmp_path):
    provider = FakePriceProvider()
    db_path = tmp_path / "research.db"
    config = ResearchConfig(symbols=("ACME", "BETA"), horizons=("1d", "5d"), max_signals_per_symbol=2)

    result = run_research_cycle(
        db_path=db_path,
        price_provider=provider,
        config=config,
        now=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
        return_summary=True,
    )

    assert result["signals_generated"] >= 2
    assert "strategy_ranking" in result
    assert isinstance(result["strategy_ranking"], list)
