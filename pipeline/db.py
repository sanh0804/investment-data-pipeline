import os

import psycopg
from dotenv import load_dotenv

from pipeline.config import PRODUCT, ROOT, SOURCE


def connect():
    load_dotenv(ROOT / ".env")
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "55432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
        options="-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000",
    )


def upsert(conn, candles, fetch_id):
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO market.daily_prices
                (source, product_id, price_date, bucket_start, open_price,
                 high_price, low_price, close_price, volume_btc, last_fetch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, product_id, price_date) DO UPDATE SET
                bucket_start = EXCLUDED.bucket_start,
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                volume_btc = EXCLUDED.volume_btc,
                last_fetch_id = EXCLUDED.last_fetch_id,
                loaded_at = CURRENT_TIMESTAMP
            """,
            [(SOURCE, PRODUCT, c.bucket_start.date(), c.bucket_start,
              c.open, c.high, c.low, c.close, c.volume, fetch_id) for c in candles],
        )
