# Finance Report Bot

An automated business review for a payment gateway's finance team. **SQL computes every
number, rules flag the problems, and an LLM writes the summary, but it may only quote numbers that
the SQL produced.** Each run writes an HTML report, a Markdown copy and BI-ready CSVs.

```
python -m finbot generate      # 6 months of payments data with 6 problems planted in it
python -m finbot report        # business review for the last full month
```

```
period 2026-09-01 to 2026-09-30: 3 finding(s)
  [high] Merchant M063 was charged 3.50% instead of its contracted 2.00%
  [medium] Merchant M041 refunded 29.9% of its volume in the week of 2026-09-07
  [medium] Travel volume fell 42.5% in the week of 2026-09-21
summary: template (LLM switched off)
detector vs answer key: 3/3 found, 0 false alarm(s)
```

## What's in the report
- **Headline numbers** with change vs the previous period: captured volume (GMV), success rate,
  fee revenue, refund rate, attempts, average ticket. Amounts in Indian format (₹5,55,74,967).
- **Findings**, each with the numbers behind it and the SQL file that produced it:

  | Check | Rule | SQL |
  | :--- | :--- | :--- |
  | Payment-method failure spike | daily failure rate vs its trailing 28 days, z > 4 and > 2x baseline | `20_method_failure_spikes.sql` |
  | Refund spike | a merchant refunds > 20% of a week's volume (≥ 40 payments) | `21_refund_spikes.sql` |
  | Settlement shortfall | settled amount ≠ captured − fees − GST − refunds (reconciliation) | `22_reconciliation.sql` |
  | Duplicate charges | same customer, merchant and amount within 60 s, ≥ 5 times a day | `23_duplicate_charges.sql` |
  | Category volume drop | a category's week is > 30% below its previous 4 weeks | `24_category_gmv_drops.sql` |
  | Fee misconfiguration | effective fee rate ≠ contracted rate by > 0.25 pp | `25_fee_rate_check.sql` |

- **Summary** written by an LLM (or a template without one), trends (charts), top merchants with
  rank and share, payment-method mix, and a **data-quality** table.
- `bi_exports/`: `fact_payments_daily.csv`, `dim_merchants.csv`, `findings.csv`, a small star
  schema that Power BI or Tableau can load directly.

## The number guardrail
The LLM never sees raw rows and never does arithmetic. It gets ~40 facts that SQL already computed,
each with an id (`F1.overcharged: ₹26,998`), and must cite the ids each bullet uses. Then every
bullet is checked: **each number and date in the text must appear in the facts it cites.**

| LLM wrote | Result |
| :--- | :--- |
| "M063 was overcharged ₹26,998" citing `F1.overcharged` | kept |
| "M063 was overcharged ₹30,000" | removed: number not in the cited facts |
| "Volume was ₹5.6 crore" (rounded) | removed: a rounded number is a new number |
| "Success rate was 93.1%" citing the GMV fact | removed: wrong citation |

Removed bullets are listed in the report. If the LLM is down, returns broken JSON or every bullet
fails, the report falls back to a template summary, so the weekly report always goes out.

## Data quality
Raw tables are kept as they are; the reports read clean views (`sql/00_clean_views.sql`), and every
excluded row is counted in the report:

| Rule | What it catches |
| :--- | :--- |
| amount is zero or negative | bad captures |
| merchant missing or unknown | orphaned payments |
| duplicate payment_id | double-loaded rows |
| refund for an unknown payment | orphaned refunds |
| total refunded exceeds the payment | over-refunds (checked with a running sum, so split refunds count) |
| fee on a failed payment | wrongly charged fees |

## Measuring the detector
The data is **synthetic**, on purpose: real merchant payment data is confidential, and planting
problems with a written answer key (`data/planted_anomalies.json`) means the detector can be
*measured*, not just demoed.

```
python -m finbot benchmark --seeds 30
60 datasets: found 179/180 planted problems (recall 99.4%), 1 false alarm(s)
```
30 random seeds, each generated with and without the planted problems. The one miss was a burst of
only 4 duplicate charges, below the 5-per-day threshold; the one false alarm was a small merchant
whose genuine refunds reached 21.2% in one week. Both are threshold trade-offs, and are reported
rather than tuned away.

## Automated weekly report
`ci/weekly-report.yml` (copy it to `.github/workflows/`) builds the report every Monday at 09:00 IST
on GitHub Actions and attaches it to the run. Add the `LLM_*` repository secrets for an LLM summary.

## Run it
```bash
pip install -r requirements.txt
python -m finbot generate
python -m finbot report --no-llm                       # template summary, no API key
cp .env.example .env                                   # add a free Groq key for the LLM summary
python -m finbot report                                # last full month
python -m finbot report --start 2026-09-21 --end 2026-09-28   # any week (end exclusive)
python -m finbot report --all                          # the whole six months
python -m pytest -q                                    # 23 offline tests
```

## Stack
Python, DuckDB (SQL with window functions: LAG, rolling averages, RANK, running sums, QUALIFY),
pandas, matplotlib, OpenAI-compatible LLM API (Groq), pytest, GitHub Actions.
