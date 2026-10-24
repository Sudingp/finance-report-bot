-- One row per data-quality rule: how many raw rows break it.
SELECT 'payments: amount is zero or negative' AS rule, count(*) AS rows FROM payments_raw WHERE amount <= 0
UNION ALL
SELECT 'payments: merchant missing or unknown', count(*) FROM payments_raw p
  WHERE p.merchant_id IS NULL OR p.merchant_id NOT IN (SELECT merchant_id FROM merchants)
UNION ALL
SELECT 'payments: duplicate payment_id', count(*) - count(DISTINCT payment_id) FROM payments_raw
UNION ALL
SELECT 'refunds: payment not found', count(*) FROM refunds_raw r
  WHERE r.payment_id NOT IN (SELECT payment_id FROM payments_raw)
UNION ALL
SELECT 'refunds: total refunded exceeds the payment', count(*) FROM (
  SELECT r.amount, p.amount AS payment_amount,
         sum(r.amount) OVER (PARTITION BY r.payment_id ORDER BY r.created_at, r.refund_id) AS running
  FROM refunds_raw r
  JOIN (SELECT payment_id, any_value(amount) AS amount FROM payments_raw GROUP BY payment_id) p USING (payment_id)
) WHERE running > payment_amount
UNION ALL
SELECT 'payments: fee on a failed payment', count(*) FROM payments_raw WHERE status = 'failed' AND fee > 0;
