from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .db import DatabasePath, create_tables, insert_signal
from .strategies import StrategyRegistry, get_default_registry


class PriceProviderProtocol(Protocol):
    def get_price(self, symbol: str, when: datetime) -> float | None:
        ...


@dataclass(frozen=True)
class ResearchConfig:
    symbols: tuple[str, ...] = ("ACME", "BETA")
    strategy_names: tuple[str, ...] = ("momentum", "mean_reversion")
    horizons: tuple[str, ...] = ("1d", "5d", "20d")
    min_confidence: float = 0.6
    max_signals_per_symbol: int = 3
    lookback_days: int = 30


def _recent_news_context(
    db_path: DatabasePath,
    symbol: str,
    now: datetime,
) -> tuple[float, int]:
    cutoff = now - timedelta(hours=48)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT sentiment, relevance_score, quality_score, published_at "
            "FROM news_events WHERE symbol = ? AND published_at IS NOT NULL",
            (symbol,),
        ).fetchall()

    weighted_sentiment = 0.0
    total_weight = 0.0
    item_count = 0
    for sentiment, relevance, quality, published_at in rows:
        try:
            text = str(published_at).replace("Z", "+00:00")
            timestamp = datetime.fromisoformat(text)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                continue
            timestamp = timestamp.astimezone(timezone.utc)
            if timestamp < cutoff or timestamp > now:
                continue
            score = max(-1.0, min(1.0, (float(sentiment) - 0.5) * 2.0))
            weight = max(0.0, float(relevance)) * max(0.0, float(quality))
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        weighted_sentiment += score * weight
        total_weight += weight
        item_count += 1
    if not total_weight:
        return 0.0, 0
    return weighted_sentiment / total_weight, item_count


def _collect_history(
    price_provider: PriceProviderProtocol,
    symbol: str,
    now: datetime,
    lookback_days: int,
) -> list[tuple[datetime, float]]:
    history: list[tuple[datetime, float]] = []
    for offset in range(lookback_days + 1):
        when = now - timedelta(days=offset)
        price = price_provider.get_price(symbol, when)
        if price is not None:
            history.append((when, float(price)))
    history.sort(key=lambda pair: pair[0])
    return history


def _fallback_registry() -> StrategyRegistry:
    return get_default_registry()


def summarize_strategy_performance(db_path: DatabasePath = "data/research.db") -> list[dict[str, object]]:
    path = Path(db_path)
    if not path.exists():
        return []

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "signals" not in tables:
            return []

        rows = connection.execute(
            """
            SELECT
                s.model_name,
                COUNT(*) AS signal_count,
                AVG(s.confidence) AS average_confidence,
                SUM(CASE WHEN s.signal = 'BUY' THEN 1 ELSE 0 END) AS buy_count,
                SUM(CASE WHEN s.signal = 'SELL' THEN 1 ELSE 0 END) AS sell_count,
                SUM(CASE WHEN s.signal = 'HOLD' THEN 1 ELSE 0 END) AS hold_count,
                AVG(CASE WHEN o.return_pct IS NULL THEN NULL ELSE o.return_pct END) AS average_return,
                AVG(CASE WHEN o.return_pct IS NOT NULL AND o.return_pct > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
            FROM signals AS s
            LEFT JOIN outcomes AS o ON o.signal_id = s.id
            GROUP BY s.model_name
            ORDER BY signal_count DESC, average_confidence DESC, average_return DESC NULLS LAST
            """
        ).fetchall()

    summary: list[dict[str, object]] = []
    for row in rows:
        summary.append(
            {
                "model_name": row["model_name"],
                "signal_count": int(row["signal_count"] or 0),
                "buy_count": int(row["buy_count"] or 0),
                "sell_count": int(row["sell_count"] or 0),
                "hold_count": int(row["hold_count"] or 0),
                "average_confidence": float(row["average_confidence"] or 0.0),
                "average_return": None if row["average_return"] is None else float(row["average_return"]),
                "win_rate": None if row["win_rate"] is None else float(row["win_rate"]),
            }
        )
    return summary


def summarize_strategy_drift(db_path: DatabasePath = "data/research.db") -> list[dict[str, object]]:
    path = Path(db_path)
    if not path.exists():
        return []

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "signals" not in tables:
            return []

        rows = connection.execute(
            """
            SELECT model_name, confidence, created_at
            FROM signals
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()

    by_model: dict[str, list[tuple[float, datetime]]] = {}
    for row in rows:
        model_name = row["model_name"]
        created_at = row["created_at"]
        if created_at is None:
            continue
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        by_model.setdefault(model_name, []).append((float(row["confidence"]), created_dt))

    drift_rows: list[dict[str, object]] = []
    for model_name, points in by_model.items():
        if len(points) < 2:
            continue
        recent_points = points[: max(1, len(points) // 2)]
        older_points = points[max(1, len(points) // 2):]
        recent_avg = sum(value for value, _ in recent_points) / len(recent_points)
        older_avg = sum(value for value, _ in older_points) / len(older_points) if older_points else recent_avg
        confidence_delta = recent_avg - older_avg
        drift_rows.append(
            {
                "model_name": model_name,
                "recent_signal_count": len(recent_points),
                "confidence_delta": confidence_delta,
                "recent_average_confidence": recent_avg,
                "baseline_average_confidence": older_avg,
                "status": "declining" if confidence_delta < 0 else "stable",
            }
        )

    drift_rows.sort(key=lambda row: (row["confidence_delta"], row["recent_signal_count"]))
    return drift_rows


def run_research_cycle(
    *,
    db_path: DatabasePath = "data/research.db",
    price_provider: PriceProviderProtocol,
    config: ResearchConfig | None = None,
    now: datetime | None = None,
    registry: StrategyRegistry | None = None,
    return_summary: bool = False,
) -> list[dict[str, object]] | dict[str, object]:
    resolved_config = config or ResearchConfig()
    resolved_now = now or datetime.now(timezone.utc)
    resolved_registry = registry or _fallback_registry()

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    create_tables(db_path)

    inserted: list[dict[str, object]] = []
    per_symbol_count: dict[str, int] = {}

    for symbol in resolved_config.symbols:
        history = _collect_history(
            price_provider,
            symbol,
            resolved_now,
            resolved_config.lookback_days,
        )
        if not history:
            continue

        for strategy_name in resolved_config.strategy_names:
            if strategy_name not in resolved_registry.names:
                continue

            strategy = resolved_registry.get(strategy_name)
            signal = strategy.generator(symbol, history, resolved_now)
            if signal is None:
                continue
            news_sentiment, news_count = _recent_news_context(db_path, symbol, resolved_now)
            direction = 1.0 if signal.signal == "BUY" else -1.0 if signal.signal == "SELL" else 0.0
            confidence_adjustment = max(-0.1, min(0.1, news_sentiment * direction * 0.1))
            adjusted_confidence = max(0.0, min(1.0, signal.confidence + confidence_adjustment))
            if adjusted_confidence < resolved_config.min_confidence:
                continue
            if per_symbol_count.get(symbol, 0) >= resolved_config.max_signals_per_symbol:
                continue

            payload = signal.as_payload()
            payload["confidence"] = adjusted_confidence
            if news_count:
                tone = "positive" if news_sentiment > 0.05 else "negative" if news_sentiment < -0.05 else "neutral"
                alignment = "aligned with" if news_sentiment * direction > 0.05 else "conflicted with" if news_sentiment * direction < -0.05 else "did not materially affect"
                payload["rationale"] = (
                    f"{signal.rationale} Recent news context: {tone} sentiment {alignment} "
                    f"the {signal.signal} signal across {news_count} item(s); confidence adjustment "
                    f"{confidence_adjustment:+.3f}."
                )
            payload["horizon"] = signal.horizon
            payload["created_at"] = resolved_now.isoformat()
            with sqlite3.connect(db_path) as connection:
                duplicate = connection.execute(
                    "SELECT 1 FROM signals WHERE symbol = ? AND signal = ? "
                    "AND confidence = ? AND horizon = ? AND rationale = ? "
                    "AND model_name = ? AND prompt_version = ? "
                    "AND price_at_signal IS ? AND date(created_at) = ? LIMIT 1",
                    (
                        payload["symbol"],
                        payload["signal"],
                        payload["confidence"],
                        payload["horizon"],
                        payload["rationale"],
                        payload["model_name"],
                        payload["prompt_version"],
                        payload["price_at_signal"],
                        resolved_now.date().isoformat(),
                    ),
                ).fetchone()
            if duplicate is not None:
                continue
            signal_id = insert_signal(
                db_path,
                item_id=None,
                symbol=str(payload["symbol"]),
                signal=str(payload["signal"]),
                confidence=float(payload["confidence"]),
                horizon=str(payload["horizon"]),
                rationale=str(payload["rationale"]),
                model_name=str(payload["model_name"]),
                prompt_version=str(payload["prompt_version"]),
                price_at_signal=payload["price_at_signal"],
                created_at=str(payload["created_at"]),
            )
            payload["id"] = signal_id
            inserted.append(payload)
            per_symbol_count[symbol] = per_symbol_count.get(symbol, 0) + 1

    result: dict[str, object] = {
        "signals_generated": len(inserted),
        "signals": inserted,
        "strategy_ranking": summarize_strategy_performance(db_path),
        "strategy_drift": summarize_strategy_drift(db_path),
    }
    if return_summary:
        return result
    return inserted
