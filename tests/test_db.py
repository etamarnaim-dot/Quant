import sqlite3

import pytest

from src.db import create_tables, fetch_signals, insert_signal


def _insert_raw_item(db_path: str) -> int:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO raw_items (source, fetched_at, text) VALUES (?, ?, ?)",
            ("test", "2026-01-01T00:00:00Z", "Sample news"),
        )
        return int(cursor.lastrowid)


def test_create_tables_is_idempotent(tmp_path):
    db_path = tmp_path / "research.db"

    create_tables(db_path)
    create_tables(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
            )
        }

    assert {
        "raw_items",
        "signals",
        "outcomes",
        "risk_events",
        "orders",
        "daily_reports",
    } <= tables


def test_insert_and_fetch_signal_including_hold(tmp_path):
    db_path = tmp_path / "research.db"
    create_tables(db_path)
    item_id = _insert_raw_item(str(db_path))

    signal_id = insert_signal(
        db_path,
        item_id=item_id,
        symbol="ACME",
        signal="HOLD",
        confidence=0.72,
        horizon="5d",
        rationale="News said 'wait and see'.",
        model_name="fake-model",
        prompt_version="v1",
        price_at_signal=12.5,
        created_at="2026-01-02T00:00:00Z",
    )

    fetched = fetch_signals(db_path, limit=1)

    assert len(fetched) == 1
    assert fetched[0]["id"] == signal_id
    assert fetched[0]["signal"] == "HOLD"
    assert fetched[0]["rationale"] == "News said 'wait and see'."
    assert fetched[0]["created_at"] == "2026-01-02T00:00:00Z"


def test_insert_signal_rejects_unknown_item(tmp_path):
    db_path = tmp_path / "research.db"
    create_tables(db_path)

    with pytest.raises(sqlite3.IntegrityError):
        insert_signal(
            db_path,
            item_id=999,
            symbol="ACME",
            signal="BUY",
            confidence=0.5,
            horizon="1d",
            rationale="Sample rationale.",
            model_name="fake-model",
            prompt_version="v1",
        )
