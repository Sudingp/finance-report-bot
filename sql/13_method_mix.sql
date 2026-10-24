-- Payment method mix and success rate in the period.
SELECT method,
       count(*)                                                AS attempts,
       count(*) / sum(count(*)) OVER ()                        AS share_of_attempts,
       count(*) FILTER (WHERE status = 'captured') / count(*)  AS success_rate,
       sum(amount) FILTER (WHERE status = 'captured')          AS gmv
FROM payments
WHERE created_at >= $start AND created_at < $end
GROUP BY method ORDER BY attempts DESC;
