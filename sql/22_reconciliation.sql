-- Settlement reconciliation: what each merchant should have been paid for a day
-- (captured - fees - tax - refunds) vs what was actually settled.
WITH expected AS (
  SELECT merchant_id, created_at::DATE AS for_date,
         sum(amount) - sum(fee) - sum(tax) AS gross_net
  FROM payments WHERE status = 'captured' GROUP BY 1, 2
), ref AS (
  SELECT p.merchant_id, r.created_at::DATE AS for_date, sum(r.amount) AS refunds
  FROM refunds r JOIN payments p USING (payment_id) GROUP BY 1, 2
)
SELECT e.merchant_id, e.for_date,
       round(e.gross_net - coalesce(f.refunds, 0), 2) AS expected,
       s.amount                                       AS settled,
       round(s.amount - (e.gross_net - coalesce(f.refunds, 0)), 2) AS difference
FROM expected e
LEFT JOIN ref f USING (merchant_id, for_date)
JOIN settlements s USING (merchant_id, for_date)
WHERE e.for_date >= $start AND e.for_date < $end
  AND abs(s.amount - (e.gross_net - coalesce(f.refunds, 0))) > $tolerance
ORDER BY abs(difference) DESC;
