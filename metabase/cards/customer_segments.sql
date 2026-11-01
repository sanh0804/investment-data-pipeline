-- Fixed customer snapshot: intentionally no dashboard date/asset template tags.
SELECT activity_segment, count(*) AS customers
FROM analytics.mart_customer_summary
WHERE dataset_id='portfolio-v1' AND as_of_date=DATE '2026-10-27'
GROUP BY activity_segment ORDER BY activity_segment
