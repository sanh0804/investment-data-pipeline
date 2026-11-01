-- Fixed customer snapshot: intentionally no dashboard date/asset template tags.
SELECT customer_id, activity_segment, total_trades, total_volume_usd, total_fee_usd
FROM analytics.mart_customer_summary
WHERE dataset_id='portfolio-v1' AND as_of_date=DATE '2026-10-27'
ORDER BY total_fee_usd DESC, customer_id LIMIT 15
