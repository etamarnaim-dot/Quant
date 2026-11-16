from __future__ import annotations

from datetime import datetime, timezone

from src.research_runner import summarize_strategy_drift


def test_summarize_strategy_drift_detects_recent_decline(tmp_path):
    db_path = tmp_path / "research.db"
    connection = __import__("sqlite3").connect(db_path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY, signal TEXT, confidence REAL, model_name TEXT, created_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS outcomes (id INTEGER PRIMARY KEY, signal_id INTEGER, return_pct REAL, no_trade INTEGER)"
    )

    connection.execute(
        "INSERT INTO signals (signal, confidence, model_name, created_at) VALUES (?, ?, ?, ?)",
        ("BUY", 0.88, "momentum", "2026-01-01T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO signals (signal, confidence, model_name, created_at) VALUES (?, ?, ?, ?)",
        ("BUY", 0.86, "momentum", "2026-01-03T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO signals (signal, confidence, model_name, created_at) VALUES (?, ?, ?, ?)",
        ("SELL", 0.64, "momentum", "2026-01-11T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO outcomes (signal_id, return_pct, no_trade) VALUES (?, ?, ?)",
        (1, 0.11, 0),
    )
    connection.execute(
        "INSERT INTO outcomes (signal_id, return_pct, no_trade) VALUES (?, ?, ?)",
        (2, 0.09, 0),
    )
    connection.execute(
        "INSERT INTO outcomes (signal_id, return_pct, no_trade) VALUES (?, ?, ?)",
        (3, -0.08, 0),
    )
    connection.commit()
    connection.close()

    summary = summarize_strategy_drift(db_path)

    assert len(summary) >= 1
    assert summary[0]["model_name"] == "momentum"
    assert summary[0]["recent_signal_count"] >= 1
    assert summary[0]["confidence_delta"] <= 0.0
