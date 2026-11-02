"""Replay a stored response in two committed transactions and compare business rows."""
import argparse
import hashlib
import json
from datetime import date
from uuid import UUID

from pipeline.db import connect, upsert
from pipeline.normalize import normalize


def snapshot(conn, start, end, product="BTC-USD"):
    return conn.execute(
        """SELECT source, product_id, price_date, bucket_start,
                  open_price, high_price, low_price, close_price, volume_base
           FROM market.daily_prices WHERE source = 'coinbase_exchange'
             AND product_id = %s AND price_date >= %s AND price_date < %s
           ORDER BY price_date""", (product, start, end),
    ).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-id", type=UUID, required=True)
    args = parser.parse_args()
    with connect() as conn:
        row = conn.execute(
            """SELECT response_body, response_sha256, request_params, http_status, product_id
               FROM raw.price_fetches WHERE fetch_id = %s""", (args.fetch_id,),
        ).fetchone()
        if row is None or row[3] != 200:
            raise ValueError("Choose an existing successful live raw fetch")
        body, sha, params, _, product = row
        if hashlib.sha256(body).hexdigest() != sha:
            raise ValueError("Raw response checksum mismatch")
        start, end = date.fromisoformat(params["start"][:10]), date.fromisoformat(params["end"][:10])
        candles = normalize(body, start, end)
        # Separate committed transactions: this checks reruns, not just one transaction.
        conn.commit()
        with conn.transaction():
            upsert(conn, candles, args.fetch_id, product)
            first = snapshot(conn, start, end, product)
        with conn.transaction():
            upsert(conn, candles, args.fetch_id, product)
            second = snapshot(conn, start, end, product)
        if first != second or len(second) != len(candles):
            raise AssertionError("Idempotency check failed: row count or values changed")
        print(json.dumps({"check": "exact_live_response_replayed_twice", "passed": True,
                          "fetch_id": str(args.fetch_id), "sha256": sha,
                          "rows_after_first": len(first), "rows_after_second": len(second),
                          "business_values_equal": first == second}))


if __name__ == "__main__":
    main()
