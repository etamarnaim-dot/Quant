import ast
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import nightly
from src.db import create_tables, insert_signal, log_risk_decision
from src.risk import ProposedTrade, RiskDecision


NOW = datetime(2026, 11, 12, 21, 0, tzinfo=timezone.utc)


class Prices:
    def __init__(self, latest=NOW):
        self.latest_timestamp = latest

    def get_price(self, symbol, when):
        return 100.0


class BadTimestampPrices(Prices):
    def __init__(self):
        super().__init__(datetime(2026, 11, 12))


def _db(tmp_path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "research.db"
    create_tables(db_path)
    monkeypatch.setattr(nightly, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(nightly, "PROJECT_ROOT", tmp_path)
    return db_path


def _signal(db_path, symbol="ACME", created_at=NOW, signal_type="HOLD"):
    return insert_signal(
        db_path,
        item_id=None,
        symbol=symbol,
        signal=signal_type,
        confidence=0.8,
        horizon="1d",
        rationale="Fictional test signal.",
        model_name="fake-model",
        prompt_version="v1",
        created_at=created_at.isoformat(),
    )


def _event(db_path, approved, reason="test rejection"):
    trade = ProposedTrade("ACME", "BUY", 100.0, 0.8, NOW)
    decision = RiskDecision(approved, 1 if approved else 0, [] if approved else [reason])
    log_risk_decision(db_path, trade, decision, created_at=NOW.isoformat())


def _run(db_path, provider=Prices()):
    return nightly.run_nightly(db_path, provider, NOW)


def test_zero_signals_are_reported(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)

    report = _run(db_path)

    assert "BUY=0, SELL=0, HOLD=0" in report
    assert "No signals were logged today." in report


def test_no_signal_warning_does_not_trigger_with_signal(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)
    _signal(db_path)

    report = _run(db_path)

    assert "No signals were logged today." not in report


def test_symbol_concentration_warning_triggers_and_does_not(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)
    _signal(db_path, "ACME")
    _signal(db_path, "ACME")
    _signal(db_path, "OTHER")
    report = _run(db_path)
    assert "More than 50% of today's signals are for ACME." in report

    db_path = _db(tmp_path / "balanced", monkeypatch)
    _signal(db_path, "ACME")
    _signal(db_path, "OTHER")
    assert "More than 50%" not in _run(db_path)


def test_stale_price_warning_triggers_and_does_not(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)
    stale = Prices(NOW - timedelta(days=1, seconds=1))
    assert "latest price data is older than 1 day" in _run(db_path, stale)

    db_path = _db(tmp_path / "fresh", monkeypatch)
    fresh = Prices(NOW - timedelta(days=1))
    assert "latest price data is older than 1 day" not in _run(db_path, fresh)


def test_kill_switch_warning_triggers_and_does_not(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)
    (tmp_path / "KILL").write_text("", encoding="utf-8")
    assert "The KILL switch file exists." in _run(db_path)

    (tmp_path / "KILL").unlink()
    db_path = _db(tmp_path / "clear", monkeypatch)
    assert "The KILL switch file exists." not in _run(db_path)


def test_daily_loss_warning_triggers_and_does_not(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)
    _event(db_path, False, "daily loss cap has been hit")
    assert "The daily loss cap was hit today." in _run(db_path)

    db_path = _db(tmp_path / "clear", monkeypatch)
    _event(db_path, False, "confidence is below the minimum")
    assert "The daily loss cap was hit today." not in _run(db_path)


def test_rejection_ratio_warning_triggers_and_does_not(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)
    for _ in range(3):
        _event(db_path, False)
    _event(db_path, True)
    warning = "Risk rejections outnumber approvals by 3 to 1 or more today."
    assert warning in _run(db_path)

    db_path = _db(tmp_path / "balanced", monkeypatch)
    _event(db_path, False)
    _event(db_path, False)
    _event(db_path, True)
    assert warning not in _run(db_path)


def test_same_day_rerun_replaces_report_without_duplicate_outcomes(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)
    start = NOW - timedelta(days=1)
    _signal(db_path, created_at=start, signal_type="BUY")
    provider = Prices(NOW)

    first = nightly.run_nightly(db_path, provider, NOW)
    second = nightly.run_nightly(db_path, provider, NOW)

    with sqlite3.connect(db_path) as connection:
        report_count = connection.execute("SELECT COUNT(*) FROM daily_reports").fetchone()[0]
        outcome_count = connection.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    assert first == second
    assert report_count == outcome_count == 1
    assert (tmp_path / "reports" / f"{NOW.date().isoformat()}.txt").read_text() == second


def test_error_is_reported_saved_and_not_raised(tmp_path, monkeypatch):
    db_path = _db(tmp_path, monkeypatch)

    report = _run(db_path, BadTimestampPrices())

    assert "ERROR: Nightly report failed" in report
    with sqlite3.connect(db_path) as connection:
        saved = connection.execute("SELECT text FROM daily_reports").fetchone()[0]
    assert saved == report
    assert (tmp_path / "reports" / f"{NOW.date().isoformat()}.txt").read_text() == report


def test_nightly_imports_no_network_ai_or_broker_sdks():
    tree = ast.parse(Path(nightly.__file__).read_text(encoding="utf-8"))
    forbidden = {
        "anthropic", "openai", "requests", "httpx", "alpaca", "alpaca_trade_api",
        "ib_insync", "ibapi", "ccxt", "robin_stocks", "tda_api", "schwab_py",
    }
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden)
