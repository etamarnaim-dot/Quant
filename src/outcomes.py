import logging
import math
from contextlib import closing
from datetime import datetime, timedelta, timezone

from .db import DatabasePath, _connect, create_tables
from .prices import PriceProvider


logger = logging.getLogger(__name__)
HORIZONS = {"1d": timedelta(days=1), "5d": timedelta(days=5), "20d": timedelta(days=20)}


def _signal_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def process_outcomes(
    db_path: DatabasePath,
    price_provider: PriceProvider,
    now: datetime,
) -> int:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    now_utc = now.astimezone(timezone.utc)
    create_tables(db_path)
    processed = 0

    with closing(_connect(db_path)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            signals = connection.execute(
                """
                SELECT s.id, s.symbol, s.signal, s.horizon, s.created_at
                FROM signals AS s
                LEFT JOIN outcomes AS o
                    ON o.signal_id = s.id AND o.horizon_checked = s.horizon
                WHERE o.id IS NULL
                ORDER BY s.id
                """
            ).fetchall()

            for signal in signals:
                horizon = HORIZONS.get(signal["horizon"])
                if horizon is None:
                    logger.warning("Skipping signal %s: unsupported horizon", signal["id"])
                    continue
                try:
                    start_time = _signal_time(signal["created_at"])
                except (TypeError, ValueError) as error:
                    logger.warning("Skipping signal %s: invalid signal time: %s", signal["id"], error)
                    continue
                end_time = start_time + horizon
                if end_time > now_utc:
                    continue

                try:
                    start_price = price_provider.get_price(signal["symbol"], start_time)
                    end_price = price_provider.get_price(signal["symbol"], end_time)
                except Exception as error:
                    logger.warning("Skipping signal %s: price lookup failed: %s", signal["id"], error)
                    continue
                if start_price is None or end_price is None:
                    missing = "signal-time" if start_price is None else "horizon"
                    if start_price is None and end_price is None:
                        missing = "signal-time and horizon"
                    logger.warning("Skipping signal %s: missing %s price", signal["id"], missing)
                    continue
                try:
                    start_price = float(start_price)
                    end_price = float(end_price)
                except (TypeError, ValueError):
                    logger.warning("Skipping signal %s: price is not numeric", signal["id"])
                    continue
                if not all(math.isfinite(price) and price > 0 for price in (start_price, end_price)):
                    logger.warning("Skipping signal %s: price is not positive and finite", signal["id"])
                    continue

                signal_type = signal["signal"]
                if signal_type == "HOLD":
                    return_pct, no_trade = 0.0, 1
                elif signal_type == "BUY":
                    return_pct, no_trade = (end_price - start_price) / start_price, 0
                elif signal_type == "SELL":
                    return_pct, no_trade = (start_price - end_price) / start_price, 0
                else:
                    logger.warning("Skipping signal %s: unsupported signal type", signal["id"])
                    continue

                connection.execute(
                    """
                    INSERT INTO outcomes (
                        signal_id, horizon_checked, price_at_check, return_pct,
                        no_trade, checked_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal["id"],
                        signal["horizon"],
                        end_price,
                        return_pct,
                        no_trade,
                        now_utc.isoformat().replace("+00:00", "Z"),
                    ),
                )
                processed += 1
    return processed
