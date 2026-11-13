import sqlite3
from contextlib import closing

from .db import DatabasePath, create_tables


BUCKETS = (("0.6-0.7", 0.6, 0.7), ("0.7-0.8", 0.7, 0.8), ("0.8-1.0", 0.8, 1.0))
SIGNAL_TYPES = ("BUY", "SELL", "HOLD")
MIN_BUCKET_OUTCOMES = 30


def summarize_confidence_outcomes(rows):
    summaries = []
    for signal_type in ("BUY", "SELL"):
        for label, lower, upper in BUCKETS:
            returns = [
                row[2]
                for row in rows
                if row[0] == signal_type
                and lower <= row[1]
                and (row[1] < upper or (upper == 1.0 and row[1] <= upper))
            ]
            count = len(returns)
            if count < MIN_BUCKET_OUTCOMES:
                summary = f"insufficient data (n={count})"
                average = None
                hit_rate = None
            else:
                average = sum(returns) / count
                hit_rate = sum(value > 0 for value in returns) / count
                summary = f"n={count}, average return={average:.2%}, hit rate={hit_rate:.1%}"
            summaries.append(
                {
                    "signal": signal_type,
                    "confidence_bucket": label,
                    "resolved_count": count,
                    "average_return": average,
                    "hit_rate": hit_rate,
                    "summary": summary,
                }
            )
    return summaries


def print_report(db_path: DatabasePath) -> None:
    create_tables(db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        counts = dict(
            connection.execute("SELECT signal, COUNT(*) FROM signals GROUP BY signal").fetchall()
        )
        resolved = connection.execute(
            """
            SELECT s.signal, s.confidence, o.return_pct
            FROM signals AS s
            JOIN outcomes AS o
                ON o.signal_id = s.id AND o.horizon_checked = s.horizon
            WHERE o.no_trade = ? AND s.signal IN (?, ?)
            """,
            (0, "BUY", "SELL"),
        ).fetchall()

    print("Signal counts:")
    for signal_type in SIGNAL_TYPES:
        print(f"  {signal_type}: {counts.get(signal_type, 0)}")

    print("Resolved outcomes by confidence:")
    for summary in summarize_confidence_outcomes(resolved):
        print(f"  {summary['signal']} {summary['confidence_bucket']}: {summary['summary']}")
