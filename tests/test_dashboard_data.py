import ast
import sqlite3
from pathlib import Path

import pytest

from src.dashboard_data import (
    CONFIDENCE_COLUMNS,
    DAILY_REPORT_COLUMNS,
    LATEST_SIGNAL_COLUMNS,
    ORDER_COLUMNS,
    OUTCOME_COLUMNS,
    RISK_EVENT_COLUMNS,
    SIGNAL_COUNT_COLUMNS,
    _readonly_connection,
    confidence_bucket_summary,
    health,
    latest_daily_report,
    latest_signals,
    orders,
    resolved_outcomes,
    risk_events,
    signal_counts,
)
from src.db import create_tables, insert_signal


DATA_FUNCTIONS = {
    latest_signals: LATEST_SIGNAL_COLUMNS,
    signal_counts: SIGNAL_COUNT_COLUMNS,
    orders: ORDER_COLUMNS,
    risk_events: RISK_EVENT_COLUMNS,
    resolved_outcomes: OUTCOME_COLUMNS,
    confidence_bucket_summary: CONFIDENCE_COLUMNS,
    latest_daily_report: DAILY_REPORT_COLUMNS,
}


def test_readonly_connection_rejects_insert(tmp_path):
    db_path = tmp_path / "research.db"
    create_tables(db_path)

    with _readonly_connection(db_path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(
                "INSERT INTO daily_reports (date, text) VALUES (?, ?)",
                ("2026-01-01", "not allowed"),
            )


def test_missing_database_returns_empty_data_without_creating_file(tmp_path):
    db_path = tmp_path / "missing.db"

    frames = [function(db_path) for function in DATA_FUNCTIONS]

    assert all(list(frame.columns) == columns for frame, columns in zip(frames, DATA_FUNCTIONS.values()))
    for function, frame in zip(DATA_FUNCTIONS, frames):
        if function == signal_counts:
            assert frame["count"].tolist() == [0, 0, 0]
        elif function == confidence_bucket_summary:
            assert frame["summary"].str.contains("insufficient data").all()
        else:
            assert frame.empty
    assert signal_counts(db_path)["count"].tolist() == [0, 0, 0]
    assert not db_path.exists()
    status = health(db_path, tmp_path / "KILL")
    assert status["database_size_bytes"] == 0
    assert status["last_signal_time"] is None


def _sample_database(db_path):
    create_tables(db_path)
    buy_id = insert_signal(
        db_path,
        item_id=None,
        symbol="ACME",
        signal="BUY",
        confidence=0.65,
        horizon="1d",
        rationale="Fictional growth.",
        model_name="fake",
        prompt_version="v1",
        created_at="2026-01-01T10:00:00+00:00",
    )
    hold_id = insert_signal(
        db_path,
        item_id=None,
        symbol="BETA",
        signal="HOLD",
        confidence=0.85,
        horizon="5d",
        rationale="Fictional neutral news.",
        model_name="fake",
        prompt_version="v1",
        created_at="2026-01-02T10:00:00+00:00",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO risk_events (symbol, side, price, confidence, price_timestamp, approved, qty, reasons) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ACME", "BUY", 10.0, 0.65, "2026-01-01T10:00:00+00:00", 0, 0, '["confidence too low"]'),
        )
        connection.execute(
            "INSERT INTO orders (client_order_id, signal_id, symbol, side, qty, fill_price, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sample-1", buy_id, "ACME", "BUY", 2, 10.0, "filled"),
        )
        connection.execute(
            "INSERT INTO outcomes (signal_id, horizon_checked, price_at_check, return_pct, no_trade) "
            "VALUES (?, ?, ?, ?, ?)",
            (buy_id, "1d", 11.0, 0.1, 0),
        )
        connection.execute(
            "INSERT INTO outcomes (signal_id, horizon_checked, price_at_check, return_pct, no_trade) "
            "VALUES (?, ?, ?, ?, ?)",
            (hold_id, "5d", 12.0, 0.0, 1),
        )
        connection.execute(
            "INSERT INTO daily_reports (date, text) VALUES (?, ?)",
            ("2026-01-02", "Nightly review sample."),
        )


def test_data_functions_return_expected_columns_on_empty_database(tmp_path):
    db_path = tmp_path / "empty.db"
    create_tables(db_path)

    for function, columns in DATA_FUNCTIONS.items():
        frame = function(db_path)
        assert list(frame.columns) == columns
        if function == confidence_bucket_summary:
            assert frame["summary"].str.contains("insufficient data").all()
        elif function != signal_counts:
            assert frame.empty


def test_data_functions_return_expected_columns_and_values_on_sample(tmp_path):
    db_path = tmp_path / "sample.db"
    _sample_database(db_path)

    for function, columns in DATA_FUNCTIONS.items():
        assert list(function(db_path).columns) == columns

    assert latest_signals(db_path).iloc[0]["symbol"] == "BETA"
    assert signal_counts(db_path)["count"].tolist() == [1, 0, 1]
    assert orders(db_path).iloc[0]["status"] == "filled"
    assert risk_events(db_path).iloc[0]["reasons"] == ["confidence too low"]
    assert len(resolved_outcomes(db_path)) == 2
    summary = confidence_bucket_summary(db_path)
    assert "insufficient data" in summary.iloc[0]["summary"]
    assert latest_daily_report(db_path).iloc[0]["text"] == "Nightly review sample."
    status = health(db_path, tmp_path / "KILL")
    assert status["last_signal_time"] == "2026-01-02T10:00:00+00:00"
    assert status["last_nightly_report_time"] == "2026-01-02"
    assert status["database_size_bytes"] > 0


def test_data_module_imports_no_network_or_broker_sdks():
    forbidden = {"anthropic", "openai", "requests", "alpaca", "ib_insync", "ibapi", "ccxt"}
    for relative_path in ("src/dashboard_data.py", "src/dashboard.py"):
        tree = ast.parse(Path(relative_path).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden)
