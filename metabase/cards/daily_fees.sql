WITH filtered_trades AS (SELECT f.*
FROM analytics.fact_trades f
WHERE f.dataset_id = 'portfolio-v1'
  AND f.trade_date BETWEEN DATE '2026-05-01' AND DATE '2026-10-27'
  [[AND f.trade_date >= {{start_date}}]]
  [[AND f.trade_date <= {{end_date}}]]
  [[AND f.base_asset = {{asset}}]]
), calendar AS (
    SELECT date_day FROM analytics.dim_date
    WHERE date_day BETWEEN DATE '2026-05-01' AND DATE '2026-10-27'
      [[AND date_day >= {{start_date}}]]
      [[AND date_day <= {{end_date}}]]
)
SELECT c.date_day AS trade_date, coalesce(sum(f.fee_usd),0) AS fees_usd
FROM calendar c LEFT JOIN filtered_trades f ON f.trade_date=c.date_day
GROUP BY c.date_day ORDER BY c.date_day
