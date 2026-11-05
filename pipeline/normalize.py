import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


class IncompleteResponse(ValueError):
    """A valid response is missing requested daily buckets."""


@dataclass(frozen=True)
class Candle:
    bucket_start: datetime
    low: Decimal
    high: Decimal
    open: Decimal
    close: Decimal
    volume: Decimal


def normalize(body: str | bytes, start: date, end: date) -> list[Candle]:
    payload = json.loads(body, parse_float=Decimal)
    if not isinstance(payload, list):
        raise ValueError("Expected a JSON array of candles")
    candles = {}
    for row in payload:
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError("Expected six fields: time, low, high, open, close, volume")
        timestamp = row[0]
        if type(timestamp) is not int or timestamp % 86400 != 0:
            raise ValueError("Expected epoch seconds aligned to midnight UTC")
        bucket = datetime.fromtimestamp(timestamp, timezone.utc)
        # Provider may return candles outside the requested interval.
        if not start <= bucket.date() < end:
            continue
        if bucket.date() >= datetime.now(timezone.utc).date():
            raise ValueError("Only completed UTC days can be normalized")
        try:
            if any(isinstance(value, bool) for value in row[1:]):
                raise ValueError("Boolean price/volume is invalid")
            low, high, opening, close, volume = (Decimal(str(v)) for v in row[1:])
        except InvalidOperation as exc:
            raise ValueError("Invalid numeric candle field") from exc
        values = (low, high, opening, close, volume)
        if not all(v.is_finite() for v in values):
            raise ValueError("Candle values must be finite")
        if min(low, high, opening, close) <= 0 or volume < 0:
            raise ValueError("Prices must be positive; volume must be non-negative")
        if not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError("Inconsistent OHLC bounds")
        if bucket.date() in candles:
            raise ValueError("Duplicate day in provider response")
        candles[bucket.date()] = Candle(bucket, low, high, opening, close, volume)
    expected = {start + timedelta(days=i) for i in range((end - start).days)}
    if set(candles) != expected:
        missing = sorted(expected - set(candles))
        raise IncompleteResponse(f"Incomplete response; missing UTC days: {missing}")
    return [candles[day] for day in sorted(candles)]
