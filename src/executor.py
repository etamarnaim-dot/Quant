from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .broker import Broker, SimOrder
from .db import DatabasePath, create_tables, log_order, log_risk_decision
from .risk import ProposedTrade, RiskConfig, RiskDecision, check_trade


logger = logging.getLogger(__name__)
KILL_SWITCH_PATH = Path(__file__).resolve().parents[1] / "KILL"


def _log_order(db_path: DatabasePath, signal_id: int, order: SimOrder) -> None:
    log_order(
        db_path,
        client_order_id=order.client_order_id,
        signal_id=signal_id,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        fill_price=order.fill_price,
        status=order.status,
        created_at=order.created_at.isoformat(),
    )


def process_signal(
    signal: Mapping[str, object],
    price: float,
    broker: Broker,
    config: RiskConfig,
    now: datetime,
    *,
    db_path: DatabasePath = "data/research.db",
) -> SimOrder | None:
    try:
        signal_id = signal["id"]
        if not isinstance(signal_id, int) or isinstance(signal_id, bool):
            raise ValueError("signal id must be an integer")
        symbol = signal["symbol"]
        signal_type = signal["signal"]
        confidence = signal["confidence"]
        if not isinstance(symbol, str) or not isinstance(signal_type, str):
            raise ValueError("signal symbol and type must be strings")
        if signal_type == "HOLD":
            logger.info("HOLD signal %s logged; no order submitted", signal_id)
            return None

        trade = ProposedTrade(symbol, signal_type, price, confidence, now)
        create_tables(db_path)
        client_order_id = f"signal-{signal_id}"
        if KILL_SWITCH_PATH.exists():
            decision = RiskDecision(False, 0, ["KILL file exists; order submission disabled"])
            log_risk_decision(db_path, trade, decision)
            logger.warning("Signal %s blocked by KILL switch", signal_id)
            return None

        existing = broker.get_order(client_order_id)
        if existing is not None:
            _log_order(db_path, signal_id, existing)
            return existing

        decision = check_trade(trade, broker.get_account(), config, now)
        log_risk_decision(db_path, trade, decision)
        if not decision.approved:
            logger.info("Signal %s rejected: %s", signal_id, "; ".join(decision.reasons))
            return None

        order = broker.submit_order(symbol, signal_type, decision.qty, client_order_id)
        _log_order(db_path, signal_id, order)
        logger.info(
            "Order %s %s %s qty=%s status=%s",
            order.client_order_id,
            order.side,
            order.symbol,
            order.qty,
            order.status,
        )
        return order
    except Exception:
        logger.exception("Signal processing failed; no order submitted")
        return None
