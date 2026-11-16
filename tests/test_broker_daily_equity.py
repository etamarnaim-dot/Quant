from __future__ import annotations

from datetime import datetime, timezone

from src.broker import SimBroker
from src.prices import CsvPriceProvider
from src.risk import AccountState, ProposedTrade, RiskConfig, check_trade


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def _make_price_provider():
    return CsvPriceProvider("data/prices.csv")


def test_broker_keeps_day_start_equity_constant_within_same_day():
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    clock = Clock(now)
    broker = SimBroker(_make_price_provider(), clock=clock)

    first = broker.get_account()
    broker._cash = 9700.0
    second = broker.get_account()

    assert first.day_start_equity == 10000.0
    assert second.day_start_equity == 10000.0


def test_broker_resets_day_start_equity_on_new_utc_day():
    day_one = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    day_two = datetime(2026, 1, 6, 9, 0, tzinfo=timezone.utc)
    clock = Clock(day_one)
    broker = SimBroker(_make_price_provider(), clock=clock)
    broker._cash = 9600.0
    broker._positions["ACME"] = 1
    broker.get_account()

    clock.value = day_two
    account = broker.get_account()

    assert account.day_start_equity == 9600.0 + 100.0


def test_day_2_loss_does_not_trip_day_1_cap():
    day_one = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    day_two = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
    clock = Clock(day_one)
    broker = SimBroker(_make_price_provider(), clock=clock)
    broker._cash = 9700.0
    broker.get_account()

    clock.value = day_two
    broker._cash = 9700.0
    account = broker.get_account()
    assert account.day_start_equity == 9700.0

    decision = check_trade(
        ProposedTrade(symbol="ACME", side="BUY", price=100.0, confidence=0.9, price_timestamp=day_two),
        AccountState(equity=9700.0, day_start_equity=9700.0, cash=9700.0, positions={}, as_of=day_two),
        RiskConfig(symbol_whitelist=frozenset({"ACME"})),
        day_two,
    )
    assert decision.approved is True


def test_3_percent_loss_within_one_day_trips_cap():
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    broker = SimBroker(_make_price_provider(), clock=Clock(now))
    broker._cash = 9700.0
    account = broker.get_account()

    decision = check_trade(
        ProposedTrade(symbol="ACME", side="BUY", price=100.0, confidence=0.9, price_timestamp=now),
        AccountState(equity=9700.0, day_start_equity=10000.0, cash=9700.0, positions={}, as_of=now),
        RiskConfig(symbol_whitelist=frozenset({"ACME"})),
        now,
    )
    assert decision.approved is False
