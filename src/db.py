from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .risk import ProposedTrade, RiskDecision


DatabasePath = str | Path


def _connect(db_path: DatabasePath) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_tables(db_path: DatabasePath) -> None:
    with closing(_connect(db_path)) as connection:
        with connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_items (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    text TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY,
                    item_id INTEGER,
                    symbol TEXT NOT NULL,
                    signal TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL', 'HOLD')),
                    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                    horizon TEXT NOT NULL CHECK (horizon IN ('1d', '5d', '20d')),
                    rationale TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    price_at_signal REAL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (item_id) REFERENCES raw_items (id)
                );

                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY,
                    signal_id INTEGER NOT NULL,
                    horizon_checked TEXT NOT NULL,
                    price_at_check REAL NOT NULL,
                    return_pct REAL NOT NULL,
                    no_trade INTEGER NOT NULL DEFAULT 0 CHECK (no_trade IN (0, 1)),
                    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (signal_id) REFERENCES signals (id)
                );

                CREATE TABLE IF NOT EXISTS risk_events (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT,
                    side TEXT,
                    price REAL,
                    confidence REAL,
                    price_timestamp TEXT,
                    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
                    qty INTEGER NOT NULL,
                    reasons TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    signal_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    fill_price REAL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (signal_id) REFERENCES signals (id)
                );

                CREATE TABLE IF NOT EXISTS daily_reports (
                    date TEXT PRIMARY KEY,
                    text TEXT NOT NULL
                );
                """
            )
            outcome_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(outcomes)")
            }
            if "no_trade" not in outcome_columns:
                connection.execute(
                    "ALTER TABLE outcomes ADD COLUMN no_trade INTEGER NOT NULL "
                    "DEFAULT 0 CHECK (no_trade IN (0, 1))"
                )


def insert_signal(
    db_path: DatabasePath,
    *,
    item_id: int | None,
    symbol: str,
    signal: str,
    confidence: float,
    horizon: str,
    rationale: str,
    model_name: str,
    prompt_version: str,
    price_at_signal: float | None = None,
    created_at: str | None = None,
) -> int:
    with closing(_connect(db_path)) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO signals (
                    item_id, symbol, signal, confidence, horizon, rationale,
                    model_name, prompt_version, price_at_signal, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    item_id,
                    symbol,
                    signal,
                    confidence,
                    horizon,
                    rationale,
                    model_name,
                    prompt_version,
                    price_at_signal,
                    created_at,
                ),
            )
            return int(cursor.lastrowid)


def log_order(
    db_path: DatabasePath,
    *,
    client_order_id: str,
    signal_id: int,
    symbol: str,
    side: str,
    qty: int,
    fill_price: float | None,
    status: str,
    created_at: str | None = None,
) -> int:
    with closing(_connect(db_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO orders (
                    client_order_id, signal_id, symbol, side, qty,
                    fill_price, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                ON CONFLICT (client_order_id) DO NOTHING
                """,
                (
                    client_order_id,
                    signal_id,
                    symbol,
                    side,
                    qty,
                    fill_price,
                    status,
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT id FROM orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            return int(row["id"])


def fetch_order(
    db_path: DatabasePath,
    client_order_id: str,
) -> dict[str, object] | None:
    with closing(_connect(db_path)) as connection:
        row = connection.execute(
            "SELECT * FROM orders WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def save_daily_report(db_path: DatabasePath, report_date: str, text: str) -> None:
    with closing(_connect(db_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO daily_reports (date, text) VALUES (?, ?)
                ON CONFLICT (date) DO UPDATE SET text = excluded.text
                """,
                (report_date, text),
            )


def fetch_signals(
    db_path: DatabasePath,
    *,
    limit: int | None = None,
) -> list[dict[str, object]]:
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    query = "SELECT * FROM signals ORDER BY id"
    parameters: tuple[int, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)

    with closing(_connect(db_path)) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def log_risk_decision(
    db_path: DatabasePath,
    trade: ProposedTrade,
    decision: RiskDecision,
    *,
    created_at: str | None = None,
) -> int:
    def audit_value(value: object) -> object:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, float) and not math.isfinite(value):
            return repr(value)
        if isinstance(value, (str, int, float)):
            return value
        return str(value)

    with closing(_connect(db_path)) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO risk_events (
                    symbol, side, price, confidence, price_timestamp,
                    approved, qty, reasons, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    audit_value(trade.symbol),
                    audit_value(trade.side),
                    audit_value(trade.price),
                    audit_value(trade.confidence),
                    audit_value(trade.price_timestamp),
                    int(decision.approved),
                    decision.qty,
                    json.dumps(decision.reasons),
                    created_at,
                ),
            )
            return int(cursor.lastrowid)
