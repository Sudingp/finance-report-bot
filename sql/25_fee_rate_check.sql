-- Effective fee rate charged per merchant per month vs the contracted rate.
SELECT p.merchant_id, date_trunc('month', p.created_at)::DATE AS month,
       count(*) AS payments, sum(p.fee) / sum(p.amount) AS effective_rate, m.fee_rate AS contracted_rate,
       sum(p.fee) - sum(p.amount) * m.fee_rate AS overcharged
FROM payments p JOIN merchants m USING (merchant_id)
WHERE p.status = 'captured' AND p.created_at >= $start AND p.created_at < $end
GROUP BY 1, 2, m.fee_rate
HAVING abs(sum(p.fee) / sum(p.amount) - m.fee_rate) > $max_gap
ORDER BY abs(overcharged) DESC;
