-- Headline numbers for a period [$start, $end).
WITH p AS (
  SELECT * FROM payments WHERE created_at >= $start AND created_at < $end
), r AS (
  SELECT r.amount FROM refunds r WHERE r.created_at >= $start AND r.created_at < $end
)
SELECT
  coalesce(sum(amount) FILTER (WHERE status = 'captured'), 0)                     AS gmv,
  count(*)                                                                         AS attempts,
  count(*) FILTER (WHERE status = 'captured')                                      AS captured,
  count(*) FILTER (WHERE status = 'captured') / nullif(count(*), 0)                AS success_rate,
  avg(amount) FILTER (WHERE status = 'captured')                                   AS avg_ticket,
  coalesce(sum(fee), 0)                                                            AS fee_revenue,
  (SELECT coalesce(sum(amount), 0) FROM r)                                         AS refunds,
  (SELECT coalesce(sum(amount), 0) FROM r)
     / nullif(sum(amount) FILTER (WHERE status = 'captured'), 0)                   AS refund_rate,
  count(DISTINCT merchant_id) FILTER (WHERE status = 'captured')                   AS active_merchants
FROM p;
