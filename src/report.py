import sqlite3
from contextlib import closing

from .db import DatabasePath, create_tables


BUCKETS = (("0.6-0.7", 0.6, 0.7), ("0.7-0.8", 0.7, 0.8), ("0.8-1.0", 0.8, 1.0))
SIGNAL_TYPES = ("BUY", "SELL", "HOLD")


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
    for signal_type in ("BUY", "SELL"):
        for label, lower, upper in BUCKETS:
            returns = [
                row[2]
                for row in resolved
                if row[0] == signal_type
                and lower <= row[1]
                and (row[1] < upper or (upper == 1.0 and row[1] <= upper))
            ]
            if len(returns) < 30:
                print(f"  {signal_type} {label}: insufficient data (n={len(returns)})")
                continue
            average = sum(returns) / len(returns)
            hit_rate = sum(value > 0 for value in returns) / len(returns)
            print(
                f"  {signal_type} {label}: n={len(returns)}, "
                f"average return={average:.2%}, hit rate={hit_rate:.1%}"
            )
