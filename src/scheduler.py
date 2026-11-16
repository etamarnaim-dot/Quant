from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .research_runner import ResearchConfig, run_research_cycle
from .strategies import StrategyRegistry, get_default_registry


class PriceProviderLike(Protocol):
    def get_price(self, symbol: str, when: datetime) -> float | None:
        ...


@dataclass
class ResearchScheduler:
    db_path: str | Path = "data/research.db"
    price_provider: PriceProviderLike | None = None
    config: ResearchConfig = field(default_factory=ResearchConfig)
    registry: StrategyRegistry | None = None
    now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    interval_seconds: float = 3600.0
    last_cycle_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.price_provider is None:
            raise ValueError("price_provider is required")
        if self.registry is None:
            self.registry = get_default_registry()
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

    def run_once(self, *, now: datetime | None = None) -> dict[str, object]:
        resolved_now = now or self.now_factory()
        db_path = Path(self.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        result = run_research_cycle(
            db_path=db_path,
            price_provider=self.price_provider,
            config=self.config,
            now=resolved_now,
            registry=self.registry,
            return_summary=True,
        )
        self.last_cycle_at = resolved_now
        return result

    def run_if_due(self, *, now: datetime | None = None) -> dict[str, object] | None:
        resolved_now = now or self.now_factory()
        if self.last_cycle_at is not None:
            elapsed = (resolved_now - self.last_cycle_at).total_seconds()
            if elapsed < self.interval_seconds:
                return None
        return self.run_once(now=resolved_now)
