from __future__ import annotations

import io
import json
import logging
import sys
from collections import Counter
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db import DatabasePath, _connect, create_tables, save_daily_report
from src.outcomes import process_outcomes
from src.prices import CsvPriceProvider, PriceProvider
from src.report import print_report


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "research.db"
STARTING_CASH = 10_000.0


def _timestamp(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _apply_fill(cash: float, positions: dict[str, int], order) -> float:
    quantity = int(order["qty"])
    fill_price = float(order["fill_price"])
    if order["side"] == "BUY":
        cash -= quantity * fill_price
        positions[order["symbol"]] = positions.get(order["symbol"], 0) + quantity
    elif order["side"] == "SELL":
        cash += quantity * fill_price
        positions[order["symbol"]] = positions.get(order["symbol"], 0) - quantity
    return cash


def _equity(cash: float, positions: dict[str, int], provider: PriceProvider, when: datetime):
    value = cash
    for symbol, quantity in positions.items():
        if quantity:
            price = provider.get_price(symbol, when)
            if price is None:
                return None
            value += quantity * price
    return value


def _build_report(db_path: DatabasePath, provider: PriceProvider, now: datetime) -> str:
    day = now.date()
    start = datetime.combine(day, datetime.min.time(), timezone.utc)
    end = start + timedelta(days=1)
    with closing(_connect(db_path)) as connection:
        signals = connection.execute(
            "SELECT id, symbol, signal, created_at FROM signals ORDER BY id"
        ).fetchall()
        orders = connection.execute(
            "SELECT symbol, side, qty, fill_price, status, created_at FROM orders ORDER BY id"
        ).fetchall()
        events = connection.execute(
            "SELECT approved, reasons, created_at FROM risk_events ORDER BY id"
        ).fetchall()
        resolved = connection.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]

    def is_today(value: str) -> bool:
        try:
            return start <= _timestamp(value) < end
        except (TypeError, ValueError):
            return False

    today_signals = [row for row in signals if is_today(row["created_at"])]
    today_orders = [row for row in orders if is_today(row["created_at"])]
    today_events = [row for row in events if is_today(row["created_at"])]
    signal_counts = Counter(row["signal"] for row in today_signals)
    symbol_counts = Counter(row["symbol"] for row in today_signals)
    fills = [row for row in today_orders if row["status"] == "filled"]
    approvals = sum(bool(row["approved"]) for row in today_events)
    rejections = len(today_events) - approvals
    rejection_reasons: Counter[str] = Counter()
    loss_cap_hit = False
    for event in today_events:
        if event["approved"]:
            continue
        try:
            reasons = json.loads(event["reasons"])
        except (TypeError, ValueError):
            reasons = ["unreadable rejection reason"]
        for reason in reasons or ["unspecified rejection"]:
            rejection_reasons[str(reason)] += 1
            if "daily loss cap has been hit" in str(reason).lower():
                loss_cap_hit = True

    cash_at_start = STARTING_CASH
    cash_now = STARTING_CASH
    positions_at_start: dict[str, int] = {}
    positions_now: dict[str, int] = {}
    for order in orders:
        if order["status"] != "filled" or order["fill_price"] is None:
            continue
        order_time = _timestamp(order["created_at"])
        if order_time > now:
            continue
        cash_now = _apply_fill(cash_now, positions_now, order)
        if order_time < start:
            cash_at_start = _apply_fill(cash_at_start, positions_at_start, order)
    equity_start = _equity(cash_at_start, positions_at_start, provider, start)
    equity_now = _equity(cash_now, positions_now, provider, now)

    output = io.StringIO()
    with redirect_stdout(output):
        print_report(db_path)
    full_summary = output.getvalue()
    confidence_summary = full_summary.split("Resolved outcomes by confidence:\n", 1)
    bucket_summary = confidence_summary[-1].strip()

    lines = [
        f"Nightly review for {day.isoformat()}",
        "",
        "Signals today: " + ", ".join(f"{kind}={signal_counts.get(kind, 0)}" for kind in ("BUY", "SELL", "HOLD")),
        f"Orders today: {len(today_orders)}; fills: {len(fills)}",
        "Risk rejections by reason:",
    ]
    lines.extend(f"  {reason}: {count}" for reason, count in sorted(rejection_reasons.items()))
    if not rejection_reasons:
        lines.append("  None")
    if equity_start is None or equity_now is None:
        lines.append("Account equity vs start of day: unavailable (missing price data).")
    else:
        change = equity_now - equity_start
        lines.append(
            f"Account equity vs start of day: ${equity_now:,.2f} vs ${equity_start:,.2f} "
            f"(change ${change:+,.2f})."
        )
    lines.extend((f"Resolved outcomes so far: {resolved}", "", "Confidence-bucket summary:", bucket_summary, "", "Warnings:"))

    warnings = []
    if not today_signals:
        warnings.append("No signals were logged today.")
    if today_signals and symbol_counts:
        symbol, count = symbol_counts.most_common(1)[0]
        if count / len(today_signals) > 0.5:
            warnings.append(f"More than 50% of today's signals are for {symbol}.")
    latest = getattr(provider, "latest_timestamp", None)
    if latest is not None:
        if latest.tzinfo is None or latest.utcoffset() is None:
            raise ValueError("price provider latest_timestamp must be timezone-aware")
        if now.astimezone(timezone.utc) - latest.astimezone(timezone.utc) > timedelta(days=1):
            warnings.append("The latest price data is older than 1 day.")
    if (PROJECT_ROOT / "KILL").exists():
        warnings.append("The KILL switch file exists.")
    if loss_cap_hit:
        warnings.append("The daily loss cap was hit today.")
    if rejections > 0 and rejections >= approvals * 3:
        warnings.append("Risk rejections outnumber approvals by 3 to 1 or more today.")
    lines.extend(warnings or ["None."])
    return "\n".join(lines) + "\n"


def _persist(db_path: DatabasePath, report_date: str, text: str) -> None:
    save_daily_report(db_path, report_date, text)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{report_date}.txt").write_text(text, encoding="utf-8")


def run_nightly(db_path: DatabasePath, price_provider: PriceProvider, now: datetime) -> str:
    report_date = datetime.now(timezone.utc).date().isoformat()
    try:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime")
        now = now.astimezone(timezone.utc)
        report_date = now.date().isoformat()
        create_tables(db_path)
        process_outcomes(db_path, price_provider, now)
        report_text = _build_report(db_path, price_provider, now)
        _persist(db_path, report_date, report_text)
        return report_text
    except Exception as error:
        logger.exception("Nightly report failed")
        report_text = f"Nightly review for {report_date}\nERROR: Nightly report failed: {error}\n"
        try:
            _persist(db_path, report_date, report_text)
        except Exception:
            logger.exception("Could not persist nightly error report")
        return report_text


if __name__ == "__main__":
    current_time = datetime.now(timezone.utc)
    try:
        prices = CsvPriceProvider(PROJECT_ROOT / "data" / "prices.csv")
        report = run_nightly(DEFAULT_DB_PATH, prices, current_time)
    except Exception as error:
        report = f"Nightly review for {current_time.date()}\nERROR: Nightly report failed: {error}\n"
        try:
            _persist(DEFAULT_DB_PATH, current_time.date().isoformat(), report)
        except Exception:
            logger.exception("Could not persist nightly startup error report")
    print(report, end="")
