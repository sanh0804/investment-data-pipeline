-- Inspect daily close and provenance.
SELECT source, product_id, price_date, bucket_start, close_price, last_fetch_id
FROM market.daily_prices ORDER BY price_date;

-- Must return no rows.
SELECT source, product_id, price_date, count(*)
FROM market.daily_prices
GROUP BY source, product_id, price_date HAVING count(*) > 1;

-- Raw data intentionally grows per attempt. Failed attempts are visible here.
SELECT fetch_id, run_id, attempt_number, fetched_at, http_status,
       octet_length(response_body) AS body_bytes, response_sha256, transport_error
FROM raw.price_fetches ORDER BY fetched_at;

-- Must return zero; UTC definition independent of session timezone.
SELECT count(*) AS invalid_utc_buckets FROM market.daily_prices
WHERE bucket_start <> price_date::timestamp AT TIME ZONE 'UTC';

-- Completeness for the documented demonstration range: must return no rows.
WITH expected AS (
    SELECT d::date AS price_date
    FROM generate_series(timestamp '2026-10-25', timestamp '2026-10-27', interval '1 day') AS d
)
SELECT e.price_date AS missing_date FROM expected e
LEFT JOIN market.daily_prices p
  ON p.price_date = e.price_date AND p.source = 'coinbase_exchange' AND p.product_id = 'BTC-USD'
WHERE p.price_date IS NULL;
