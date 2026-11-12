from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 0.05
    max_total_exposure_pct: float = 0.50
    daily_loss_cap_pct: float = 0.02
    max_data_age_seconds: int = 60
    min_confidence: float = 0.6
    symbol_whitelist: frozenset[str] = frozenset()
    allow_short: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol_whitelist", frozenset(self.symbol_whitelist))


@dataclass
class ProposedTrade:
    symbol: str
    side: str
    price: float
    confidence: float
    price_timestamp: datetime


@dataclass
class AccountState:
    equity: float
    day_start_equity: float
    cash: float
    positions: dict[str, float]
    as_of: datetime


@dataclass
class RiskDecision:
    approved: bool
    qty: int
    reasons: list[str]


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{name} must be a number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return result


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _floor_ratio(numerator: Decimal, denominator: Decimal) -> int:
    return int((numerator / denominator).to_integral_value(rounding=ROUND_FLOOR))


def _ceil_ratio(numerator: Decimal, denominator: Decimal) -> int:
    return int((numerator / denominator).to_integral_value(rounding=ROUND_CEILING))


def _validate_config(config: RiskConfig) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    if not isinstance(config, RiskConfig):
        raise ValueError("config must be a RiskConfig")

    max_position = _decimal(config.max_position_pct, "max_position_pct")
    max_exposure = _decimal(config.max_total_exposure_pct, "max_total_exposure_pct")
    loss_cap = _decimal(config.daily_loss_cap_pct, "daily_loss_cap_pct")
    max_age = _decimal(config.max_data_age_seconds, "max_data_age_seconds")
    min_confidence = _decimal(config.min_confidence, "min_confidence")

    if not Decimal(0) < max_position <= Decimal(1):
        raise ValueError("max_position_pct must be greater than 0 and at most 1")
    if not Decimal(0) < max_exposure <= Decimal(1):
        raise ValueError("max_total_exposure_pct must be greater than 0 and at most 1")
    if not Decimal(0) < loss_cap <= Decimal(1):
        raise ValueError("daily_loss_cap_pct must be greater than 0 and at most 1")
    if max_age <= 0:
        raise ValueError("max_data_age_seconds must be greater than 0")
    if not Decimal(0) < min_confidence <= Decimal(1):
        raise ValueError("min_confidence must be greater than 0 and at most 1")
    if not isinstance(config.symbol_whitelist, frozenset) or any(
        not isinstance(symbol, str) or not symbol.strip()
        for symbol in config.symbol_whitelist
    ):
        raise ValueError("symbol_whitelist must contain non-empty symbol strings")
    if not isinstance(config.allow_short, bool):
        raise ValueError("allow_short must be a boolean")

    return max_position, max_exposure, loss_cap, max_age, min_confidence


def _check_trade(
    trade: ProposedTrade,
    account: AccountState,
    config: RiskConfig,
    now: datetime,
) -> RiskDecision:
    if not isinstance(trade, ProposedTrade):
        raise ValueError("trade must be a ProposedTrade")
    if not isinstance(account, AccountState):
        raise ValueError("account must be an AccountState")

    max_position_pct, max_exposure_pct, loss_cap_pct, max_age, min_confidence = (
        _validate_config(config)
    )
    now = _aware_datetime(now, "now")

    if not isinstance(trade.symbol, str) or not trade.symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    if trade.side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    price = _decimal(trade.price, "price")
    confidence = _decimal(trade.confidence, "confidence")
    if price <= 0:
        raise ValueError("price must be greater than 0")
    if not Decimal(0) < confidence <= Decimal(1):
        raise ValueError("confidence must be greater than 0 and at most 1")

    price_timestamp = _aware_datetime(trade.price_timestamp, "price_timestamp")
    equity = _decimal(account.equity, "equity")
    day_start_equity = _decimal(account.day_start_equity, "day_start_equity")
    cash = _decimal(account.cash, "cash")
    if equity <= 0:
        raise ValueError("equity must be greater than 0")
    if day_start_equity <= 0:
        raise ValueError("day_start_equity must be greater than 0")
    if cash <= 0:
        raise ValueError("cash must be greater than 0")
    if not isinstance(account.positions, dict):
        raise ValueError("positions must be a dictionary")

    positions: dict[str, Decimal] = {}
    for symbol, market_value in account.positions.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("position symbols must be non-empty strings")
        value = _decimal(market_value, f"position value for {symbol}")
        if value == 0:
            raise ValueError(f"position value for {symbol} must not be zero")
        if value < 0 and not config.allow_short:
            raise ValueError(f"short position in {symbol} is not allowed")
        positions[symbol] = value

    account_timestamp = _aware_datetime(account.as_of, "account.as_of")
    for timestamp, name in (
        (price_timestamp, "price data"),
        (account_timestamp, "account data"),
    ):
        age_seconds = Decimal(str((now - timestamp).total_seconds()))
        if age_seconds < 0:
            raise ValueError(f"{name} timestamp is in the future")
        if age_seconds > max_age:
            raise ValueError(f"{name} is stale")

    if trade.symbol not in config.symbol_whitelist:
        return RiskDecision(False, 0, ["symbol is not on the whitelist"])
    if confidence < min_confidence:
        return RiskDecision(False, 0, ["confidence is below the minimum"])

    market_now = now.astimezone(ZoneInfo("America/New_York"))
    # US market holidays are not handled yet.
    if market_now.weekday() >= 5 or not (
        time(9, 30) <= market_now.time().replace(tzinfo=None) < time(16, 0)
    ):
        return RiskDecision(False, 0, ["outside regular US market hours"])

    daily_loss_pct = (day_start_equity - equity) / day_start_equity
    if daily_loss_pct >= loss_cap_pct:
        return RiskDecision(False, 0, ["daily loss cap has been hit"])

    current_position = positions.get(trade.symbol, Decimal(0))
    if trade.side == "SELL" and current_position <= 0 and not config.allow_short:
        return RiskDecision(False, 0, ["cannot sell a position that is not held"])

    position_limit = equity * max_position_pct
    total_limit = equity * max_exposure_pct
    other_exposure = sum(
        (abs(value) for symbol, value in positions.items() if symbol != trade.symbol),
        Decimal(0),
    )
    remaining_total = total_limit - other_exposure
    if remaining_total < 0:
        return RiskDecision(False, 0, ["existing positions exceed the total exposure limit"])
    allowed_position_value = min(position_limit, remaining_total)

    if trade.side == "BUY":
        minimum_qty = _ceil_ratio(-allowed_position_value - current_position, price)
        maximum_qty = _floor_ratio(allowed_position_value - current_position, price)
        maximum_qty = min(maximum_qty, _floor_ratio(cash, price))
    else:
        minimum_qty = _ceil_ratio(current_position - allowed_position_value, price)
        maximum_qty = _floor_ratio(current_position + allowed_position_value, price)
        if not config.allow_short:
            maximum_qty = min(maximum_qty, _floor_ratio(current_position, price))

    minimum_qty = max(1, minimum_qty)
    if maximum_qty < minimum_qty:
        if trade.side == "BUY" and cash < price:
            reason = "insufficient cash for one share"
        else:
            reason = "no whole-share quantity fits within the risk limits"
        return RiskDecision(False, 0, [reason])

    return RiskDecision(True, maximum_qty, [])


def check_trade(
    trade: ProposedTrade,
    account: AccountState,
    config: RiskConfig,
    now: datetime,
) -> RiskDecision:
    try:
        return _check_trade(trade, account, config, now)
    except ValueError as error:
        return RiskDecision(False, 0, [str(error)])
    except Exception as error:
        return RiskDecision(
            False,
            0,
            [f"risk check failed closed: invalid or ambiguous input ({type(error).__name__})"],
        )
