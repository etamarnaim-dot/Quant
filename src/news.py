from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .db import DatabasePath, create_tables, fetch_news_events, insert_news_event, insert_raw_item
from .news_sources import build_default_news_sources


def _normalize_datetime(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _dedupe_key(article: dict[str, object]) -> str:
    href = str(article.get("url") or "").strip()
    if href:
        return "url:" + href
    title = str(article.get("title") or "").strip()
    content = str(article.get("content") or "").strip()
    payload = (title + "\n" + content).strip()
    return "content:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _detect_event_type(title: str, content: str) -> str:
    text = (title + " " + content).lower()
    if "guidance" in text or "raises guidance" in text or "guidance upgrade" in text:
        return "guidance"
    if "earnings" in text or "revenue" in text or "profit" in text:
        return "earnings"
    if "merger" in text or "acquisition" in text or "deal" in text:
        return "deal"
    if "regulatory" in text or "sec" in text or "compliance" in text:
        return "regulatory"
    if "launch" in text or "product" in text:
        return "product"
    return "general"


def _sentiment_score(title: str, content: str) -> float:
    text = (title + " " + content).lower()
    positives = [
        "strong",
        "beat",
        "raise",
        "rises",
        "growth",
        "upbeat",
        "surge",
        "outperform",
        "boost",
        "guidance",
        "expands",
    ]
    negatives = [
        "miss",
        "weak",
        "cut",
        "lower",
        "downgrade",
        "drop",
        "decline",
        "down",
        "issue",
        "delay",
    ]
    positive_hits = sum(1 for term in positives if term in text)
    negative_hits = sum(1 for term in negatives if term in text)
    score = (positive_hits - negative_hits) / max(1, positive_hits + negative_hits + 1)
    return max(0.0, min(1.0, (score + 1.0) / 2.0))


def normalize_news_item(article: dict[str, object]) -> dict[str, object]:
    title = str(article.get("title") or article.get("headline") or "Untitled").strip()
    content = str(article.get("content") or article.get("text") or title).strip()
    symbols = article.get("symbols") or []
    if isinstance(symbols, str):
        symbols = [symbols]
    symbol_list = [str(symbol).upper().strip() for symbol in list(symbols)]
    symbol_list = [symbol for symbol in symbol_list if symbol]
    if not symbol_list:
        symbol_list = ["UNKNOWN"]

    event_type = _detect_event_type(title, content)
    sentiment = _sentiment_score(title, content)
    relevance = min(1.0, 0.55 + (0.1 * len(symbol_list)) + (0.15 if event_type != "general" else 0.0))
    quality = min(1.0, 0.65 + (0.15 if article.get("source") else 0.0))
    summary = content[:280] if content else title

    return {
        "source": str(article.get("source") or "unknown"),
        "title": title,
        "content": content,
        "summary": summary,
        "published_at": _normalize_datetime(str(article.get("published_at") or article.get("published") or "")),
        "url": str(article.get("url") or article.get("link") or ""),
        "symbols": symbol_list,
        "event_type": event_type,
        "sentiment": float(sentiment),
        "relevance_score": float(relevance),
        "quality_score": float(quality),
    }


def ingest_news_articles(
    db_path: DatabasePath,
    articles: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    create_tables(path)

    seen_keys: set[str] = set()
    with path.open("r", encoding="utf-8", errors="ignore") as _:
        pass

    existing = fetch_news_events(path)
    for row in existing:
        if "source" in row and "title" in row:
            seen_keys.add(f"source:{row['source']}|title:{row['title']}")

    inserted: list[dict[str, object]] = []
    for article in articles:
        normalized = normalize_news_item(article)
        dedupe_key = _dedupe_key(normalized)
        if dedupe_key in seen_keys:
            continue

        item_id = insert_raw_item(
            path,
            source=normalized["source"],
            fetched_at=_normalize_datetime(None),
            text=normalized["content"],
            title=normalized["title"],
            url=normalized["url"],
            published_at=normalized["published_at"],
        )
        for symbol in normalized["symbols"]:
            event_id = insert_news_event(
                path,
                item_id=item_id,
                symbol=str(symbol),
                title=str(normalized["title"]),
                summary=str(normalized["summary"]),
                event_type=str(normalized["event_type"]),
                sentiment=float(normalized["sentiment"]),
                relevance_score=float(normalized["relevance_score"]),
                quality_score=float(normalized["quality_score"]),
                source=str(normalized["source"]),
                published_at=str(normalized["published_at"]),
            )
            inserted.append(
                {
                    "id": event_id,
                    "item_id": item_id,
                    "symbol": symbol,
                    "title": normalized["title"],
                    "summary": normalized["summary"],
                    "event_type": normalized["event_type"],
                    "sentiment": normalized["sentiment"],
                    "relevance_score": normalized["relevance_score"],
                    "quality_score": normalized["quality_score"],
                    "source": normalized["source"],
                    "published_at": normalized["published_at"],
                }
            )
        seen_keys.add(dedupe_key)
    return inserted


def fetch_news_items(db_path: DatabasePath, *, limit: int | None = None) -> list[dict[str, object]]:
    query = "SELECT * FROM raw_items ORDER BY id DESC"
    if limit is not None:
        query += " LIMIT ?"
    from sqlite3 import connect
    connection = connect(db_path)
    try:
        rows = connection.execute(query, ((limit,) if limit is not None else ())).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def fetch_news_events(db_path: DatabasePath, *, limit: int | None = None) -> list[dict[str, object]]:
    from .db import fetch_news_events as _fetch_news_events
    return _fetch_news_events(db_path, limit=limit)


def fetch_live_news(db_path: DatabasePath, *, symbols: Iterable[str] | None = None, limit: int | None = None) -> list[dict[str, object]]:
    registry = build_default_news_sources(symbols=symbols)
    articles = registry.fetch_articles()
    if limit is not None:
        articles = articles[:limit]
    return ingest_news_articles(db_path, articles)
