-- Daily failure rate per method vs its own trailing 28 days (z-score).
WITH d AS (
  SELECT method, created_at::DATE AS day, count(*) AS attempts,
         count(*) FILTER (WHERE status = 'failed') / count(*) AS failure_rate
  FROM payments GROUP BY 1, 2
), w AS (
  SELECT *,
    avg(failure_rate)         OVER t AS baseline,
    stddev_samp(failure_rate) OVER t AS sd
  FROM d
  WINDOW t AS (PARTITION BY method ORDER BY day ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING)
)
SELECT method, day, attempts, failure_rate, baseline, (failure_rate - baseline) / nullif(sd, 0) AS z
FROM w
WHERE day >= $start AND day < $end AND attempts >= $min_attempts
  AND (failure_rate - baseline) / nullif(sd, 0) > $z AND failure_rate > 2 * baseline
ORDER BY z DESC;
