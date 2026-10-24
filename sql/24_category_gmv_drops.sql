-- Weekly volume per category vs its own previous 4 weeks (full weeks only).
WITH w AS (
  SELECT m.category, date_trunc('week', p.created_at)::DATE AS week, sum(p.amount) AS gmv
  FROM payments p JOIN merchants m USING (merchant_id)
  WHERE p.status = 'captured'
  GROUP BY 1, 2
), t AS (
  SELECT *, avg(gmv) OVER (PARTITION BY category ORDER BY week ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING) AS trailing_avg,
         count(*) OVER (PARTITION BY category ORDER BY week ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING) AS n_prev
  FROM w
)
SELECT category, week, gmv, trailing_avg, gmv / trailing_avg - 1 AS change
FROM t
WHERE week >= $start AND week + 7 <= $end AND week + 7 <= (SELECT max(created_at)::DATE + 1 FROM payments)
  AND n_prev = 4 AND gmv / trailing_avg - 1 < -$max_drop
ORDER BY change;
