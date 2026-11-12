import json
import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from math import nan
from zoneinfo import ZoneInfo

import pytest

from src.db import create_tables, log_risk_decision
from src.risk import AccountState, ProposedTrade, RiskConfig, RiskDecision, check_trade


NEW_YORK = ZoneInfo("America/New_York")
BASE_NOW = datetime(2026, 1, 5, 10, 0, tzinfo=NEW_YORK)


def _trade(**changes):
    values = {
        "symbol": "ACME",
        "side": "BUY",
        "price": 100.0,
        "confidence": 0.8,
        "price_timestamp": BASE_NOW,
    }
    values.update(changes)
    return ProposedTrade(**values)


def _account(**changes):
    values = {
        "equity": 10_000.0,
        "day_start_equity": 10_000.0,
        "cash": 10_000.0,
        "positions": {},
        "as_of": BASE_NOW,
    }
    values.update(changes)
    return AccountState(**values)


def _config(**changes):
    values = {"symbol_whitelist": frozenset({"ACME"})}
    values.update(changes)
    return RiskConfig(**values)


def _check(trade=None, account=None, config=None, now=BASE_NOW):
    return check_trade(
        _trade() if trade is None else trade,
        _account() if account is None else account,
        _config() if config is None else config,
        now,
    )


def test_approves_largest_quantity_at_position_limit():
    result = _check()

    assert result.approved is True
    assert result.qty == 5
    assert result.reasons == []


def test_exact_position_limit_is_allowed():
    result = _check(
        trade=_trade(price=2.5),
        account=_account(
            equity=100.0,
            day_start_equity=100.0,
            cash=100.0,
            positions={"ACME": 2.5},
        ),
        config=_config(max_position_pct=0.05),
    )

    assert result.approved is True
    assert result.qty == 1


def test_one_cent_over_position_limit_is_rejected():
    result = _check(
        trade=_trade(price=0.02),
        account=_account(
            equity=100.0,
            day_start_equity=100.0,
            cash=100.0,
            positions={"ACME": 4.99},
        ),
        config=_config(max_position_pct=0.05),
    )

    assert result.approved is False
    assert result.qty == 0
    assert "risk limits" in result.reasons[0]


def test_total_exposure_limit_is_enforced():
    result = _check(
        trade=_trade(price=0.02),
        account=_account(
            equity=100.0,
            day_start_equity=100.0,
            cash=100.0,
            positions={"OTHER": 9.99},
        ),
        config=_config(max_position_pct=0.20, max_total_exposure_pct=0.10),
    )

    assert result.approved is False
    assert result.qty == 0


def test_buy_is_limited_by_cash():
    result = _check(
        trade=_trade(price=60.0),
        account=_account(equity=1_000.0, day_start_equity=1_000.0, cash=50.0),
    )

    assert result.approved is False
    assert "cash" in result.reasons[0]


@pytest.mark.parametrize(
    "config",
    [
        RiskConfig(),
        RiskConfig(symbol_whitelist=frozenset({"OTHER"})),
    ],
)
def test_symbol_must_be_whitelisted(config):
    result = _check(config=config)

    assert result.approved is False
    assert "whitelist" in result.reasons[0]


def test_confidence_below_minimum_is_rejected_and_threshold_is_inclusive():
    rejected = _check(trade=_trade(confidence=0.59))
    accepted = _check(trade=_trade(confidence=0.6))

    assert rejected.approved is False
    assert "confidence" in rejected.reasons[0]
    assert accepted.approved is True


@pytest.mark.parametrize("field", ["price_timestamp", "account_timestamp"])
def test_stale_data_is_rejected(field):
    stale = BASE_NOW - timedelta(seconds=61)
    if field == "price_timestamp":
        result = _check(trade=_trade(price_timestamp=stale))
    else:
        result = _check(account=_account(as_of=stale))

    assert result.approved is False
    assert "stale" in result.reasons[0]


def test_data_exactly_at_max_age_is_accepted():
    timestamp = BASE_NOW - timedelta(seconds=60)

    result = _check(
        trade=_trade(price_timestamp=timestamp),
        account=_account(as_of=timestamp),
    )

    assert result.approved is True


def test_weekend_is_rejected():
    saturday = datetime(2026, 1, 10, 10, 0, tzinfo=NEW_YORK)

    result = _check(
        trade=_trade(price_timestamp=saturday),
        account=_account(as_of=saturday),
        now=saturday,
    )

    assert result.approved is False
    assert "market hours" in result.reasons[0]


def test_1559_is_open_and_1600_is_closed():
    before_close = datetime(2026, 1, 5, 15, 59, tzinfo=NEW_YORK)
    at_close = datetime(2026, 1, 5, 16, 0, tzinfo=NEW_YORK)

    open_result = _check(
        trade=_trade(price_timestamp=before_close),
        account=_account(as_of=before_close),
        now=before_close,
    )
    closed_result = _check(
        trade=_trade(price_timestamp=at_close),
        account=_account(as_of=at_close),
        now=at_close,
    )

    assert open_result.approved is True
    assert closed_result.approved is False
    assert "market hours" in closed_result.reasons[0]


def test_daily_loss_cap_rejects_at_exact_threshold():
    result = _check(
        account=_account(equity=9_800.0, day_start_equity=10_000.0)
    )

    assert result.approved is False
    assert "daily loss cap" in result.reasons[0]


def test_sell_requires_a_held_position_when_shorting_is_disabled():
    result = _check(trade=_trade(side="SELL"))

    assert result.approved is False
    assert "not held" in result.reasons[0]


def test_sell_quantity_cannot_exceed_held_shares():
    result = _check(
        trade=_trade(side="SELL", price=30.0),
        account=_account(
            equity=1_000.0,
            day_start_equity=1_000.0,
            cash=10.0,
            positions={"ACME": 100.0},
        ),
        config=_config(max_position_pct=0.5, max_total_exposure_pct=0.5),
    )

    assert result.approved is True
    assert result.qty == 3


def test_short_sale_requires_explicit_opt_in():
    result = _check(
        trade=_trade(side="SELL"),
        config=_config(allow_short=True),
    )

    assert result.approved is True
    assert result.qty == 5


@pytest.mark.parametrize("price", [0, -1, nan])
def test_zero_negative_and_nan_prices_are_rejected(price):
    result = _check(trade=_trade(price=price))

    assert result.approved is False
    assert result.qty == 0


def test_non_finite_confidence_is_rejected():
    result = _check(trade=_trade(confidence=nan))

    assert result.approved is False
    assert result.qty == 0


@pytest.mark.parametrize(
    "account",
    [
        _account(equity=0),
        _account(equity=-1),
        _account(equity=nan),
        _account(cash=0),
        _account(positions={"OTHER": 0}),
        _account(positions={"OTHER": nan}),
        _account(positions={"OTHER": -1}),
    ],
)
def test_invalid_account_numbers_are_rejected(account):
    result = _check(account=account)

    assert result.approved is False
    assert result.qty == 0


def test_missing_or_naive_timestamps_are_rejected():
    missing = _check(trade=_trade(price_timestamp=None))
    naive_now = _check(now=datetime(2026, 1, 5, 10, 0))

    assert missing.approved is False
    assert naive_now.approved is False


def test_future_timestamps_are_rejected():
    future = BASE_NOW + timedelta(seconds=1)

    result = _check(
        trade=_trade(price_timestamp=future),
        account=_account(as_of=future),
    )

    assert result.approved is False
    assert "future" in result.reasons[0]


def test_risk_config_is_immutable_and_whitelist_is_frozen():
    source = {"ACME"}
    config = RiskConfig(symbol_whitelist=source)
    source.add("OTHER")

    with pytest.raises(FrozenInstanceError):
        config.min_confidence = 0.1

    assert config.symbol_whitelist == frozenset({"ACME"})


def test_risk_events_log_approved_and_rejected_decisions(tmp_path):
    db_path = tmp_path / "risk.db"
    create_tables(db_path)
    trade = _trade()
    approved = check_trade(trade, _account(), _config(), BASE_NOW)
    rejected = check_trade(
        trade,
        _account(),
        RiskConfig(),
        BASE_NOW,
    )
    invalid_trade = _trade(price=nan)
    invalid = check_trade(invalid_trade, _account(), _config(), BASE_NOW)

    approved_id = log_risk_decision(db_path, trade, approved)
    rejected_id = log_risk_decision(db_path, trade, rejected)
    invalid_id = log_risk_decision(db_path, invalid_trade, invalid)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT id, symbol, side, price, confidence, approved, qty, reasons "
            "FROM risk_events ORDER BY id"
        ).fetchall()

    assert rows[0][:7] == (approved_id, "ACME", "BUY", 100.0, 0.8, 1, 5)
    assert json.loads(rows[0][7]) == []
    assert rows[1][0] == rejected_id
    assert rows[1][5:7] == (0, 0)
    assert "whitelist" in json.loads(rows[1][7])[0]
    assert rows[2][0] == invalid_id
    assert rows[2][3] == "nan"
    assert rows[2][5:7] == (0, 0)
