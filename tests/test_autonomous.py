from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.autonomous import AutonomousResearchScheduler, run_autonomous_cycle


NOW = datetime(2026, 1, 3, 15, 0, tzinfo=timezone.utc)


class FakePriceRefresher:
    def refresh(self, csv_path, symbols, *, start, end):
        assert tuple(symbols) == ("AAPL", "MSFT", "NVDA")
        assert start.tzinfo is not None and end == NOW
        csv_path.write_text(
            "timestamp,symbol,price\n"
            "2026-01-01T15:00:00Z,AAPL,100\n"
            "2026-01-02T15:00:00Z,AAPL,110\n"
            "2026-01-03T15:00:00Z,AAPL,90\n"
            "2026-01-01T15:00:00Z,MSFT,100\n"
            "2026-01-02T15:00:00Z,MSFT,101\n"
            "2026-01-03T15:00:00Z,MSFT,102\n"
            "2026-01-01T15:00:00Z,NVDA,100\n"
            "2026-01-02T15:00:00Z,NVDA,120\n"
            "2026-01-03T15:00:00Z,NVDA,80\n",
            encoding="utf-8",
        )
        return 9


def test_autonomous_cycle_uses_volatility_watchlist_for_news_and_research(tmp_path):
    news_calls = []

    def news_fetcher(db_path, *, symbols):
        news_calls.append(tuple(symbols))
        return []

    result = run_autonomous_cycle(
        db_path=tmp_path / "research.db",
        price_path=tmp_path / "prices.csv",
        universe=("AAPL", "MSFT", "NVDA"),
        price_refresher=FakePriceRefresher(),
        news_fetcher=news_fetcher,
        now=NOW,
        lookback_days=2,
        top_n=2,
    )

    assert result["watchlist"] == ["NVDA", "AAPL"]
    assert news_calls == [("NVDA", "AAPL")]
    assert result["price_rows_added"] == 9
    assert result["signals_generated"] >= 1


def test_autonomous_scheduler_runs_only_when_interval_is_due(tmp_path):
    scheduler = AutonomousResearchScheduler(
        db_path=tmp_path / "research.db",
        price_path=tmp_path / "prices.csv",
        universe=("AAPL", "MSFT", "NVDA"),
        price_refresher=FakePriceRefresher(),
        news_fetcher=lambda db_path, *, symbols: [],
        interval_seconds=3600,
        lookback_days=2,
        history_days=2,
        top_n=2,
    )

    first = scheduler.run_if_due(now=NOW)
    skipped = scheduler.run_if_due(now=NOW + timedelta(minutes=30))

    assert first is not None
    assert skipped is None