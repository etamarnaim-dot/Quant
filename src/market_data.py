from __future__ import annotations

import csv
import json
import math
import os
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request

from dotenv import load_dotenv

from .prices import Quote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESEARCH_UNIVERSE = (
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "AVGO", "NFLX",
)
DEFAULT_PRICE_PATH = PROJECT_ROOT / "data" / "prices.csv"

load_dotenv(PROJECT_ROOT / ".env")


def default_research_symbols() -> tuple[str, ...]:
    configured = os.getenv("RESEARCH_SYMBOLS", "")
    if not configured.strip():
        return DEFAULT_RESEARCH_UNIVERSE
    symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in configured.split(",") if symbol.strip()))
    if not symbols:
        raise ValueError("RESEARCH_SYMBOLS must contain at least one ticker")
    return symbols


def volatility_rankings(csv_path: str | Path = DEFAULT_PRICE_PATH, *, limit: int = 10) -> list[dict[str, object]]:
    if limit < 1:
        raise ValueError("limit must be positive")
    path = Path(csv_path)
    if not path.is_file():
        return []

    prices_by_symbol: dict[str, list[tuple[datetime, float]]] = {}
    with path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        if not {"timestamp", "symbol", "price"} <= set(reader.fieldnames or ()):
            return []
        for row in reader:
            try:
                timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                price = float(row["price"])
            except (TypeError, ValueError):
                continue
            symbol = row["symbol"].strip().upper()
            if not symbol or timestamp.tzinfo is None or not math.isfinite(price) or price <= 0:
                continue
            prices_by_symbol.setdefault(symbol, []).append((timestamp.astimezone(timezone.utc), price))

    rankings = []
    for symbol, history in prices_by_symbol.items():
        history.sort(key=lambda item: item[0])
        returns = [current / previous - 1 for (_, previous), (_, current) in zip(history, history[1:])]
        if not returns:
            continue
        absolute_returns = [abs(value) for value in returns]
        rankings.append(
            {
                "symbol": symbol,
                "latest_price": history[-1][1],
                "daily_return_std": statistics.stdev(returns) if len(returns) > 1 else 0.0,
                "average_absolute_return": statistics.mean(absolute_returns),
                "max_absolute_return": max(absolute_returns),
                "days_seen": len(history),
            }
        )
    rankings.sort(
        key=lambda item: (
            item["daily_return_std"],
            item["average_absolute_return"],
            item["max_absolute_return"],
        ),
        reverse=True,
    )
    return rankings[:limit]


class FinnhubDailyPriceRefresher:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 15,
        request_interval_seconds: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY")
        self.timeout = timeout
        self.request_interval_seconds = request_interval_seconds
        self._last_request = 0.0

    def refresh(
        self,
        csv_path: str | Path,
        symbols: Iterable[str],
        *,
        start: datetime,
        end: datetime,
    ) -> int:
        if not self.api_key:
            raise ValueError("FINNHUB_API_KEY is required to refresh daily prices")
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("end must be timezone-aware")
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        if start_utc >= end_utc:
            raise ValueError("start must be earlier than end")

        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        prices: dict[tuple[str, str], float] = {}
        if path.exists():
            with path.open(newline="", encoding="utf-8") as csv_file:
                reader = csv.DictReader(csv_file)
                if not {"timestamp", "symbol", "price"} <= set(reader.fieldnames or ()):
                    raise ValueError("CSV must contain timestamp, symbol, and price columns")
                for row in reader:
                    stamp = self._normalize_timestamp(row["timestamp"])
                    symbol = row["symbol"].strip().upper()
                    price = float(row["price"])
                    if symbol and math.isfinite(price) and price > 0:
                        prices[(stamp, symbol)] = price

        added = 0
        for symbol in dict.fromkeys(str(value).strip().upper() for value in symbols if str(value).strip()):
            query = parse.urlencode(
                {
                    "symbol": symbol,
                    "resolution": "D",
                    "from": int(start_utc.timestamp()),
                    "to": int(end_utc.timestamp()),
                    "token": self.api_key,
                }
            )
            req = request.Request(f"https://finnhub.io/api/v1/stock/candle?{query}")
            try:
                elapsed = time.monotonic() - self._last_request
                if self._last_request and elapsed < self.request_interval_seconds:
                    time.sleep(self.request_interval_seconds - elapsed)
                self._last_request = time.monotonic()
                with request.urlopen(req, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (error.URLError, TimeoutError, ValueError) as exc:
                raise RuntimeError(f"Finnhub daily candles failed for {symbol}") from exc
            if not isinstance(payload, dict) or payload.get("s") != "ok":
                continue
            timestamps = payload.get("t", [])
            closes = payload.get("c", [])
            for timestamp, close in zip(timestamps, closes):
                try:
                    price = float(close)
                    quote_time = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
                except (TypeError, ValueError, OverflowError, OSError):
                    continue
                if not math.isfinite(price) or price <= 0 or quote_time > end_utc:
                    continue
                stamp = quote_time.isoformat().replace("+00:00", "Z")
                key = (stamp, symbol)
                if key not in prices:
                    added += 1
                prices[key] = price

        self._write_csv(path, prices)
        return added

    @staticmethod
    def _normalize_timestamp(value: str) -> str:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("price timestamps must be timezone-aware")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _write_csv(path: Path, prices: dict[tuple[str, str], float]) -> None:
        with tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8", dir=path.parent, delete=False
        ) as csv_file:
            temporary_path = Path(csv_file.name)
            writer = csv.writer(csv_file)
            writer.writerow(("timestamp", "symbol", "price"))
            for (timestamp, symbol), price in sorted(prices.items(), key=lambda item: (item[0][1], item[0][0])):
                writer.writerow((timestamp, symbol, f"{price:.8f}"))
        temporary_path.replace(path)


class FinnhubQuoteProvider:
    def __init__(self, api_key: str | None = None, *, timeout: float = 15) -> None:
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY")
        self.timeout = timeout

    def get_quote(self, symbol: str, when: datetime) -> Quote | None:
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("when must be timezone-aware")
        if not self.api_key:
            return None
        query = parse.urlencode({"symbol": symbol.upper(), "token": self.api_key})
        req = request.Request(f"https://finnhub.io/api/v1/quote?{query}")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            price = float(payload["c"])
            timestamp = datetime.fromtimestamp(float(payload["t"]), tz=timezone.utc)
        except (error.URLError, TimeoutError, ValueError, TypeError, KeyError, OverflowError, OSError):
            return None
        if not math.isfinite(price) or price <= 0 or timestamp > when.astimezone(timezone.utc):
            return None
        return Quote(price, timestamp)

    def get_price(self, symbol: str, when: datetime) -> float | None:
        quote = self.get_quote(symbol, when)
        return None if quote is None else quote.price