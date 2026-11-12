import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.db import create_tables, insert_signal
from src.outcomes import process_outcomes
from src.prices import CsvPriceProvider
from src.report import print_report


UTC = timezone.utc
SIGNAL_TIME = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
HORIZON_TIME = SIGNAL_TIME + timedelta(days=1)


def _provider(tmp_path, quotes):
    path = tmp_path / "prices.csv"
    lines = ["timestamp,symbol,price"]
    lines.extend(
        f"{timestamp.isoformat().replace('+00:00', 'Z')},{symbol},{price}"
        for timestamp, symbol, price in quotes
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return CsvPriceProvider(path)


def _database(tmp_path):
    db_path = tmp_path / "research.db"
    create_tables(db_path)
    return db_path


def _insert_signal(db_path, signal="BUY", confidence=0.65):
    return insert_signal(
        db_path,
        item_id=None,
        symbol="ACME",
        signal=signal,
        confidence=confidence,
        horizon="1d",
        rationale="Fictional test signal.",
        model_name="fake-model",
        prompt_version="v1",
        created_at=SIGNAL_TIME.isoformat().replace("+00:00", "Z"),
    )


def _outcomes(db_path):
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            "SELECT signal_id, return_pct, no_trade FROM outcomes ORDER BY id"
        ).fetchall()


def test_csv_provider_never_looks_ahead(tmp_path):
    provider = _provider(
        tmp_path,
        [
            (SIGNAL_TIME, "ACME", 100.0),
            (SIGNAL_TIME + timedelta(minutes=1), "ACME", 105.0),
        ],
    )

    assert provider.get_price("ACME", SIGNAL_TIME - timedelta(seconds=1)) is None
    assert provider.get_price("ACME", SIGNAL_TIME + timedelta(seconds=30)) == 100.0
    assert provider.get_price("ACME", SIGNAL_TIME + timedelta(minutes=1)) == 105.0


def test_horizon_that_has_not_elapsed_is_not_recorded(tmp_path):
    db_path = _database(tmp_path)
    _insert_signal(db_path)
    provider = _provider(
        tmp_path,
        [(SIGNAL_TIME, "ACME", 100.0), (HORIZON_TIME, "ACME", 110.0)],
    )

    processed = process_outcomes(db_path, provider, HORIZON_TIME - timedelta(seconds=1))

    assert processed == 0
    assert _outcomes(db_path) == []


class _MissingHorizonPrice:
    def get_price(self, symbol, when):
        return 100.0 if when == SIGNAL_TIME else None


def test_missing_price_is_skipped_and_logged(tmp_path, caplog):
    db_path = _database(tmp_path)
    _insert_signal(db_path)

    with caplog.at_level(logging.WARNING):
        processed = process_outcomes(db_path, _MissingHorizonPrice(), HORIZON_TIME)

    assert processed == 0
    assert _outcomes(db_path) == []
    assert "missing horizon price" in caplog.text


@pytest.mark.parametrize(
    ("signal_type", "later_price", "expected_return"),
    [("BUY", 110.0, 0.1), ("SELL", 90.0, 0.1)],
)
def test_directional_return_is_recorded(tmp_path, signal_type, later_price, expected_return):
    db_path = _database(tmp_path)
    _insert_signal(db_path, signal=signal_type)
    provider = _provider(
        tmp_path,
        [(SIGNAL_TIME, "ACME", 100.0), (HORIZON_TIME, "ACME", later_price)],
    )

    assert process_outcomes(db_path, provider, HORIZON_TIME) == 1
    assert _outcomes(db_path)[0][1] == pytest.approx(expected_return)
    assert _outcomes(db_path)[0][2] == 0


def test_hold_is_recorded_as_no_trade(tmp_path):
    db_path = _database(tmp_path)
    _insert_signal(db_path, signal="HOLD")
    provider = _provider(
        tmp_path,
        [(SIGNAL_TIME, "ACME", 100.0), (HORIZON_TIME, "ACME", 110.0)],
    )

    assert process_outcomes(db_path, provider, HORIZON_TIME) == 1
    assert _outcomes(db_path)[0][1:] == (0.0, 1)


def test_repeated_processing_does_not_duplicate_outcomes(tmp_path):
    db_path = _database(tmp_path)
    _insert_signal(db_path)
    provider = _provider(
        tmp_path,
        [(SIGNAL_TIME, "ACME", 100.0), (HORIZON_TIME, "ACME", 101.0)],
    )

    assert process_outcomes(db_path, provider, HORIZON_TIME) == 1
    assert process_outcomes(db_path, provider, HORIZON_TIME) == 0
    assert len(_outcomes(db_path)) == 1


def test_report_marks_small_buckets_as_insufficient(tmp_path, capsys):
    db_path = _database(tmp_path)
    _insert_signal(db_path, confidence=0.65)
    provider = _provider(
        tmp_path,
        [(SIGNAL_TIME, "ACME", 100.0), (HORIZON_TIME, "ACME", 101.0)],
    )
    process_outcomes(db_path, provider, HORIZON_TIME)

    print_report(db_path)

    assert "BUY 0.6-0.7: insufficient data (n=1)" in capsys.readouterr().out
