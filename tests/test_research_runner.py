from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.research_runner import ResearchConfig, run_research_cycle
from src.strategies import StrategyRegistry, get_default_registry


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


def test_default_registry_contains_multiple_strategies():
    registry = get_default_registry()

    assert isinstance(registry, StrategyRegistry)
    assert len(registry.names) >= 2
    assert "momentum" in registry.names
    assert "mean_reversion" in registry.names


def test_run_research_cycle_creates_signals(tmp_path):
    provider = FakePriceProvider()
    db_path = tmp_path / "research.db"
    config = ResearchConfig(symbols=["ACME", "BETA"], horizons=["1d", "5d"], max_signals_per_symbol=2)

    signals = run_research_cycle(
        db_path=db_path,
        price_provider=provider,
        config=config,
        now=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
    )

    assert len(signals) >= 2
    for signal in signals:
        assert signal["symbol"] in {"ACME", "BETA"}
        assert signal["signal"] in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= float(signal["confidence"]) <= 1.0
        assert "model_name" in signal


def test_repeated_same_day_cycle_does_not_duplicate_unchanged_signals(tmp_path):
    provider = FakePriceProvider()
    db_path = tmp_path / "research.db"
    now = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
    config = ResearchConfig(symbols=("ACME",), strategy_names=("momentum",), lookback_days=30)

    first = run_research_cycle(db_path=db_path, price_provider=provider, config=config, now=now)
    second = run_research_cycle(db_path=db_path, price_provider=provider, config=config, now=now)

    assert first
    assert second == []
