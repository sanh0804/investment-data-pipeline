SELECT coalesce(sum(f.fee_usd),0) AS fees_usd
FROM analytics.fact_trades f
WHERE f.dataset_id = 'portfolio-v1'
  AND f.trade_date BETWEEN DATE '2026-05-01' AND DATE '2026-10-27'
  [[AND f.trade_date >= {{start_date}}]]
  [[AND f.trade_date <= {{end_date}}]]
  [[AND f.base_asset = {{asset}}]]
