-- Clean views used by every report query. Rows failing a data-quality rule are
-- excluded here and counted in quality checks, never silently dropped.
CREATE OR REPLACE VIEW payments AS
SELECT p.*
FROM payments_raw p
JOIN merchants m USING (merchant_id)               -- drops missing / unknown merchants
WHERE p.amount > 0
QUALIFY row_number() OVER (PARTITION BY p.payment_id ORDER BY p.created_at) = 1;   -- one row per id

CREATE OR REPLACE VIEW refunds AS
WITH r AS (
  SELECT r.*, p.amount AS payment_amount, p.status
  FROM refunds_raw r
  JOIN payments p USING (payment_id)               -- drops refunds for unknown payments
  QUALIFY row_number() OVER (PARTITION BY r.refund_id ORDER BY r.created_at) = 1
)
SELECT refund_id, payment_id, created_at, amount
FROM r
WHERE status = 'captured' AND amount > 0
-- a payment can be refunded in parts, but never more than it was worth in total
QUALIFY sum(amount) OVER (PARTITION BY payment_id ORDER BY created_at, refund_id
                          ROWS UNBOUNDED PRECEDING) <= payment_amount;
