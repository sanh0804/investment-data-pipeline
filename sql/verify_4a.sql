-- Fixed live verification range: one manual day plus three backfill days.
SELECT product_id, count(*) AS days, min(price_date), max(price_date)
FROM market.daily_prices
WHERE source='coinbase_exchange'
  AND price_date >= DATE '2026-10-28' AND price_date < DATE '2026-11-01'
GROUP BY product_id ORDER BY product_id;

WITH expected AS (
 SELECT p, d::date AS price_date
 FROM unnest(ARRAY['BTC-USD','ETH-USD','SOL-USD']) p
 CROSS JOIN generate_series(DATE '2026-10-28',DATE '2026-10-31',INTERVAL '1 day') d
)
SELECT count(*) AS missing_keys FROM expected e
LEFT JOIN market.daily_prices m ON m.source='coinbase_exchange'
 AND m.product_id=e.p AND m.price_date=e.price_date
WHERE m.price_date IS NULL;

SELECT count(*) AS invalid_lineage_or_utc FROM market.daily_prices p
LEFT JOIN raw.price_fetches r ON r.fetch_id=p.last_fetch_id
WHERE p.price_date >= DATE '2026-10-28' AND p.price_date < DATE '2026-11-01'
AND (r.fetch_id IS NULL OR r.http_status<>200 OR r.product_id<>p.product_id
 OR r.source<>p.source OR p.bucket_start<>(p.price_date::timestamp AT TIME ZONE 'UTC'));

-- Fixed portfolio analytics remain a separate snapshot.
SELECT count(*) AS trades,sum(notional_usd) AS volume_usd,sum(fee_usd) AS fees_usd,
 count(DISTINCT customer_id) AS customers FROM analytics.fact_trades;
SELECT activity_segment,count(*) FROM analytics.mart_customer_summary GROUP BY 1 ORDER BY 1;
