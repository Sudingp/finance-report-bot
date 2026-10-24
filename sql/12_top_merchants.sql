-- Top merchants by volume in the period, with share of total and change vs the previous period.
WITH cur AS (
  SELECT merchant_id, sum(amount) AS gmv FROM payments
  WHERE status = 'captured' AND created_at >= $start AND created_at < $end GROUP BY 1
), prev AS (
  SELECT merchant_id, sum(amount) AS gmv FROM payments
  WHERE status = 'captured' AND created_at >= $prev_start AND created_at < $start GROUP BY 1
)
SELECT
  rank() OVER (ORDER BY c.gmv DESC)              AS rank,
  c.merchant_id, m.name, m.category,
  c.gmv,
  c.gmv / sum(c.gmv) OVER ()                     AS share,
  c.gmv / nullif(p.gmv, 0) - 1                   AS change_vs_prev
FROM cur c JOIN merchants m USING (merchant_id) LEFT JOIN prev p USING (merchant_id)
QUALIFY rank <= $limit
ORDER BY rank;
