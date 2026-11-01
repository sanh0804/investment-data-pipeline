SELECT count(DISTINCT f.customer_id) AS active_customers
FROM analytics.fact_trades f
WHERE f.dataset_id = 'portfolio-v1'
  AND f.trade_date BETWEEN DATE '2026-05-01' AND DATE '2026-10-27'
  [[AND f.trade_date >= {{start_date}}]]
  [[AND f.trade_date <= {{end_date}}]]
  [[AND f.base_asset = {{asset}}]]
