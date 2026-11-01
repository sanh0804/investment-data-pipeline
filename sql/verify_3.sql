-- Independent dashboard reconciliation (no Metabase template syntax).
SELECT count(*) AS trades,sum(notional_usd) AS volume_usd,sum(fee_usd) AS fees_usd,
count(DISTINCT customer_id) AS distinct_active_customers
FROM analytics.fact_trades
WHERE dataset_id='portfolio-v1' AND trade_date BETWEEN DATE '2026-05-01' AND DATE '2026-10-27';

-- Date + asset check: 3100 / 2633325.78 / 2633.37 / 300.
SELECT count(*) AS trades,sum(notional_usd) AS volume_usd,sum(fee_usd) AS fees_usd,
count(DISTINCT customer_id) AS distinct_active_customers
FROM analytics.fact_trades
WHERE dataset_id='portfolio-v1' AND trade_date BETWEEN DATE '2026-06-01' AND DATE '2026-06-30'
AND base_asset='ETH';

-- Calendar padding must preserve 180 days and 64 zero-trade days.
WITH daily AS (
 SELECT d.date_day,count(f.trade_id) AS trades,coalesce(sum(f.notional_usd),0) AS volume
 FROM analytics.dim_date d LEFT JOIN analytics.fact_trades f
 ON f.trade_date=d.date_day AND f.dataset_id='portfolio-v1'
 WHERE d.date_day BETWEEN DATE '2026-05-01' AND DATE '2026-10-27' GROUP BY 1
)
SELECT count(*) AS calendar_days,count(*) FILTER(WHERE trades=0) AS zero_trade_days,sum(volume)
FROM daily;

SELECT activity_segment,count(*) FROM analytics.mart_customer_summary
WHERE dataset_id='portfolio-v1' AND as_of_date=DATE '2026-10-27' GROUP BY 1 ORDER BY 1;

-- Actual daily-mart grain: no asset/side dimensions here.
SELECT count(*) AS rows,count(DISTINCT(dataset_id,trade_date)) AS unique_keys
FROM analytics.mart_daily_trading;
