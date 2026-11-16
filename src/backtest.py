from __future__ import annotations

import csv
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .strategies import StrategyRegistry, get_default_registry


HORIZONS = {"1d": timedelta(days=1), "5d": timedelta(days=5), "20d": timedelta(days=20)}


def walk_forward_backtest(
    csv_path: str | Path,
    *,
    registry: StrategyRegistry | None = None,
    symbols: Iterable[str] | None = None,
    horizon: str = "1d",
    train_fraction: float = 0.7,
    cost_bps: float = 5.0,
) -> list[dict[str, object]]:
    """Evaluate fixed strategies on the chronological holdout portion of CSV history.

    Strategies receive only bars at or before each decision time. Costs are a
    round trip, applied as twice ``cost_bps`` to each directional trade.
    Results are per-signal diagnostics, not a portfolio simulation.
    """
    if horizon not in HORIZONS:
        raise ValueError("horizon must be one of 1d, 5d, or 20d")
    if not math.isfinite(train_fraction) or not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    if not math.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("cost_bps must be finite and non-negative")

    selected_symbols = None if symbols is None else {str(symbol).upper() for symbol in symbols}
    histories: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    path = Path(csv_path)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        if not {"timestamp", "symbol", "price"} <= set(reader.fieldnames or ()):
            raise ValueError("CSV must contain timestamp, symbol, and price columns")
        for row in reader:
            symbol = row["symbol"].strip().upper()
            if not symbol or (selected_symbols is not None and symbol not in selected_symbols):
                continue
            try:
                timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                price = float(row["price"])
            except (TypeError, ValueError):
                continue
            if timestamp.tzinfo is None or not math.isfinite(price) or price <= 0:
                continue
            histories[symbol].append((timestamp.astimezone(timezone.utc), price))

    for history in histories.values():
        history.sort(key=lambda quote: quote[0])

    resolved_registry = registry or get_default_registry()
    results: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
    round_trip_cost = 2 * cost_bps / 10_000
    holding_period = HORIZONS[horizon]

    for symbol, history in histories.items():
        if len(history) < 3:
            continue
        split_index = max(1, min(len(history) - 1, int(len(history) * train_fraction)))
        for strategy in resolved_registry.iterate():
            for index in range(split_index, len(history)):
                decision_time, entry_price = history[index]
                exit_quote = next(
                    (quote for quote in history[index + 1:] if quote[0] >= decision_time + holding_period),
                    None,
                )
                if exit_quote is None:
                    continue
                signal = strategy.generator(symbol, history[:index + 1], decision_time)
                if signal is None:
                    continue
                if signal.signal not in {"BUY", "SELL"}:
                    continue
                exit_price = exit_quote[1]
                gross_return = (
                    (exit_price - entry_price) / entry_price
                    if signal.signal == "BUY"
                    else (entry_price - exit_price) / entry_price
                )
                net_return = gross_return - round_trip_cost
                results[strategy.name].append((decision_time, gross_return, net_return))

    summaries: list[dict[str, object]] = []
    for strategy in resolved_registry.iterate():
        trades = sorted(results.get(strategy.name, ()), key=lambda row: row[0])
        if not trades:
            continue
        gross_returns = [row[1] for row in trades]
        net_returns = [row[2] for row in trades]
        summaries.append(
            {
                "model_name": strategy.name,
                "out_of_sample_start": trades[0][0].isoformat(),
                "trades": len(trades),
                "horizon": horizon,
                "round_trip_cost_bps": 2 * cost_bps,
                "average_gross_return": statistics.mean(gross_returns),
                "average_net_return": statistics.mean(net_returns),
                "median_net_return": statistics.median(net_returns),
                "win_rate": sum(value > 0 for value in net_returns) / len(net_returns),
            }
        )
    return summaries