from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from .backtest import walk_forward_backtest
from .dashboard_data import DEFAULT_DB_PATH
from .market_data import (
    DEFAULT_PRICE_PATH,
    FinnhubDailyPriceRefresher,
    default_research_symbols,
    volatility_rankings,
)
from .news import fetch_live_news
from .outcomes import process_outcomes
from .prices import CsvPriceProvider
from .research_runner import ResearchConfig, run_research_cycle


class PriceRefresher(Protocol):
    def refresh(
        self,
        csv_path: str | Path,
        symbols: Iterable[str],
        *,
        start: datetime,
        end: datetime,
    ) -> int: ...


NewsFetcher = Callable[..., list[dict[str, object]]]


def run_autonomous_cycle(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    price_path: str | Path = DEFAULT_PRICE_PATH,
    universe: Iterable[str],
    price_refresher: PriceRefresher,
    news_fetcher: NewsFetcher = fetch_live_news,
    now: datetime | None = None,
    research_config: ResearchConfig | None = None,
    lookback_days: int = 30,
    history_days: int = 90,
    top_n: int = 10,
) -> dict[str, object]:
    cycle_time = now or datetime.now(timezone.utc)
    if cycle_time.tzinfo is None or cycle_time.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    cycle_time = cycle_time.astimezone(timezone.utc)
    symbols = tuple(dict.fromkeys(str(symbol).strip().upper() for symbol in universe if str(symbol).strip()))
    if not symbols:
        raise ValueError("universe must contain at least one symbol")
    if lookback_days < 1 or history_days < lookback_days or top_n < 1:
        raise ValueError("history_days must cover a positive lookback and top_n must be positive")

    price_file = Path(price_path)
    price_file.parent.mkdir(parents=True, exist_ok=True)
    rows_added = price_refresher.refresh(
        price_file,
        symbols,
        start=cycle_time - timedelta(days=history_days),
        end=cycle_time,
    )
    rankings = volatility_rankings(price_file, limit=top_n)
    watchlist = [str(row["symbol"]) for row in rankings]
    if not watchlist:
        raise RuntimeError("no symbols have enough price history to rank")

    news_items = news_fetcher(db_path, symbols=watchlist)
    provider = CsvPriceProvider(price_file)
    config = replace(
        research_config or ResearchConfig(),
        symbols=tuple(watchlist),
        lookback_days=lookback_days,
    )
    research = run_research_cycle(
        db_path=db_path,
        price_provider=provider,
        config=config,
        now=cycle_time,
        return_summary=True,
    )
    resolved_outcomes = process_outcomes(db_path, provider, cycle_time)
    out_of_sample_evaluation = walk_forward_backtest(
        price_file,
        symbols=watchlist,
        horizon="1d",
        cost_bps=5.0,
    )

    return {
        "checked_at": cycle_time.isoformat(),
        "universe": list(symbols),
        "watchlist": watchlist,
        "price_rows_added": rows_added,
        "news_items_ingested": len(news_items),
        "signals_generated": research["signals_generated"],
        "signals": research["signals"],
        "strategy_ranking": research["strategy_ranking"],
        "strategy_drift": research["strategy_drift"],
        "out_of_sample_evaluation": out_of_sample_evaluation,
        "outcomes_resolved": resolved_outcomes,
    }


@dataclass
class AutonomousResearchScheduler:
    db_path: str | Path = DEFAULT_DB_PATH
    price_path: str | Path = DEFAULT_PRICE_PATH
    universe: tuple[str, ...] = ()
    price_refresher: PriceRefresher | None = None
    news_fetcher: NewsFetcher = fetch_live_news
    interval_seconds: float = 3600.0
    lookback_days: int = 30
    history_days: int = 90
    top_n: int = 10
    last_cycle_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.price_refresher is None:
            self.price_refresher = FinnhubDailyPriceRefresher()
        if not self.universe:
            self.universe = default_research_symbols()
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

    def run_once(self, *, now: datetime | None = None) -> dict[str, object]:
        cycle_time = now or datetime.now(timezone.utc)
        result = run_autonomous_cycle(
            db_path=self.db_path,
            price_path=self.price_path,
            universe=self.universe,
            price_refresher=self.price_refresher,
            news_fetcher=self.news_fetcher,
            now=cycle_time,
            lookback_days=self.lookback_days,
            history_days=self.history_days,
            top_n=self.top_n,
        )
        self.last_cycle_at = cycle_time
        return result

    def run_if_due(self, *, now: datetime | None = None) -> dict[str, object] | None:
        cycle_time = now or datetime.now(timezone.utc)
        if self.last_cycle_at is not None:
            elapsed = (cycle_time - self.last_cycle_at).total_seconds()
            if elapsed < self.interval_seconds:
                return None
        return self.run_once(now=cycle_time)

    def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            result = self.run_if_due()
            if result is not None:
                print(json.dumps(result, indent=2))
            elapsed = time.monotonic() - started
            time.sleep(max(1.0, self.interval_seconds - elapsed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh market data and run a paper-trading research cycle.")
    parser.add_argument("--watch", action="store_true", help="repeat cycles at the configured interval")
    parser.add_argument("--interval", type=float, default=3600.0, help="seconds between cycles")
    parser.add_argument("--symbols", nargs="*", help="symbols to rank; defaults to RESEARCH_SYMBOLS")
    parser.add_argument("--top", type=int, default=10, help="number of volatile symbols to research")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--history-days", type=int, default=90)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICE_PATH)
    args = parser.parse_args(argv)
    scheduler = AutonomousResearchScheduler(
        db_path=args.db,
        price_path=args.prices,
        universe=tuple(args.symbols) if args.symbols else default_research_symbols(),
        interval_seconds=args.interval,
        top_n=args.top,
        lookback_days=args.lookback_days,
        history_days=args.history_days,
    )
    if args.watch:
        scheduler.run_forever()
    else:
        print(json.dumps(scheduler.run_once(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())