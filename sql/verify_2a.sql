-- Fixed stage 2A verification range: [2026-05-01,2026-10-28).
-- Expected 180 rows per product; adjust literals deliberately for another dataset.
SELECT product_id, count(*) AS days, min(price_date), max(price_date)
FROM market.daily_prices WHERE price_date >= '2026-05-01' AND price_date < '2026-10-28'
GROUP BY product_id ORDER BY product_id;

WITH expected AS (
    SELECT p AS product_id, d::date AS price_date
    FROM unnest(ARRAY['BTC-USD','ETH-USD','SOL-USD']) AS p
    CROSS JOIN generate_series(timestamp '2026-05-01', timestamp '2026-10-27', interval '1 day') AS d
)
SELECT e.* AS missing_price FROM expected e LEFT JOIN market.daily_prices p
ON p.source='coinbase_exchange' AND p.product_id=e.product_id AND p.price_date=e.price_date
WHERE p.price_date IS NULL;

SELECT activity_profile, count(*) AS customers FROM synthetic.customers
WHERE dataset_id='portfolio-v1' GROUP BY activity_profile ORDER BY activity_profile;

-- All violation counters must be zero.
SELECT count(*) AS orphan_customer FROM synthetic.trades t LEFT JOIN synthetic.customers c
ON c.dataset_id=t.dataset_id AND c.customer_id=t.customer_id
WHERE t.dataset_id='portfolio-v1' AND c.customer_id IS NULL;

SELECT count(*) AS trades_before_customer FROM synthetic.trades t JOIN synthetic.customers c
ON c.dataset_id=t.dataset_id AND c.customer_id=t.customer_id
WHERE t.dataset_id='portfolio-v1' AND t.traded_at <= c.created_at;

SELECT count(*) AS invalid_amounts FROM synthetic.trades WHERE dataset_id='portfolio-v1' AND (
quantity <= 0 OR execution_price <= 0 OR fee_usd < 0 OR
execution_price <> round(reference_close * (1 + slippage_bps::numeric / 10000),2) OR
notional_usd <> round(quantity * execution_price,2) OR fee_usd <> round(notional_usd * 0.001,2));

SELECT product_id, side, count(*) AS trades, sum(notional_usd) AS notional_usd, sum(fee_usd) AS fee_usd
FROM synthetic.trades WHERE dataset_id='portfolio-v1' GROUP BY product_id,side ORDER BY product_id,side;

SELECT count(*) AS trades, count(DISTINCT customer_id) AS trading_customers,
sum(notional_usd) AS notional_usd, sum(fee_usd) AS fee_usd
FROM synthetic.trades WHERE dataset_id='portfolio-v1';
