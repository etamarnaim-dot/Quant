from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from .report import summarize_confidence_outcomes


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "research.db"
KILL_SWITCH_PATH = PROJECT_ROOT / "KILL"

LATEST_SIGNAL_COLUMNS = [
    "id", "item_id", "symbol", "signal", "confidence", "horizon", "rationale",
    "model_name", "prompt_version", "price_at_signal", "created_at",
]
SIGNAL_COUNT_COLUMNS = ["signal", "count"]
ORDER_COLUMNS = [
    "id", "client_order_id", "signal_id", "symbol", "side", "qty",
    "fill_price", "status", "created_at",
]
RISK_EVENT_COLUMNS = [
    "id", "symbol", "side", "price", "confidence", "price_timestamp",
    "approved", "qty", "reasons", "created_at",
]
OUTCOME_COLUMNS = [
    "id", "signal_id", "symbol", "signal", "confidence", "horizon_checked",
    "price_at_check", "return_pct", "no_trade", "checked_at",
]
CONFIDENCE_COLUMNS = [
    "signal", "confidence_bucket", "resolved_count", "average_return", "hit_rate", "summary",
]
DAILY_REPORT_COLUMNS = ["date", "text"]


@contextmanager
def _readonly_connection(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        yield connection


def _query_frame(
    db_path: str | Path,
    query: str,
    columns: list[str],
    params: tuple[object, ...] = (),
) -> pd.DataFrame:
    if not Path(db_path).is_file():
        return pd.DataFrame(columns=columns)
    try:
        with _readonly_connection(db_path) as connection:
            return pd.read_sql_query(query, connection, params=params)
    except sqlite3.OperationalError as error:
        if "no such table" in str(error).lower():
            return pd.DataFrame(columns=columns)
        raise


def latest_signals(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    return _query_frame(
        db_path,
        f"SELECT {', '.join(LATEST_SIGNAL_COLUMNS)} FROM signals "
        "ORDER BY created_at DESC, id DESC LIMIT 200",
        LATEST_SIGNAL_COLUMNS,
    )


def signal_counts(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    counts = _query_frame(
        db_path,
        "SELECT signal, COUNT(*) AS count FROM signals GROUP BY signal",
        SIGNAL_COUNT_COLUMNS,
    )
    values = dict(zip(counts["signal"], counts["count"])) if not counts.empty else {}
    return pd.DataFrame(
        [(signal, int(values.get(signal, 0))) for signal in ("BUY", "SELL", "HOLD")],
        columns=SIGNAL_COUNT_COLUMNS,
    )


def orders(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    return _query_frame(
        db_path,
        f"SELECT {', '.join(ORDER_COLUMNS)} FROM orders ORDER BY id DESC",
        ORDER_COLUMNS,
    )


def risk_events(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    frame = _query_frame(
        db_path,
        f"SELECT {', '.join(RISK_EVENT_COLUMNS)} FROM risk_events ORDER BY id DESC",
        RISK_EVENT_COLUMNS,
    )
    if not frame.empty:
        def parse_reasons(value: str) -> object:
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value

        frame["reasons"] = frame["reasons"].map(parse_reasons)
    return frame


def resolved_outcomes(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    return _query_frame(
        db_path,
        """
        SELECT o.id, o.signal_id, s.symbol, s.signal, s.confidence,
               o.horizon_checked, o.price_at_check, o.return_pct,
               o.no_trade, o.checked_at
        FROM outcomes AS o
        JOIN signals AS s ON s.id = o.signal_id
        ORDER BY o.id DESC
        """,
        OUTCOME_COLUMNS,
    )


def confidence_bucket_summary(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    rows = _query_frame(
        db_path,
        """
        SELECT s.signal, s.confidence, o.return_pct
        FROM signals AS s
        JOIN outcomes AS o ON o.signal_id = s.id AND o.horizon_checked = s.horizon
        WHERE s.signal IN (?, ?) AND o.no_trade = ?
        """,
        ["signal", "confidence", "return_pct"],
        ("BUY", "SELL", 0),
    )
    summaries = summarize_confidence_outcomes(list(rows.itertuples(index=False, name=None)))
    return pd.DataFrame(summaries, columns=CONFIDENCE_COLUMNS)


def latest_daily_report(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    return _query_frame(
        db_path,
        "SELECT date, text FROM daily_reports ORDER BY date DESC LIMIT 1",
        DAILY_REPORT_COLUMNS,
    )


def health(
    db_path: str | Path = DEFAULT_DB_PATH,
    kill_path: str | Path = KILL_SWITCH_PATH,
) -> dict[str, object]:
    path = Path(db_path).resolve()
    signals = latest_signals(path)
    reports = latest_daily_report(path)
    return {
        "last_signal_time": None if signals.empty else signals.iloc[0]["created_at"],
        "last_nightly_report_time": None if reports.empty else reports.iloc[0]["date"],
        "kill_switch_exists": Path(kill_path).exists(),
        "database_path": str(path),
        "database_size_bytes": path.stat().st_size if path.is_file() else 0,
    }
