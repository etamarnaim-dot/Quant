from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
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
        max_quote_age_seconds: int = 60,
        state_db_path: str | Path | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not math.isfinite(slippage) or not 0 <= slippage < 1:
            raise ValueError("slippage must be finite and in [0, 1)")
        if (
            not isinstance(max_quote_age_seconds, int)
            or isinstance(max_quote_age_seconds, bool)
            or max_quote_age_seconds < 0
        ):
            raise ValueError("max_quote_age_seconds must be a non-negative integer")
        self._price_provider = price_provider
        self._slippage = slippage
        self._max_quote_age_seconds = max_quote_age_seconds
        self._clock = clock
        self._state_db_path = Path(state_db_path) if state_db_path is not None else None
        self._cash = 10_000.0
        self._day_start_equity = 10_000.0
        self._day_start_equity_date: date | None = None
        self._positions: dict[str, int] = {}
        self._orders: dict[str, SimOrder] = {}
        if self._state_db_path is not None:
            self._load_state()

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
        current_day = now.date()
        if self._day_start_equity_date != current_day:
            self._day_start_equity = equity
            self._day_start_equity_date = current_day
            self._persist_state()
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
            self._persist_state()
            return order

        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            return reject()
        if side not in ("BUY", "SELL"):
            return reject()
        if side == "SELL" and qty > self._positions.get(symbol, 0):
            return reject()

        quote = self._price_provider.get_quote(symbol, now)
        if quote is None:
            return reject()
        quote_age_seconds = (now - quote.timestamp.astimezone(timezone.utc)).total_seconds()
        if (
            quote.timestamp.tzinfo is None
            or quote.timestamp.utcoffset() is None
            or quote_age_seconds < 0
            or quote_age_seconds > self._max_quote_age_seconds
            or not math.isfinite(quote.price)
            or quote.price <= 0
        ):
            return reject()
        market_price = quote.price
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
        self._persist_state()
        return order

    def _load_state(self) -> None:
        assert self._state_db_path is not None
        self._state_db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._state_db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS broker_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL,
                    day_start_equity REAL NOT NULL,
                    day_start_equity_date TEXT
                );
                CREATE TABLE IF NOT EXISTS broker_positions (
                    symbol TEXT PRIMARY KEY,
                    qty INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broker_orders (
                    client_order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    fill_price REAL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            account = connection.execute(
                "SELECT cash, day_start_equity, day_start_equity_date FROM broker_account WHERE id = 1"
            ).fetchone()
            if account is not None:
                self._cash = float(account[0])
                self._day_start_equity = float(account[1])
                self._day_start_equity_date = date.fromisoformat(account[2]) if account[2] else None
            self._positions = dict(connection.execute("SELECT symbol, qty FROM broker_positions"))
            for row in connection.execute(
                "SELECT client_order_id, symbol, side, qty, fill_price, status, created_at FROM broker_orders"
            ):
                order = SimOrder(
                    row[0], row[1], row[2], int(row[3]), row[4], row[5], datetime.fromisoformat(row[6])
                )
                self._orders[order.client_order_id] = order
        self._persist_state()

    def _persist_state(self) -> None:
        if self._state_db_path is None:
            return
        with sqlite3.connect(self._state_db_path) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO broker_account (id, cash, day_start_equity, day_start_equity_date) "
                    "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "cash=excluded.cash, day_start_equity=excluded.day_start_equity, "
                    "day_start_equity_date=excluded.day_start_equity_date",
                    (
                        self._cash,
                        self._day_start_equity,
                        self._day_start_equity_date.isoformat() if self._day_start_equity_date else None,
                    ),
                )
                connection.execute("DELETE FROM broker_positions")
                connection.executemany(
                    "INSERT INTO broker_positions (symbol, qty) VALUES (?, ?)",
                    self._positions.items(),
                )
                connection.execute("DELETE FROM broker_orders")
                connection.executemany(
                    "INSERT INTO broker_orders "
                    "(client_order_id, symbol, side, qty, fill_price, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            order.client_order_id,
                            order.symbol,
                            order.side,
                            order.qty,
                            order.fill_price,
                            order.status,
                            order.created_at.isoformat(),
                        )
                        for order in self._orders.values()
                    ),
                )
