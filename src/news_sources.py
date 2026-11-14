from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import error, request

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class NewsSource(ABC):
    @abstractmethod
    def fetch_articles(self) -> list[dict[str, object]]:
        ...


def normalize_news_article(item: dict[str, object], *, default_source: str = "unknown") -> dict[str, object]:
    if not isinstance(item, dict):
        raise TypeError("expected a dict-like article payload")

    normalized = dict(item)
    source = str(normalized.get("source") or normalized.get("provider") or default_source)
    title = str(normalized.get("title") or normalized.get("headline") or "Untitled").strip()
    content = str(normalized.get("content") or normalized.get("text") or title).strip()
    symbols = normalized.get("symbols") or normalized.get("symbol") or []
    if isinstance(symbols, str):
        list_symbols = [symbols]
    elif isinstance(symbols, (list, tuple, set)):
        list_symbols = list(symbols)
    else:
        list_symbols = []
    normalized_symbols = [str(symbol).upper().strip() for symbol in list_symbols if str(symbol).strip()]
    if not normalized_symbols and normalized.get("symbol"):
        normalized_symbols = [str(normalized["symbol"]).upper().strip()]
    if not normalized_symbols:
        normalized_symbols = ["UNKNOWN"]

    published_at = normalized.get("published_at") or normalized.get("published") or normalized.get("datetime")
    if published_at is None:
        published_at = datetime.now(timezone.utc).isoformat()
    else:
        published_text = str(published_at).strip()
        if published_text.endswith("Z"):
            published_text = published_text[:-1] + "+00:00"
        try:
            published_dt = datetime.fromisoformat(published_text)
            if published_dt.tzinfo is None:
                published_dt = published_dt.replace(tzinfo=timezone.utc)
            published_at = published_dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            published_at = published_text

    normalized["source"] = source
    normalized["title"] = title
    normalized["content"] = content
    normalized["published_at"] = published_at
    normalized["url"] = str(normalized.get("url") or normalized.get("link") or "")
    normalized["symbols"] = normalized_symbols
    normalized["symbol"] = normalized_symbols[0]
    return normalized


class JsonFeedSource(NewsSource):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_articles(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("articles", [])
        items: list[dict[str, object]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            items.append(normalize_news_article(item, default_source=self.path.stem))
        return items


class FinnhubNewsSource(NewsSource):
    def __init__(self, api_key: str | None = None, symbols: Iterable[str] | None = None) -> None:
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY")
        self.symbols = tuple(symbols or ())

    def fetch_articles(self) -> list[dict[str, object]]:
        if not self.api_key:
            return []
        url = "https://finnhub.io/api/v1/news?category=general"
        req = request.Request(url, headers={"X-Finnhub-Token": self.api_key})
        try:
            with request.urlopen(req, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.URLError, ValueError, TimeoutError):
            return []
        items: list[dict[str, object]] = []
        for article in payload:
            if not isinstance(article, dict):
                continue
            normalized = normalize_news_article(article, default_source="finnhub")
            if self.symbols:
                normalized["symbols"] = [str(symbol).upper() for symbol in self.symbols]
                normalized["symbol"] = normalized["symbols"][0]
            items.append(normalized)
        return items


class FeedRegistry:
    def __init__(self, sources: Iterable[NewsSource] | None = None) -> None:
        self._sources: list[NewsSource] = list(sources or ())

    def register(self, source: NewsSource) -> None:
        self._sources.append(source)

    def fetch_articles(self) -> list[dict[str, object]]:
        articles: list[dict[str, object]] = []
        for source in self._sources:
            for item in source.fetch_articles():
                if not isinstance(item, dict):
                    continue
                articles.append(normalize_news_article(item, default_source=getattr(source, "__class__", type(source)).__name__))
        return articles


def build_default_news_sources(symbols: Iterable[str] | None = None) -> FeedRegistry:
    configured_symbols = tuple(symbols or ())
    sources: list[NewsSource] = []

    api_key = os.getenv("FINNHUB_API_KEY")
    if api_key:
        sources.append(FinnhubNewsSource(api_key=api_key, symbols=configured_symbols))

    fallback_path = Path(__file__).resolve().parents[1] / "data" / "samples" / "news.json"
    if fallback_path.exists():
        sources.append(JsonFeedSource(fallback_path))

    if not sources:
        return FeedRegistry()
    return FeedRegistry(sources)
