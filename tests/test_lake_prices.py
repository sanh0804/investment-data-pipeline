"""Offline tests for silver prices: selection rule, rejects, upserts and scale guard."""
import json
import random
from datetime import date, datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pytest
from deltalake import DeltaTable

from pipeline.lake import arrow_table, query, upsert_changed
from pipeline.lake_prices import KEY, PRICES_SCHEMA, latest_prices


def epoch(day):
    return int(datetime(2026, 10, day, tzinfo=timezone.utc).timestamp())


def body(days, close):
    # Coinbase order: time, low, high, open, close, volume.
    return json.dumps([[epoch(d), 1, close + 1, close, close, 2.5] for d in days]).encode()


def attempt(fetch_id, minute, start, end):
    return {"fetch_id": fetch_id, "source": "coinbase_exchange", "product_id": "BTC-USD",
            "request_params": json.dumps({"start": f"2026-10-{start:02d}T00:00:00Z",
                                          "end": f"2026-10-{end:02d}T00:00:00Z"}),
            "fetched_at": datetime(2026, 11, 1, 0, minute, tzinfo=timezone.utc),
            "object_key": fetch_id}


def test_latest_covering_attempt_wins_regardless_of_input_order():
    bodies = {"old": body([1, 2, 3], 100), "new": body([2, 3], 200)}
    attempts = [attempt("old", 1, 1, 4), attempt("new", 2, 2, 4)]
    random.Random(7).shuffle(attempts)
    rows, rejected = latest_prices(attempts, bodies.__getitem__)
    assert rejected == []
    assert [(r["price_date"].day, r["close_price"], r["last_fetch_id"]) for r in rows] == [
        (1, Decimal("100"), "old"), (2, Decimal("200"), "new"), (3, Decimal("200"), "new")]


def test_invalid_body_is_skipped_and_reported():
    bodies = {"good": body([1], 100), "short": body([1], 300)}  # window asks for 2 days
    rows, rejected = latest_prices([attempt("good", 1, 1, 2), attempt("short", 2, 1, 3)],
                                   bodies.__getitem__)
    assert [r["last_fetch_id"] for r in rows] == ["good"]
    assert rejected[0]["fetch_id"] == "short" and "missing UTC days" in rejected[0]["error"]


def price(day, close):
    return {"source": "coinbase_exchange", "product_id": "BTC-USD", "price_date": date(2026, 10, day),
            "bucket_start": datetime(2026, 10, day, tzinfo=timezone.utc),
            "open_price": close, "high_price": close, "low_price": close, "close_price": close,
            "volume_base": Decimal("2.5"), "last_fetch_id": "f1"}


def test_upsert_writes_only_changes_and_keeps_history(tmp_path):
    uri = str(tmp_path / "prices")
    rows = [price(1, Decimal("100.10")), price(2, Decimal("101"))]
    assert upsert_changed(uri, {}, PRICES_SCHEMA, KEY, rows) == (2, 0)
    assert upsert_changed(uri, {}, PRICES_SCHEMA, KEY, rows) == (0, 0)  # 100.10 == 100.100000...

    corrected = [rows[0], price(2, Decimal("102"))]
    assert upsert_changed(uri, {}, PRICES_SCHEMA, KEY, corrected) == (1, 1)
    current = query(DeltaTable(uri), "SELECT close_price FROM t ORDER BY price_date").column(0).to_pylist()
    before = query(DeltaTable(uri, version=0), "SELECT close_price FROM t ORDER BY price_date").column(0).to_pylist()
    assert (current[1], before[1]) == (Decimal("102"), Decimal("101"))


def test_values_beyond_scale_are_rejected_not_rounded():
    with pytest.raises(pa.ArrowInvalid):
        arrow_table([price(1, Decimal("1.0000000000000000001"))], PRICES_SCHEMA)
