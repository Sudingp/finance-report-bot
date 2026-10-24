"""Offline tests: generated data, SQL, detector, number guardrail, report. No API key needed."""

import json
import os
from datetime import date
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from finbot import summary as S
from finbot.__main__ import main
from finbot.db import SQL_DIR, Warehouse
from finbot.detect import detect, inr, score_against_planted
from finbot.report import build_report

ALL = (date(2026, 4, 1), date(2026, 10, 1))


def planted(d):
    return json.load(open(os.path.join(d, "planted_anomalies.json")))["anomalies"]


# ---- data and SQL ---------------------------------------------------------
def test_generation_is_reproducible(tmp_path, data_dir):
    from finbot.generate import generate
    generate(str(tmp_path), seed=7)
    a = pd.read_parquet(os.path.join(data_dir, "payments.parquet"))
    b = pd.read_parquet(tmp_path / "payments.parquet")
    pd.testing.assert_frame_equal(a, b)


def test_every_sql_file_runs(wh):
    params = {"start": pd.Timestamp("2026-09-01"), "end": pd.Timestamp("2026-10-01"),
              "prev_start": pd.Timestamp("2026-08-02"), "limit": 10, "z": 4.0, "min_attempts": 200,
              "max_rate": 0.2, "min_payments": 40, "tolerance": 1.0, "seconds": 60, "min_pairs": 5,
              "max_drop": 0.3, "max_gap": 0.0025}
    names = sorted(f[:-4] for f in os.listdir(SQL_DIR) if f.endswith(".sql") and not f.startswith("00"))
    assert len(names) == 11
    for n in names:
        assert isinstance(wh.query(n, params), pd.DataFrame), n


def test_quality_checks_count_exactly_the_injected_bad_rows(wh):
    q = dict(wh.query("01_quality_checks").values)
    assert q == {"payments: amount is zero or negative": 5, "payments: merchant missing or unknown": 7,
                 "payments: duplicate payment_id": 10, "refunds: payment not found": 6,
                 "refunds: total refunded exceeds the payment": 8, "payments: fee on a failed payment": 0}


def test_clean_views_exclude_bad_rows(wh):
    raw, clean = (wh.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("payments_raw", "payments"))
    assert raw - clean == 5 + 7 + 10
    assert wh.con.execute("SELECT count(*) FROM payments WHERE amount <= 0 OR merchant_id IS NULL").fetchone()[0] == 0
    over = wh.con.execute("""SELECT count(*) FROM (SELECT payment_id, sum(r.amount) s FROM refunds r GROUP BY 1)
                              JOIN payments p USING (payment_id) WHERE s > p.amount""").fetchone()[0]
    assert over == 0


def test_kpis_match_pandas(wh, data_dir):
    k = wh.query("10_kpis", {"start": pd.Timestamp("2026-09-01"), "end": pd.Timestamp("2026-10-01")}).iloc[0]
    p = pd.read_parquet(os.path.join(data_dir, "payments.parquet"))
    p = p[(p.amount > 0) & p.merchant_id.notna()].drop_duplicates("payment_id")
    sep = p[(p.created_at >= "2026-09-01") & (p.created_at < "2026-10-01")]
    assert k.gmv == pytest.approx(sep[sep.status == "captured"].amount.sum())
    assert k.success_rate == pytest.approx((sep.status == "captured").mean())


def test_indian_number_format():
    assert inr(5574967) == "₹55,74,967"
    assert inr(999) == "₹999"
    assert inr(123456789.5, 2) == "₹12,34,56,789.50"
    assert inr(-1500) == "-₹1,500"


# ---- detector ---------------------------------------------------------------
def test_detector_finds_every_planted_problem_and_nothing_else(wh, data_dir):
    s = score_against_planted(detect(wh, *ALL), planted(data_dir))
    assert s["found"] == 6 and s["false_alarms"] == []


def test_detector_is_silent_on_clean_data(clean_dir):
    assert detect(Warehouse(clean_dir), *ALL) == []


def test_findings_carry_their_numbers(wh):
    f = {x.type: x for x in detect(wh, *ALL)}
    assert f["fee_misconfig"].facts["effective_rate"] == "3.50%"
    assert f["fee_misconfig"].facts["contracted_rate"] == "2.00%"
    assert f["settlement_shortfall"].key == "M017" and f["settlement_shortfall"].facts["days"] == "3"
    assert float(f["method_failure_spike"].facts["failure_rate"].rstrip("%")) > 30
    assert [x.severity for x in detect(wh, *ALL)][:4] == ["high"] * 4


def test_period_filter(wh):
    sept = detect(wh, date(2026, 9, 1), date(2026, 10, 1))
    assert {x.type for x in sept} == {"fee_misconfig", "refund_spike", "gmv_drop"}


# ---- number guardrail ----------------------------------------------------------
FACTS = {"kpi.gmv": "₹5,55,74,967", "kpi.success_rate": "93.1%", "F1.title": "Merchant M063 was charged 3.50% instead of 2.00%",
         "F1.overcharged": "₹26,998", "F1.dates": "2026-09-01 to 2026-09-30"}


def test_grounded_bullet_is_kept():
    assert S.check_item({"text": "M063 was overcharged ₹26,998 (3.50% vs 2.00%) from 2026-09-01.",
                         "fact_ids": ["F1.title", "F1.overcharged", "F1.dates"]}, FACTS) is None


@pytest.mark.parametrize("item,reason", [
    ({"text": "M063 was overcharged ₹30,000.", "fact_ids": ["F1.overcharged"]}, "numbers not in the cited facts"),
    ({"text": "Volume was ₹5.6 crore.", "fact_ids": ["kpi.gmv"]}, "numbers not in the cited facts"),       # rounded = invented
    ({"text": "Success rate was 93.1%.", "fact_ids": ["kpi.gmv"]}, "numbers not in the cited facts"),     # wrong citation
    ({"text": "Since 2026-08-15 fees rose.", "fact_ids": ["F1.title"]}, "numbers not in the cited facts"),
    ({"text": "Volume grew.", "fact_ids": ["kpi.nope"]}, "unknown"),
    ({"text": "Volume grew.", "fact_ids": []}, "unknown or no facts"),
])
def test_ungrounded_bullets_are_removed(item, reason):
    assert reason in S.check_item(item, FACTS)


class FakeLLM:
    def __init__(self, reply, fail_json_mode=False):
        self.reply, self.fail_json_mode, self.requests = reply, fail_json_mode, []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        if self.fail_json_mode and "response_format" in kw:
            raise Exception("response_format json_object is not supported")
        return NS(choices=[NS(message=NS(content=self.reply))])


def test_llm_summary_keeps_good_bullets_and_reports_removed_ones():
    reply = json.dumps({"headline": "One fee problem needs action.", "bullets": [
        {"text": "M063 was overcharged ₹26,998.", "fact_ids": ["F1.overcharged", "F1.title"]},
        {"text": "Volume was ₹6,00,00,000.", "fact_ids": ["kpi.gmv"]}], "actions": []})
    out = S.write_summary(FACTS, [], client=FakeLLM(reply), model="fake")
    assert [b["text"] for b in out["bullets"]] == ["M063 was overcharged ₹26,998."]
    assert out["removed"][0]["text"] == "Volume was ₹6,00,00,000." and out["source"].startswith("LLM (fake)")


def test_facts_reach_the_llm_but_raw_data_does_not():
    llm = FakeLLM(json.dumps({"headline": "", "bullets": [{"text": "ok", "fact_ids": ["kpi.gmv"]}]}))
    S.write_summary(FACTS, [], client=llm, model="fake")
    sent = llm.requests[0]["messages"][1]["content"]
    assert "₹26,998" in sent and "pay_0" not in sent
    assert llm.requests[0]["temperature"] == 0.0


def test_provider_without_json_mode_still_works():
    llm = FakeLLM("```json\n" + json.dumps({"bullets": [{"text": "ok", "fact_ids": ["kpi.gmv"]}]}) + "\n```",
                  fail_json_mode=True)
    out = S.write_summary(FACTS, [], client=llm, model="fake")
    assert out["bullets"] and "response_format" not in llm.requests[1]


def test_falls_back_to_template_when_llm_fails_or_invents_everything(wh):
    findings = detect(wh, date(2026, 9, 1), date(2026, 10, 1))
    facts = {**FACTS, "kpi.attempts": "37,656", **{f"{x.id}.title": x.title for x in findings},
             **{f"{x.id}.severity": x.severity for x in findings}}
    broken = FakeLLM("not json at all")
    assert S.write_summary(facts, findings, client=broken, model="fake")["source"].startswith("template (LLM unavailable")
    liar = FakeLLM(json.dumps({"bullets": [{"text": "Volume was ₹1.", "fact_ids": ["kpi.gmv"]}]}))
    out = S.write_summary(facts, findings, client=liar, model="fake")
    assert "every LLM bullet failed" in out["source"] and out["bullets"]
    for b in out["bullets"]:                      # the template passes its own guardrail
        assert S.check_item(b, facts) is None


# ---- report and CLI ----------------------------------------------------------------
def test_report_files(wh, data_dir, tmp_path):
    res = build_report(wh, date(2026, 9, 1), date(2026, 10, 1), str(tmp_path), use_llm=False,
                       planted=planted(data_dir))
    assert res["score"]["found"] == 3 and res["score"]["planted"] == 3
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Business review" in html and "data:image/png;base64," in html and "sql/25_fee_rate_check.sql" in html
    assert "M063" in (tmp_path / "report.md").read_text(encoding="utf-8")
    fact = pd.read_csv(tmp_path / "bi_exports" / "fact_payments_daily.csv")
    assert fact.gmv.sum() == pytest.approx(res["kpis"]["gmv"], rel=1e-9)
    assert len(pd.read_csv(tmp_path / "bi_exports" / "findings.csv")) == 3


def test_cli_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["generate", "--seed", "7"]) == 0
    reply = json.dumps({"headline": "Fee issue.", "bullets": [
        {"text": "M063 was charged 3.50% instead of 2.00%.", "fact_ids": ["F1.effective_rate", "F1.contracted_rate"]}]})
    assert main(["report"], llm_client=FakeLLM(reply), llm_model="fake") == 0
    out = tmp_path / "reports" / "2026-09-01_to_2026-09-30"
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert data["summary"]["bullets"][0]["text"].startswith("M063")
    assert data["detector_score"]["found"] == 3
