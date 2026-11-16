from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.backtest import walk_forward_backtest
from src.strategies import ResearchSignal, StrategyRegistry, StrategySpec


def test_walk_forward_backtest_returns_empty_for_missing_price_history(tmp_path):
    assert walk_forward_backtest(tmp_path / "missing.csv") == []


def test_walk_forward_backtest_uses_only_out_of_sample_dates_and_costs(tmp_path):
    start = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
    rows = ["timestamp,symbol,price"]
    for day in range(10):
        timestamp = (start + timedelta(days=day)).isoformat().replace("+00:00", "Z")
        rows.append(f"{timestamp},ACME,{100 + day}")
    price_path = tmp_path / "prices.csv"
    price_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def buy_only(symbol, history, now):
        assert history[-1][0] == now
        return ResearchSignal(symbol, "BUY", 0.8, "1d", "test", "buy-only")

    registry = StrategyRegistry([StrategySpec("buy-only", "test strategy", buy_only)])
    result = walk_forward_backtest(
        price_path,
        registry=registry,
        horizon="1d",
        train_fraction=0.6,
        cost_bps=10,
    )

    assert len(result) == 1
    assert result[0]["out_of_sample_start"] == (start + timedelta(days=6)).isoformat()
    assert result[0]["trades"] == 3
    assert result[0]["average_net_return"] < result[0]["average_gross_return"]
    assert result[0]["win_rate"] == 1.0