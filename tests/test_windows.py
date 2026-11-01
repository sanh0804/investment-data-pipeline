import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from pipeline.collector import collect
from pipeline.config import Settings, date_windows, endpoint
from pipeline.normalize import normalize


def test_long_range_windows_cover_once_with_safe_boundaries():
    start, end = date(2025, 1, 1), date(2026, 10, 28)
    windows = list(date_windows(start, end, 299))
    days = [a + timedelta(days=i) for a, b in windows for i in range((b-a).days)]
    assert len(days) == (end-start).days == len(set(days))
    assert days[0] == start and days[-1] == end-timedelta(days=1)
    assert all((b-a).days + 1 <= 300 for a, b in windows)
    with pytest.raises(ValueError):
        list(date_windows(start, end, 300))
    with pytest.raises(ValueError):
        endpoint("UNKNOWN-USD")


def test_inclusive_boundary_response_does_not_duplicate_neighboring_day():
    start, mid, end = date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)
    ts = lambda d: int(datetime.combine(d, datetime.min.time(), timezone.utc).timestamp())
    def body(a,b):
        return json.dumps([[ts(a),10,20,12,18,1], [ts(b),10,20,12,18,1]])
    candles = normalize(body(start, mid), start, mid) + normalize(body(mid, end), mid, end)
    assert [c.bucket_start.date() for c in candles] == [start, mid]


def test_missing_later_window_never_writes_partial_normalized_batch(monkeypatch):
    settings = Settings(date(2026,5,1),date(2026,5,3),1,2,1,0,30,("BTC-USD",),1,0)
    timestamp = int(datetime(2026,5,1,tzinfo=timezone.utc).timestamp())
    fetch_mock = Mock(side_effect=[("first",json.dumps([[timestamp,10,20,12,18,1]])), ("second","[]")])
    write_mock = Mock()
    monkeypatch.setattr("pipeline.collector.fetch", fetch_mock)
    monkeypatch.setattr("pipeline.collector.upsert", write_mock)
    with pytest.raises(ValueError, match="BTC-USD.*Incomplete"):
        collect(Mock(), Mock(), settings)
    assert fetch_mock.call_count == 2
    write_mock.assert_not_called()


def test_missing_other_product_never_writes_first_product(monkeypatch):
    settings = Settings(date(2026,5,1),date(2026,5,2),1,2,1,0,30,("BTC-USD","ETH-USD"),60,0)
    timestamp = int(datetime(2026,5,1,tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr("pipeline.collector.fetch", Mock(side_effect=[("btc",json.dumps([[timestamp,10,20,12,18,1]])),("eth","[]")]))
    write_mock = Mock()
    monkeypatch.setattr("pipeline.collector.upsert", write_mock)
    with pytest.raises(ValueError, match="ETH-USD.*Incomplete"):
        collect(Mock(), Mock(), settings)
    write_mock.assert_not_called()
