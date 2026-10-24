-- The same customer charged the same amount by the same merchant within N seconds.
WITH x AS (
  SELECT *, lag(created_at) OVER (PARTITION BY merchant_id, customer_id, amount ORDER BY created_at) AS prev_at
  FROM payments WHERE status = 'captured'
)
SELECT merchant_id, created_at::DATE AS day, count(*) AS duplicate_pairs, sum(amount) AS amount_charged_twice
FROM x
WHERE prev_at IS NOT NULL AND created_at - prev_at <= INTERVAL ($seconds) SECOND
  AND created_at >= $start AND created_at < $end
GROUP BY 1, 2
HAVING count(*) >= $min_pairs
ORDER BY duplicate_pairs DESC;
