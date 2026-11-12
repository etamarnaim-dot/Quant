import logging
import sqlite3
from datetime import datetime, timezone

import pytest

from src.broker import SimBroker
from src.db import create_tables, fetch_order, insert_signal
from src.executor import process_signal
from src.risk import ProposedTrade, RiskConfig, check_trade


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class StaticPrices:
    def get_price(self, symbol, when):
        return 100.0 if symbol == "ACME" else None


class RecordingBroker(SimBroker):
    def __init__(self):
        super().__init__(StaticPrices(), clock=lambda: NOW)
        self.submissions = []

    def submit_order(self, symbol, side, qty, client_order_id):
        self.submissions.append((symbol, side, qty, client_order_id))
        return super().submit_order(symbol, side, qty, client_order_id)


class FailingBroker(RecordingBroker):
    def submit_order(self, symbol, side, qty, client_order_id):
        self.submissions.append((symbol, side, qty, client_order_id))
        raise RuntimeError("simulated broker failure")


def _signal(db_path, signal_type="BUY", confidence=0.8):
    create_tables(db_path)
    signal_id = insert_signal(
        db_path,
        item_id=None,
        symbol="ACME",
        signal=signal_type,
        confidence=confidence,
        horizon="1d",
        rationale="Fictional test signal.",
        model_name="fake-model",
        prompt_version="v1",
        created_at=NOW.isoformat().replace("+00:00", "Z"),
    )
    return {
        "id": signal_id,
        "symbol": "ACME",
        "signal": signal_type,
        "confidence": confidence,
    }


def _risk_config():
    return RiskConfig(symbol_whitelist=frozenset({"ACME"}))


def _order_count(db_path):
    with sqlite3.connect(db_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def test_hold_logs_and_does_not_submit(tmp_path, caplog):
    db_path = tmp_path / "research.db"
    signal = _signal(db_path, signal_type="HOLD")
    broker = RecordingBroker()

    with caplog.at_level(logging.INFO, logger="src.executor"):
        result = process_signal(signal, 100.0, broker, _risk_config(), NOW, db_path=db_path)

    assert result is None
    assert broker.submissions == []
    assert "HOLD signal" in caplog.text
    assert _order_count(db_path) == 0


def test_rejected_trade_is_logged_without_submission(tmp_path):
    db_path = tmp_path / "research.db"
    signal = _signal(db_path)
    broker = RecordingBroker()

    result = process_signal(signal, 100.0, broker, RiskConfig(), NOW, db_path=db_path)

    assert result is None
    assert broker.submissions == []
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT approved FROM risk_events").fetchone()[0] == 0
    assert _order_count(db_path) == 0


def test_approved_trade_submits_risk_quantity_and_is_idempotent(tmp_path):
    db_path = tmp_path / "research.db"
    signal = _signal(db_path)
    broker = RecordingBroker()
    config = _risk_config()
    expected = check_trade(
        ProposedTrade("ACME", "BUY", 100.0, 0.8, NOW),
        broker.get_account(),
        config,
        NOW,
    ).qty

    first = process_signal(signal, 100.0, broker, config, NOW, db_path=db_path)
    second = process_signal(signal, 100.0, broker, config, NOW, db_path=db_path)

    assert first is not None
    assert first.qty == expected
    assert broker.submissions == [("ACME", "BUY", expected, f"signal-{signal['id']}")]
    assert second is first
    assert broker.submit_order("ACME", "BUY", expected, f"signal-{signal['id']}") is first
    assert _order_count(db_path) == 1
    assert fetch_order(db_path, f"signal-{signal['id']}")["qty"] == expected


def test_kill_switch_blocks_and_logs(tmp_path, monkeypatch):
    db_path = tmp_path / "research.db"
    signal = _signal(db_path)
    broker = RecordingBroker()
    kill_path = tmp_path / "KILL"
    kill_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("src.executor.KILL_SWITCH_PATH", kill_path)

    result = process_signal(signal, 100.0, broker, _risk_config(), NOW, db_path=db_path)

    assert result is None
    assert broker.submissions == []
    with sqlite3.connect(db_path) as connection:
        reasons = connection.execute("SELECT reasons FROM risk_events").fetchone()[0]
    assert "KILL file exists" in reasons
    assert _order_count(db_path) == 0


def test_broker_error_is_logged_without_order(tmp_path, caplog):
    db_path = tmp_path / "research.db"
    signal = _signal(db_path)
    broker = FailingBroker()

    with caplog.at_level(logging.ERROR, logger="src.executor"):
        result = process_signal(signal, 100.0, broker, _risk_config(), NOW, db_path=db_path)

    assert result is None
    assert "simulated broker failure" in caplog.text
    assert _order_count(db_path) == 0
    assert broker.get_order(f"signal-{signal['id']}") is None


def test_slippage_is_unfavorable_for_both_sides():
    broker = SimBroker(StaticPrices(), clock=lambda: NOW)

    buy = broker.submit_order("ACME", "BUY", 1, "buy-1")
    sell = broker.submit_order("ACME", "SELL", 1, "sell-1")

    assert buy.fill_price == pytest.approx(100.1)
    assert sell.fill_price == pytest.approx(99.9)


def test_insufficient_cash_rejects_order_without_changing_account():
    broker = SimBroker(StaticPrices(), clock=lambda: NOW)

    order = broker.submit_order("ACME", "BUY", 101, "too-large")

    assert order.status == "rejected"
    assert order.fill_price is None
    assert broker.get_account().cash == 10_000.0
    assert broker.get_positions() == {}
