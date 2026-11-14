from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable


@dataclass(frozen=True)
class ResearchSignal:
    symbol: str
    signal: str
    confidence: float
    horizon: str
    rationale: str
    model_name: str
    prompt_version: str = "research-v1"
    price_at_signal: float | None = None

    def as_payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "signal": self.signal,
            "confidence": float(self.confidence),
            "horizon": self.horizon,
            "rationale": self.rationale,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "price_at_signal": self.price_at_signal,
        }


@dataclass(frozen=True)
class StrategySpec:
    name: str
    description: str
    generator: Callable[[str, list[tuple[datetime, float]], datetime], ResearchSignal | None]


class StrategyRegistry:
    def __init__(self, strategies: Iterable[StrategySpec] | None = None) -> None:
        self._strategies: dict[str, StrategySpec] = {}
        for strategy in strategies or ():
            self.register(strategy)

    def register(self, strategy: StrategySpec) -> None:
        self._strategies[strategy.name] = strategy

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._strategies.keys())

    def get(self, name: str) -> StrategySpec:
        return self._strategies[name]

    def iterate(self) -> tuple[StrategySpec, ...]:
        return tuple(self._strategies.values())


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _price_history_summary(history: list[tuple[datetime, float]], now: datetime) -> tuple[float, float, float]:
    if not history:
        raise ValueError("price history is empty")
    current_price = history[-1][1]
    if len(history) >= 5:
        short_price = history[-5][1]
        short_return = (current_price - short_price) / short_price if short_price else 0.0
    else:
        short_return = 0.0
    if len(history) >= 20:
        long_price = history[-20][1]
        long_return = (current_price - long_price) / long_price if long_price else 0.0
    else:
        long_return = short_return
    average_price = sum(price for _, price in history) / len(history)
    return current_price, short_return, long_return, average_price


def momentum_strategy(symbol: str, history: list[tuple[datetime, float]], now: datetime) -> ResearchSignal | None:
    if len(history) < 2:
        return None
    current_price = history[-1][1]
    short_window = history[-5][1] if len(history) >= 5 else history[0][1]
    long_window = history[-20][1] if len(history) >= 20 else history[0][1]
    short_return = (current_price - short_window) / short_window if short_window else 0.0
    long_return = (current_price - long_window) / long_window if long_window else 0.0
    score = float((short_return * 0.6) + (long_return * 0.4))
    if score > 0.04:
        signal = "BUY"
        direction = "uptrend momentum remains positive"
    elif score < -0.04:
        signal = "SELL"
        direction = "downtrend momentum remains negative"
    else:
        signal = "HOLD"
        direction = "price trend is too weak to act on"
    confidence = _clamp(abs(score) * 18.0 + 0.6, 0.6, 0.95)
    return ResearchSignal(
        symbol=symbol,
        signal=signal,
        confidence=confidence,
        horizon="5d",
        rationale=f"Momentum strategy: {direction}; current price {current_price:.2f} vs prior window behavior.",
        model_name="momentum",
        price_at_signal=current_price,
    )


def mean_reversion_strategy(symbol: str, history: list[tuple[datetime, float]], now: datetime) -> ResearchSignal | None:
    if len(history) < 3:
        return None
    current_price = history[-1][1]
    average_price = sum(price for _, price in history) / len(history)
    normalized_gap = (current_price - average_price) / average_price if average_price else 0.0
    if normalized_gap < -0.03:
        signal = "BUY"
        direction = "price is trading below its rolling average"
    elif normalized_gap > 0.03:
        signal = "SELL"
        direction = "price is trading above its rolling average"
    else:
        signal = "HOLD"
        direction = "price remains near its rolling average"
    confidence = _clamp(abs(normalized_gap) * 20.0 + 0.6, 0.6, 0.94)
    return ResearchSignal(
        symbol=symbol,
        signal=signal,
        confidence=confidence,
        horizon="20d",
        rationale=f"Mean reversion strategy: {direction}; average price {average_price:.2f} vs current {current_price:.2f}.",
        model_name="mean_reversion",
        price_at_signal=current_price,
    )


def get_default_registry() -> StrategyRegistry:
    return StrategyRegistry(
        (
            StrategySpec(
                name="momentum",
                description="Trend-following momentum strategy over recent price changes.",
                generator=momentum_strategy,
            ),
            StrategySpec(
                name="mean_reversion",
                description="Mean-reversion strategy based on price vs rolling average.",
                generator=mean_reversion_strategy,
            ),
        )
    )
