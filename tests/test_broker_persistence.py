from __future__ import annotations

from datetime import datetime, timezone

from src.broker import SimBroker
from src.prices import Quote


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class Prices:
    def get_price(self, symbol, when):
        return 100.0 if symbol == "ACME" else None

    def get_quote(self, symbol, when):
        return Quote(100.0, NOW) if symbol == "ACME" else None


def test_sim_broker_restores_cash_positions_and_idempotent_orders(tmp_path):
    state_path = tmp_path / "broker.db"
    broker = SimBroker(Prices(), clock=lambda: NOW, state_db_path=state_path)
    original_order = broker.submit_order("ACME", "BUY", 2, "persisted-order")
    original_account = broker.get_account()

    restarted = SimBroker(Prices(), clock=lambda: NOW, state_db_path=state_path)
    duplicate_order = restarted.submit_order("ACME", "BUY", 2, "persisted-order")
    restored_account = restarted.get_account()

    assert restarted.get_positions() == {"ACME": 2}
    assert restored_account.cash == original_account.cash
    assert restored_account.day_start_equity == original_account.day_start_equity
    assert duplicate_order == original_order
    assert restarted.get_account().cash == original_account.cash