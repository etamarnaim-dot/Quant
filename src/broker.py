from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from .prices import PriceProvider
from .risk import AccountState


@dataclass
class SimOrder:
    client_order_id: str
    symbol: str
    side: str
    qty: int
    fill_price: float | None
    status: str
    created_at: datetime


class Broker(Protocol):
    def get_account(self) -> AccountState: ...

    def get_positions(self) -> dict[str, int]: ...

    def submit_order(
        self, symbol: str, side: str, qty: int, client_order_id: str
    ) -> SimOrder: ...

    def get_order(self, client_order_id: str) -> SimOrder | None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SimBroker:
    def __init__(
        self,
        price_provider: PriceProvider,
        *,
        slippage: float = 0.001,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not math.isfinite(slippage) or not 0 <= slippage < 1:
            raise ValueError("slippage must be finite and in [0, 1)")
        self._price_provider = price_provider
        self._slippage = slippage
        self._clock = clock
        self._cash = 10_000.0
        self._day_start_equity = 10_000.0
        self._positions: dict[str, int] = {}
        self._orders: dict[str, SimOrder] = {}

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("broker clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    def get_account(self) -> AccountState:
        now = self._now()
        positions: dict[str, float] = {}
        for symbol, qty in self._positions.items():
            price = self._price_provider.get_price(symbol, now)
            if price is None or not math.isfinite(price) or price <= 0:
                raise ValueError(f"no valid price available for held symbol {symbol}")
            positions[symbol] = qty * price
        equity = self._cash + sum(positions.values())
        return AccountState(
            equity=equity,
            day_start_equity=self._day_start_equity,
            cash=self._cash,
            positions=positions,
            as_of=now,
        )

    def get_positions(self) -> dict[str, int]:
        return dict(self._positions)

    def get_order(self, client_order_id: str) -> SimOrder | None:
        return self._orders.get(client_order_id)

    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        client_order_id: str,
    ) -> SimOrder:
        existing = self.get_order(client_order_id)
        if existing is not None:
            return existing
        now = self._now()

        def reject() -> SimOrder:
            order = SimOrder(client_order_id, symbol, side, qty, None, "rejected", now)
            self._orders[client_order_id] = order
            return order

        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            return reject()
        if side not in ("BUY", "SELL"):
            return reject()
        if side == "SELL" and qty > self._positions.get(symbol, 0):
            return reject()

        market_price = self._price_provider.get_price(symbol, now)
        if market_price is None or not math.isfinite(market_price) or market_price <= 0:
            return reject()
        fill_price = market_price * (1 + self._slippage if side == "BUY" else 1 - self._slippage)
        if side == "BUY" and qty * fill_price > self._cash:
            return reject()

        if side == "BUY":
            self._cash -= qty * fill_price
            self._positions[symbol] = self._positions.get(symbol, 0) + qty
        else:
            self._cash += qty * fill_price
            remaining = self._positions[symbol] - qty
            if remaining:
                self._positions[symbol] = remaining
            else:
                del self._positions[symbol]

        order = SimOrder(client_order_id, symbol, side, qty, fill_price, "filled", now)
        self._orders[client_order_id] = order
        return order
