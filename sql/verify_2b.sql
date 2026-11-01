-- Demo only: analytics built with dataset_id=portfolio-v1, as_of_date=2026-10-27.
-- Reconciliation: all rows must report 50000 / 615063969.51 / 615063.60 for this verified snapshot.
SELECT 'staging' AS layer,count(*) AS trade_count,sum(notional_usd) AS trade_volume_usd,sum(fee_usd) AS fees_usd
FROM analytics.stg_trades
UNION ALL
SELECT 'fact',count(*),sum(notional_usd),sum(fee_usd) FROM analytics.fact_trades
UNION ALL
SELECT 'daily',sum(trade_count),sum(trade_volume_usd),sum(fees_usd) FROM analytics.mart_daily_trading
UNION ALL
SELECT 'customer',sum(total_trades),sum(total_volume_usd),sum(total_fee_usd) FROM analytics.mart_customer_summary;

-- Must return no rows: no fan-out or lost execution after joins.
SELECT dataset_id,trade_id,count(*) FROM analytics.fact_trades
GROUP BY dataset_id,trade_id HAVING count(*)<>1;

-- Correct whole-range distinct, NOT SUM(daily.active_customers).
SELECT count(DISTINCT customer_id) AS active_customers_in_range FROM analytics.fact_trades;

SELECT activity_segment,count(*) AS customers FROM analytics.mart_customer_summary
GROUP BY activity_segment ORDER BY activity_segment;

-- Must return zero: zero-trade semantics.
SELECT count(*) AS invalid_never_traded FROM analytics.mart_customer_summary
WHERE total_trades=0 AND (first_trade_date IS NOT NULL OR last_trade_date IS NOT NULL OR
days_since_last_trade IS NOT NULL OR total_volume_usd<>0 OR total_fee_usd<>0 OR activity_segment<>'never_traded');

-- Must return zero: eligibility/cutoff and summary cardinality.
SELECT (SELECT count(*) FROM analytics.mart_customer_summary) -
(SELECT count(*) FROM synthetic.customers WHERE dataset_id='portfolio-v1'
 AND created_at < timestamptz '2026-10-28 00:00:00+00') AS customer_count_difference;

SELECT count(*) AS trades_beyond_cutoff FROM analytics.fact_trades
WHERE traded_at >= timestamptz '2026-10-28 00:00:00+00';
