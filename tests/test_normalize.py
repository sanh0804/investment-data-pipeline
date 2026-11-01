import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipeline.normalize import normalize

# Synthetic values for unit tests only; never loaded by the collector.
START = date(2026, 10, 25)
END = date(2026, 10, 27)
TS = int(datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp())


def candle(timestamp=TS):
    return [timestamp, 10, 20, 12, 18, 5]


def test_reverse_order_and_exclusive_end():
    body = json.dumps([candle(TS + 2 * 86400), candle(TS + 86400), candle()])
    rows = normalize(body, START, END)
    assert [r.bucket_start.date() for r in rows] == [START, date(2026, 10, 26)]
    assert all(r.bucket_start.tzinfo == timezone.utc for r in rows)


def test_decimal_precision_without_float_roundtrip():
    body = f'[[{TS},10,20,12,18.123456789123456789,5]]'
    assert normalize(body, START, date(2026, 10, 26))[0].close == Decimal("18.123456789123456789")


@pytest.mark.parametrize("payload", [
    {}, [], [candle(), candle()],  # Object, missing day, duplicate day.
    [candle(TS * 1000)], [candle(TS + 1)],  # Milliseconds or misaligned UTC.
    [[TS, 10, 20, 12, 21, 5]],  # Close outside bounds.
    [[TS, 10, 20, 12, 18, -1]],
    [[TS, 10, 20, 12, "NaN", 5]],
    [[TS, 10, 20, 12, True, 5]],
    [[TS, 10, 20, 12, 18]],
])
def test_invalid_response_is_rejected(payload):
    with pytest.raises((ValueError, OverflowError)):
        normalize(json.dumps(payload), START, date(2026, 10, 26))


def test_incomplete_current_day_is_rejected():
    today = datetime.now(timezone.utc).date()
    timestamp = int(datetime.combine(today, datetime.min.time(), timezone.utc).timestamp())
    from datetime import timedelta
    with pytest.raises(ValueError, match="completed"):
        normalize(json.dumps([candle(timestamp)]), today, today + timedelta(days=1))
