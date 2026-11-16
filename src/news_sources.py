from __future__ import annotations

import json
import email.utils
import os
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import error, request
from urllib.parse import urlencode
from xml.etree import ElementTree

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
        configured_symbols = {str(symbol).strip().upper() for symbol in self.symbols}
        for article in payload:
            if not isinstance(article, dict):
                continue
            related_value = article.get("related") or ""
            if isinstance(related_value, str):
                related_symbols = {
                    symbol.strip().upper()
                    for symbol in related_value.split(",")
                    if symbol.strip()
                }
            elif isinstance(related_value, (list, tuple, set)):
                related_symbols = {str(symbol).strip().upper() for symbol in related_value if str(symbol).strip()}
            else:
                related_symbols = set()
            matched_symbols = [symbol for symbol in self.symbols if str(symbol).strip().upper() in related_symbols & configured_symbols]
            if not matched_symbols:
                continue
            normalized = normalize_news_article(article, default_source="finnhub")
            normalized["symbols"] = [str(symbol).strip().upper() for symbol in matched_symbols]
            normalized["symbol"] = normalized["symbols"][0]
            items.append(normalized)
        return items


_IMPORTANT_8K_ITEMS = {"1.03", "1.05", "2.02", "2.04", "2.06", "3.01", "4.02", "5.01", "5.02"}
_8K_ITEM_LABELS = {
    "1.03": "bankruptcy",
    "1.05": "cybersecurity incident",
    "2.02": "earnings results",
    "2.04": "debt default or acceleration",
    "2.06": "impairment",
    "3.01": "delisting notice",
    "4.02": "past financials no longer reliable",
    "5.01": "change in control",
    "5.02": "officer or director change",
}


def _feed_datetime(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_yahoo_rss(payload: bytes | str, symbol: str) -> list[dict[str, object]]:
    root = ElementTree.fromstring(payload)
    articles: list[dict[str, object]] = []
    for item in root.iter("item"):
        def text(name: str) -> str:
            element = item.find(name)
            return "" if element is None or element.text is None else element.text.strip()

        title = text("title")
        if not title:
            continue
        published_at = _feed_datetime(text("pubDate"))
        articles.append(
            {
                "source": "Yahoo Finance",
                "title": title,
                "content": text("description") or title,
                "published_at": published_at,
                "url": text("link"),
                "symbols": [symbol.upper()],
                "symbol": symbol.upper(),
                "external_id": text("guid"),
                "kind": "headline",
            }
        )
    return articles


def parse_sec_atom(payload: bytes | str, symbol: str) -> list[dict[str, object]]:
    root = ElementTree.fromstring(payload)

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    articles: list[dict[str, object]] = []
    for entry in root.iter():
        if local_name(entry.tag) != "entry":
            continue
        fields = {
            local_name(element.tag): (element.text or "").strip()
            for element in entry.iter()
            if element is not entry
        }
        form = fields.get("filing-type", "filing")
        item_codes = sorted(set(re.findall(r"\d+\.\d+", fields.get("items-desc", ""))))
        labels = [_8K_ITEM_LABELS.get(code, f"item {code}") for code in item_codes]
        title = f"{symbol.upper()} filed a {form}"
        if labels:
            title += ": " + ", ".join(labels)
        important = bool(_IMPORTANT_8K_ITEMS.intersection(item_codes))
        if important:
            title = "! " + title
        link = next(
            (element.get("href", "") for element in entry.iter() if local_name(element.tag) == "link"),
            "",
        )
        articles.append(
            {
                "source": "SEC EDGAR",
                "title": title,
                "content": fields.get("form-name") or title,
                "published_at": _feed_datetime(fields.get("updated", "")),
                "url": link,
                "symbols": [symbol.upper()],
                "symbol": symbol.upper(),
                "external_id": fields.get("accession-number", ""),
                "kind": "filing",
                "form": form,
                "items": item_codes,
                "important": important,
            }
        )
    return articles


class PublicFeedNewsSource(NewsSource):
    def __init__(
        self,
        symbols: Iterable[str],
        *,
        sec_user_agent: str | None = None,
        timeout: float = 15,
    ) -> None:
        self.symbols = tuple(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()))
        candidate_user_agent = sec_user_agent or os.getenv("SEC_USER_AGENT")
        contact_email = email.utils.parseaddr(candidate_user_agent or "")[1].casefold()
        email_domain = contact_email.rsplit("@", 1)[-1]
        is_example_domain = any(
            email_domain == domain or email_domain.endswith("." + domain)
            for domain in ("example.com", "example.org", "example.net")
        )
        self.sec_user_agent = (
            candidate_user_agent
            if candidate_user_agent
            and contact_email
            and not is_example_domain
            and "your.name" not in candidate_user_agent.casefold()
            else None
        )
        self.timeout = timeout
        self._last_sec_request = 0.0

    def _fetch(self, url: str, *, user_agent: str) -> bytes:
        req = request.Request(url, headers={"User-Agent": user_agent})
        with request.urlopen(req, timeout=self.timeout) as response:
            return response.read()

    def _fetch_sec_url(self, url: str) -> bytes:
        if self._last_sec_request:
            elapsed = time.monotonic() - self._last_sec_request
            if elapsed < 0.15:
                time.sleep(0.15 - elapsed)
        self._last_sec_request = time.monotonic()
        return self._fetch(url, user_agent=self.sec_user_agent or "")

    def _fetch_sec(self, symbol: str, cik: str) -> bytes:
        query = urlencode(
            {
                "action": "getcompany",
                "CIK": cik,
                "type": "8-K",
                "dateb": "",
                "owner": "include",
                "count": 10,
                "output": "atom",
            }
        )
        return self._fetch_sec_url("https://www.sec.gov/cgi-bin/browse-edgar?" + query)

    def fetch_articles(self) -> list[dict[str, object]]:
        articles: list[dict[str, object]] = []
        cik_by_symbol: dict[str, str] | None = None
        for symbol in self.symbols:
            yahoo_url = "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urlencode(
                {"s": symbol, "region": "US", "lang": "en-US"}
            )
            try:
                articles.extend(
                    parse_yahoo_rss(
                        self._fetch(yahoo_url, user_agent="QuantResearch/1.0"),
                        symbol,
                    )
                )
            except (error.URLError, ElementTree.ParseError, ValueError, TimeoutError):
                pass

            if self.sec_user_agent:
                try:
                    if cik_by_symbol is None:
                        payload = self._fetch_sec_url(
                            "https://www.sec.gov/files/company_tickers.json"
                        )
                        records = json.loads(payload.decode("utf-8"))
                        if isinstance(records, dict):
                            records = records.values()
                        cik_by_symbol = {
                            str(record["ticker"]).upper(): str(record["cik_str"]).zfill(10)
                            for record in records
                            if isinstance(record, dict) and record.get("ticker") and record.get("cik_str")
                        }
                    cik = cik_by_symbol.get(symbol)
                    if cik:
                        articles.extend(parse_sec_atom(self._fetch_sec(symbol, cik), symbol))
                except (error.URLError, ElementTree.ParseError, ValueError, TimeoutError, KeyError):
                    cik_by_symbol = cik_by_symbol or {}
        return articles


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

    if configured_symbols:
        sources.append(PublicFeedNewsSource(configured_symbols))

    fallback_path = Path(__file__).resolve().parents[1] / "data" / "samples" / "news.json"
    if fallback_path.exists():
        sources.append(JsonFeedSource(fallback_path))

    if not sources:
        return FeedRegistry()
    return FeedRegistry(sources)
