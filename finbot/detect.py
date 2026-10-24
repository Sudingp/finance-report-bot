"""
Anomaly detection: plain SQL rules with explicit thresholds, so every alert can be
explained and re-run. Each finding carries the numbers that triggered it; the LLM
summary may only quote those numbers.
"""

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Dict, List

import pandas as pd

THRESHOLDS = {
    "method_failure_spike": {"z": 4.0, "min_attempts": 200},
    "refund_spike": {"max_rate": 0.20, "min_payments": 40},
    "settlement_shortfall": {"tolerance": 1.0},
    "duplicate_charges": {"seconds": 60, "min_pairs": 5},
    "gmv_drop": {"max_drop": 0.30},
    "fee_misconfig": {"max_gap": 0.0025},
}
SEVERITY = {"settlement_shortfall": "high", "duplicate_charges": "high", "fee_misconfig": "high",
            "method_failure_spike": "high", "refund_spike": "medium", "gmv_drop": "medium"}


@dataclass
class Finding:
    id: str
    type: str
    key: str
    start: str
    end: str
    severity: str
    title: str
    facts: Dict[str, str] = field(default_factory=dict)   # name -> display value (what the LLM may quote)
    sql: str = ""

    def to_dict(self):
        return asdict(self)


def inr(x: float, decimals: int = 0) -> str:
    """Indian digit grouping: 12,34,567."""
    neg, x = x < 0, abs(x)
    whole, frac = f"{x:.{decimals}f}".split(".") if decimals else (f"{x:.0f}", "")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    s = ",".join(groups + [tail]) if groups else tail
    return ("-" if neg else "") + "₹" + s + (f".{frac}" if frac else "")


def pct(x: float, decimals: int = 1) -> str:
    return f"{x * 100:.{decimals}f}%"


def _d(x) -> str:
    return pd.Timestamp(x).date().isoformat()


def detect(wh, start: date, end: date) -> List[Finding]:
    """All findings whose day/week starts inside [start, end)."""
    p = {"start": pd.Timestamp(start), "end": pd.Timestamp(end)}
    out: List[Finding] = []

    df = wh.query("20_method_failure_spikes", {**p, **THRESHOLDS["method_failure_spike"]})
    for r in df.itertuples():
        out.append(Finding("", "method_failure_spike", r.method, _d(r.day), _d(r.day), "",
                           f"{r.method.upper()} failure rate spiked on {_d(r.day)}",
                           {"failure_rate": pct(r.failure_rate), "baseline": pct(r.baseline),
                            "attempts": f"{r.attempts:,}", "z_score": f"{r.z:.1f}"}, "20_method_failure_spikes"))

    df = wh.query("21_refund_spikes", {**p, **THRESHOLDS["refund_spike"]})
    for r in df.itertuples():
        wk = pd.Timestamp(r.week)
        out.append(Finding("", "refund_spike", r.merchant_id, _d(wk), _d(wk + timedelta(days=6)), "",
                           f"Merchant {r.merchant_id} refunded {pct(r.refund_rate)} of its volume in the week of {_d(wk)}",
                           {"refund_rate": pct(r.refund_rate), "refunds": inr(r.refunds), "gmv": inr(r.gmv),
                            "payments": f"{r.payments:,}"}, "21_refund_spikes"))

    df = wh.query("22_reconciliation", {**p, **THRESHOLDS["settlement_shortfall"]})
    for mid, g in df.groupby("merchant_id"):
        out.append(Finding("", "settlement_shortfall", mid, _d(g.for_date.min()), _d(g.for_date.max()), "",
                           f"Merchant {mid} was settled {inr(-g.difference.sum())} less than expected",
                           {"shortfall": inr(-g.difference.sum()), "days": str(len(g)),
                            "expected": inr(g.expected.sum()), "settled": inr(g.settled.sum())}, "22_reconciliation"))

    df = wh.query("23_duplicate_charges", {**p, **THRESHOLDS["duplicate_charges"]})
    for r in df.itertuples():
        out.append(Finding("", "duplicate_charges", r.merchant_id, _d(r.day), _d(r.day), "",
                           f"Merchant {r.merchant_id} charged {r.duplicate_pairs} customers twice on {_d(r.day)}",
                           {"duplicate_charges": str(r.duplicate_pairs), "amount": inr(r.amount_charged_twice)},
                           "23_duplicate_charges"))

    df = wh.query("24_category_gmv_drops", {**p, **THRESHOLDS["gmv_drop"]})
    for r in df.itertuples():
        wk = pd.Timestamp(r.week)
        out.append(Finding("", "gmv_drop", r.category, _d(wk), _d(wk + timedelta(days=6)), "",
                           f"{r.category.replace('_', ' ').title()} volume fell {pct(-r.change)} in the week of {_d(wk)}",
                           {"change": pct(r.change), "gmv": inr(r.gmv), "trailing_avg": inr(r.trailing_avg)},
                           "24_category_gmv_drops"))

    df = wh.query("25_fee_rate_check", {**p, **THRESHOLDS["fee_misconfig"]})
    for mid, g in df.groupby("merchant_id"):
        rate = (g.effective_rate * g.payments).sum() / g.payments.sum()
        out.append(Finding("", "fee_misconfig", mid, _d(g.month.min()),
                           _d(pd.Timestamp(g.month.max()) + pd.offsets.MonthEnd(0)), "",
                           f"Merchant {mid} was charged {pct(rate, 2)} instead of its contracted "
                           f"{pct(g.contracted_rate.iloc[0], 2)}",
                           {"effective_rate": pct(rate, 2), "contracted_rate": pct(g.contracted_rate.iloc[0], 2),
                            "overcharged": inr(g.overcharged.sum())}, "25_fee_rate_check"))

    order = {"high": 0, "medium": 1}
    for f in out:
        f.severity = SEVERITY[f.type]
    out.sort(key=lambda f: (order[f.severity], f.start))
    for i, f in enumerate(out, 1):
        f.id = f"F{i}"
    return out


def score_against_planted(findings: List[Finding], planted: List[dict]) -> Dict:
    """Precision and recall of the detector against the answer key."""
    def overlaps(f, a):
        return f.type == a["type"] and f.key == a["key"] and f.start <= a["end"] and a["start"] <= f.end
    hit = {a["id"] for a in planted if any(overlaps(f, a) for f in findings)}
    true_pos = [f for f in findings if any(overlaps(f, a) for a in planted)]
    return {"planted": len(planted), "found": len(hit), "missed": sorted({a["id"] for a in planted} - hit),
            "findings": len(findings), "false_alarms": [f.title for f in findings if f not in true_pos],
            "recall": len(hit) / len(planted) if planted else None,
            "precision": len(true_pos) / len(findings) if findings else None}
