from __future__ import annotations

from datetime import datetime, timezone

from src.scheduler import ResearchScheduler


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


def test_scheduler_run_once_returns_summary(tmp_path):
    provider = FakePriceProvider()
    scheduler = ResearchScheduler(
        db_path=tmp_path / "research.db",
        price_provider=provider,
        now_factory=lambda: datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
    )

    result = scheduler.run_once()

    assert result["signals_generated"] >= 2
    assert "strategy_ranking" in result
    assert result["strategy_ranking"]
    assert scheduler.last_cycle_at is not None
