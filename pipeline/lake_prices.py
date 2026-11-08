"""Silver daily prices rebuilt only from bronze objects listed in the Delta manifest.

The selection rule mirrors what market.daily_prices ended up holding: for each
(source, product_id, price_date), the latest successful attempt whose request window
covers the day wins (fetched_at, then fetch_id). Checked on 2026-11-09 against all
555 PostgreSQL rows, including last_fetch_id. Parsing reuses pipeline.normalize, so
there is one definition of a valid candle.
"""
import argparse
import json
from datetime import datetime

import pyarrow as pa
from deltalake import DeltaTable

from pipeline.lake import (MANIFEST_SCHEMA, UTC_MICROS, check_table_schema, load_lake_settings,
                           query, read_object, s3_client, upsert_changed)
from pipeline.normalize import normalize

PRICES_PATH = "silver/prices"
# PostgreSQL NUMERIC is unbounded; observed scale is 2 (prices) and 8 (volume).
# Scale 18 leaves room; anything finer is rejected by pyarrow, never rounded.
AMOUNT = pa.decimal128(38, 18)
KEY = ("source", "product_id", "price_date")
PRICES_SCHEMA = pa.schema([
    pa.field("source", pa.string(), nullable=False),
    pa.field("product_id", pa.string(), nullable=False),
    pa.field("price_date", pa.date32(), nullable=False),
    pa.field("bucket_start", UTC_MICROS, nullable=False),
    pa.field("open_price", AMOUNT, nullable=False),
    pa.field("high_price", AMOUNT, nullable=False),
    pa.field("low_price", AMOUNT, nullable=False),
    pa.field("close_price", AMOUNT, nullable=False),
    pa.field("volume_base", AMOUNT, nullable=False),
    pa.field("last_fetch_id", pa.string(), nullable=False),
])


def request_window(request_params):
    params = json.loads(request_params)
    start, end = (datetime.fromisoformat(params[k]).date() for k in ("start", "end"))
    return start, end


def successful_attempts(manifest):
    return query(manifest, """
        SELECT fetch_id, source, product_id, request_params, fetched_at, object_key
        FROM t WHERE http_status = 200 AND object_key IS NOT NULL
        ORDER BY fetched_at, fetch_id""").to_pylist()


def latest_prices(attempts, read_body):
    """Return (rows, rejected): one row per key from the latest covering attempt.

    A 200 body that fails validation is skipped, as the collector would have
    refused to load it; the count is reported so it cannot pass unnoticed.
    """
    winners, rejected = {}, []
    for attempt in sorted(attempts, key=lambda a: (a["fetched_at"], a["fetch_id"])):
        start, end = request_window(attempt["request_params"])
        try:
            candles = normalize(read_body(attempt["object_key"]), start, end)
        except ValueError as exc:
            rejected.append({"fetch_id": attempt["fetch_id"], "error": str(exc)[:200]})
            continue
        for c in candles:
            row = {"source": attempt["source"], "product_id": attempt["product_id"],
                   "price_date": c.bucket_start.date(), "bucket_start": c.bucket_start,
                   "open_price": c.open, "high_price": c.high, "low_price": c.low,
                   "close_price": c.close, "volume_base": c.volume,
                   "last_fetch_id": attempt["fetch_id"]}
            winners[tuple(row[k] for k in KEY)] = row  # later attempt replaces earlier
    return [winners[k] for k in sorted(winners)], rejected


def build(settings, s3, target_uri=None, storage_options=None):
    options = settings.storage_options() if storage_options is None else storage_options
    manifest = DeltaTable(settings.manifest_uri, storage_options=settings.storage_options())
    check_table_schema(manifest, MANIFEST_SCHEMA)
    attempts = successful_attempts(manifest)
    rows, rejected = latest_prices(attempts, lambda key: read_object(s3, settings.bucket, key))
    uri = target_uri or f"s3://{settings.bucket}/{PRICES_PATH}"
    written, version = upsert_changed(uri, options, PRICES_SCHEMA, KEY, rows)
    return {"manifest_version": manifest.version(), "attempts_read": len(attempts),
            "rejected_attempts": rejected, "price_rows": len(rows),
            "rows_written": written, "silver_version": version, "uri": uri}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target-path", help="local path for a rebuild check instead of the bucket")
    args = parser.parse_args()
    settings = load_lake_settings()
    report = build(settings, s3_client(settings), args.target_path, {} if args.target_path else None)
    print(json.dumps(report, default=str))


if __name__ == "__main__":
    main()
