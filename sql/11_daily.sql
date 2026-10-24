-- Daily volume and success rate, with a 7-day moving average for the chart.
WITH d AS (
  SELECT created_at::DATE                                           AS day,
         sum(amount) FILTER (WHERE status = 'captured')             AS gmv,
         count(*) FILTER (WHERE status = 'captured') / count(*)     AS success_rate
  FROM payments
  WHERE created_at >= $start AND created_at < $end
  GROUP BY 1
)
SELECT *, avg(gmv) OVER (ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS gmv_7d_avg
FROM d
ORDER BY day;
