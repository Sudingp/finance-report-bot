"""
Synthetic payments data for a payment gateway, with known problems planted in it.

The data is synthetic on purpose: real merchant payment data is confidential, and
planting problems with a written answer key (data/planted_anomalies.json) lets the
detector be *measured* (did it find every planted problem? did it raise false alarms?)
instead of just demoed.

Tables
  merchants    merchant_id, name, category, city, fee_rate (contracted MDR)
  payments     payment_id, created_at, merchant_id, customer_id, method, amount, status, fee, tax
  refunds      refund_id, payment_id, created_at, amount
  settlements  settlement_id, merchant_id, settlement_date, amount   (T+2, net of fees, tax, refunds)

Planted anomalies (all switchable off with anomalies=False)
  method_failure_spike  UPI failure rate jumps on one day
  refund_spike          one merchant refunds ~30% of its volume for a week
  settlement_shortfall  one merchant is under-settled for three days (reconciliation break)
  duplicate_charges     one merchant charges customers twice within seconds
  gmv_drop              one category's volume falls sharply for a week
  fee_misconfig         one merchant is charged 3.5% instead of its contracted fee
Plus dirty rows for the data-quality checks: negative amounts, missing merchant,
duplicate payment ids, refunds larger than the payment, refunds for unknown payments.
"""

import json
import os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

START, END = date(2026, 4, 1), date(2026, 9, 30)
CATEGORIES = {  # category: (share of merchants, median ticket in INR)
    "ecommerce": (0.30, 900), "food_delivery": (0.15, 420), "travel": (0.10, 5200),
    "education": (0.10, 3500), "saas": (0.10, 2400), "gaming": (0.10, 300), "utilities": (0.15, 1100),
}
CITIES = ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune", "Kolkata", "Jaipur"]
METHODS = {"upi": (0.55, 0.06), "card": (0.25, 0.09), "netbanking": (0.12, 0.08), "wallet": (0.08, 0.05)}
GST = 0.18

PLANTED = [
    {"id": "A1", "type": "method_failure_spike", "key": "upi", "start": "2026-08-14", "end": "2026-08-14",
     "what": "UPI failure rate jumps to about 35% for one day"},
    {"id": "A2", "type": "refund_spike", "key": "M041", "start": "2026-09-07", "end": "2026-09-13",
     "what": "merchant M041 refunds about 30% of its volume for a week"},
    {"id": "A3", "type": "settlement_shortfall", "key": "M017", "start": "2026-07-20", "end": "2026-07-22",
     "what": "merchant M017 is settled 12% short for three days"},
    {"id": "A4", "type": "duplicate_charges", "key": "M088", "start": "2026-06-05", "end": "2026-06-05",
     "what": "merchant M088 charges customers twice within seconds (count in `planted_count`)"},
    {"id": "A5", "type": "gmv_drop", "key": "travel", "start": "2026-09-21", "end": "2026-09-27",
     "what": "travel volume falls about 45% for a week"},
    {"id": "A6", "type": "fee_misconfig", "key": "M063", "start": "2026-08-01", "end": "2026-09-30",
     "what": "merchant M063 is charged 3.5% instead of its contracted 2%"},
]


def _d(s):
    return date.fromisoformat(s)


def generate(out_dir: str, seed: int = 7, anomalies: bool = True, n_merchants: int = 120,
             payments_per_day: int = 1200, dirty_rows: bool = True) -> dict:
    rng = np.random.default_rng(seed)
    os.makedirs(out_dir, exist_ok=True)

    # ---- merchants -----------------------------------------------------
    cats = rng.choice(list(CATEGORIES), size=n_merchants, p=[v[0] for v in CATEGORIES.values()])
    cats[[16, 40, 62, 87]] = ["ecommerce", "ecommerce", "saas", "food_delivery"]  # planted merchants exist
    merchants = pd.DataFrame({
        "merchant_id": [f"M{i + 1:03d}" for i in range(n_merchants)],
        "name": [f"{c.replace('_', ' ').title()} Co {i + 1}" for i, c in enumerate(cats)],
        "category": cats,
        "city": rng.choice(CITIES, size=n_merchants),
        "fee_rate": rng.choice([0.018, 0.02, 0.02, 0.02, 0.022], size=n_merchants),
    })
    merchants.loc[merchants.merchant_id == "M063", "fee_rate"] = 0.02
    weight = rng.pareto(1.6, n_merchants) + 0.2          # a few big merchants, a long tail
    weight[[16, 40, 62, 87]] = np.maximum(weight[[16, 40, 62, 87]], np.median(weight) * 2.5)
    weight /= weight.sum()

    # ---- payments --------------------------------------------------------
    days = pd.date_range(START, END, freq="D")
    rows = []
    for d in days:
        n = int(payments_per_day * (1.15 if d.dayofweek >= 5 else 1.0) * rng.normal(1, 0.05))
        m_idx = rng.choice(n_merchants, size=n, p=weight)
        cat = merchants.category.values[m_idx]
        if anomalies and _d("2026-09-21") <= d.date() <= _d("2026-09-27"):
            keep = (cat != "travel") | (rng.random(n) > 0.45)
            m_idx, cat, n = m_idx[keep], cat[keep], int(keep.sum())
        median = np.array([CATEGORIES[c][1] for c in cat])
        amount = np.round(median * rng.lognormal(0, 0.6, n), 2)
        method = rng.choice(list(METHODS), size=n, p=[v[0] for v in METHODS.values()])
        fail_p = np.array([METHODS[m][1] for m in method])
        if anomalies and d.date() == _d("2026-08-14"):
            fail_p = np.where(method == "upi", 0.35, fail_p)
        status = np.where(rng.random(n) < fail_p, "failed", "captured")
        secs = np.sort(rng.integers(0, 86400, n))
        rows.append(pd.DataFrame({
            "created_at": d + pd.to_timedelta(secs, unit="s"), "m_idx": m_idx, "method": method,
            "amount": amount, "status": status, "customer_id": rng.integers(1, 400_000, n)}))
    p = pd.concat(rows, ignore_index=True)
    p["merchant_id"] = merchants.merchant_id.values[p.m_idx]
    rate = merchants.set_index("merchant_id").fee_rate.reindex(p.merchant_id).values
    if anomalies:
        wrong = (p.merchant_id == "M063") & (p.created_at >= pd.Timestamp("2026-08-01"))
        rate = np.where(wrong, 0.035, rate)
    captured = p.status == "captured"
    p["fee"] = np.where(captured, np.round(p.amount * rate, 2), 0.0)
    p["tax"] = np.round(p.fee * GST, 2)

    if anomalies:  # duplicate charges: same customer, amount and merchant within seconds
        src = p[(p.merchant_id == "M088") & (p.created_at.dt.date == _d("2026-06-05")) & captured].head(25).copy()
        src["created_at"] = src.created_at + pd.to_timedelta(rng.integers(3, 40, len(src)), unit="s")
        p = pd.concat([p, src], ignore_index=True)
        dup_count = len(src)

    p = p.sort_values("created_at", kind="stable").reset_index(drop=True)
    p.insert(0, "payment_id", [f"pay_{i:07d}" for i in range(len(p))])
    payments = p[["payment_id", "created_at", "merchant_id", "customer_id", "method", "amount", "status", "fee", "tax"]].copy()

    # ---- refunds -----------------------------------------------------------
    cap = payments[payments.status == "captured"]
    refund_p = np.full(len(cap), 0.02)
    if anomalies:
        spike = (cap.merchant_id == "M041") & cap.created_at.between("2026-09-07", "2026-09-13 23:59:59")
        refund_p = np.where(spike, 0.30, refund_p)
    picked = cap[rng.random(len(cap)) < refund_p]
    partial = rng.random(len(picked)) < 0.3
    refunds = pd.DataFrame({
        "payment_id": picked.payment_id.values,
        "created_at": picked.created_at.values + pd.to_timedelta(rng.integers(600, 86400, len(picked)), unit="s"),
        "amount": np.where(partial, np.round(picked.amount.values * rng.uniform(0.2, 0.8, len(picked)), 2),
                           picked.amount.values),
    })
    if anomalies:  # refunds for the spike week land inside that week, so they show up there
        late = (refunds.payment_id.isin(picked[(picked.merchant_id == "M041")].payment_id)
                & (refunds.created_at > pd.Timestamp("2026-09-13 23:59:59")))
        # .loc keeps the datetime type; np.where here gave mixed ints and Timestamps on
        # some numpy/pandas versions and broke the sort below (seen on Windows, Python 3.14)
        refunds.loc[late, "created_at"] = pd.Timestamp("2026-09-13 22:00:00")
    refunds["created_at"] = pd.to_datetime(refunds["created_at"])
    refunds = refunds.sort_values("created_at", kind="stable").reset_index(drop=True)
    refunds.insert(0, "refund_id", [f"rfnd_{i:06d}" for i in range(len(refunds))])

    # ---- settlements (T+2, per merchant per day) --------------------------------
    cap = payments[payments.status == "captured"].assign(day=lambda x: x.created_at.dt.normalize())
    gross = cap.groupby(["merchant_id", "day"]).agg(amount=("amount", "sum"), fee=("fee", "sum"), tax=("tax", "sum"))
    rf = refunds.merge(payments[["payment_id", "merchant_id"]], on="payment_id")
    rf = rf.assign(day=rf.created_at.dt.normalize()).groupby(["merchant_id", "day"]).amount.sum().rename("refunds")
    s = gross.join(rf, how="left").fillna({"refunds": 0}).reset_index()
    s["net"] = np.round(s.amount - s.fee - s.tax - s.refunds, 2)
    if anomalies:
        short = (s.merchant_id == "M017") & s.day.between("2026-07-20", "2026-07-22")
        s.loc[short, "net"] = np.round(s.loc[short, "net"] * 0.88, 2)
    settlements = pd.DataFrame({
        "settlement_id": [f"setl_{i:06d}" for i in range(len(s))], "merchant_id": s.merchant_id,
        "settlement_date": (s.day + pd.Timedelta(days=2)).dt.date, "for_date": s.day.dt.date, "amount": s.net})

    # ---- dirty rows for the data-quality checks -----------------------------
    if dirty_rows:
        bad = payments.sample(30, random_state=seed).copy()
        bad.iloc[:5, bad.columns.get_loc("amount")] *= -1                       # negative amounts
        bad.iloc[5:12, bad.columns.get_loc("merchant_id")] = None                # missing merchant
        bad["payment_id"] = [f"pay_bad_{i:03d}" for i in range(len(bad))]
        dup = payments.sample(10, random_state=seed + 1)                          # duplicate ids
        payments = pd.concat([payments, bad.iloc[:12], dup], ignore_index=True)
        over = refunds.sample(8, random_state=seed).copy()                        # refund > payment
        over["refund_id"] = [f"rfnd_over_{i}" for i in range(8)]
        pay_amt = payments.drop_duplicates("payment_id").set_index("payment_id").amount
        over["amount"] = np.round(pay_amt.reindex(over.payment_id).values + 100, 2)  # always over the total
        orphan = refunds.sample(6, random_state=seed + 2).copy()                 # unknown payment
        orphan["refund_id"] = [f"rfnd_orph_{i}" for i in range(6)]
        orphan["payment_id"] = [f"pay_missing_{i}" for i in range(6)]
        refunds = pd.concat([refunds, over, orphan], ignore_index=True)

    for name, df in {"merchants": merchants, "payments": payments, "refunds": refunds,
                     "settlements": settlements}.items():
        df.to_parquet(os.path.join(out_dir, f"{name}.parquet"), index=False)
    planted = [dict(a) for a in PLANTED] if anomalies else []
    for a in planted:
        if a["id"] == "A4":
            a["planted_count"] = dup_count
    with open(os.path.join(out_dir, "planted_anomalies.json"), "w") as f:
        json.dump({"seed": seed, "anomalies": planted, "generated_at": datetime.now().isoformat(timespec="seconds")},
                  f, indent=2)
    return {"payments": len(payments), "refunds": len(refunds), "settlements": len(settlements),
            "merchants": len(merchants), "planted": len(planted)}
