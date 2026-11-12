import csv
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol


class PriceProvider(Protocol):
    def get_price(self, symbol: str, when: datetime) -> float | None:
        """Return the latest known price no later than when."""
        ...


class CsvPriceProvider:
    def __init__(self, csv_path: str | Path = "data/prices.csv") -> None:
        self._prices: dict[str, list[tuple[datetime, float]]] = {}
        with Path(csv_path).open(newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            if not {"timestamp", "symbol", "price"} <= set(reader.fieldnames or ()):
                raise ValueError("CSV must contain timestamp, symbol, and price columns")
            for row in reader:
                timestamp_text = row["timestamp"].strip()
                if timestamp_text.endswith("Z"):
                    timestamp_text = timestamp_text[:-1] + "+00:00"
                timestamp = datetime.fromisoformat(timestamp_text)
                if timestamp.utcoffset() != timedelta(0):
                    raise ValueError("price timestamps must be UTC")
                symbol = row["symbol"].strip()
                price = float(row["price"])
                if not symbol or not math.isfinite(price) or price <= 0:
                    raise ValueError("CSV contains an invalid symbol or price")
                self._prices.setdefault(symbol, []).append(
                    (timestamp.astimezone(timezone.utc), price)
                )

        for prices in self._prices.values():
            prices.sort(key=lambda quote: quote[0])

    def get_price(self, symbol: str, when: datetime) -> float | None:
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("when must be a timezone-aware datetime")
        target = when.astimezone(timezone.utc)
        last_price = None
        for timestamp, price in self._prices.get(symbol, ()):
            if timestamp > target:
                break
            last_price = price
        return last_price
