-- Merchants whose refunds in a week are an unusual share of their captured volume.
WITH cap AS (
  SELECT merchant_id, date_trunc('week', created_at)::DATE AS week, count(*) AS payments, sum(amount) AS gmv
  FROM payments WHERE status = 'captured' GROUP BY 1, 2
), ref AS (
  SELECT p.merchant_id, date_trunc('week', r.created_at)::DATE AS week, sum(r.amount) AS refunds
  FROM refunds r JOIN payments p USING (payment_id) GROUP BY 1, 2
)
SELECT c.merchant_id, c.week, c.payments, c.gmv, coalesce(f.refunds, 0) AS refunds,
       coalesce(f.refunds, 0) / c.gmv AS refund_rate
FROM cap c LEFT JOIN ref f USING (merchant_id, week)
WHERE c.week >= $start AND c.week < $end AND c.payments >= $min_payments
  AND coalesce(f.refunds, 0) / c.gmv > $max_rate
ORDER BY refund_rate DESC;
