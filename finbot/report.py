"""Builds the business review: HTML (for people), Markdown (for GitHub), CSV (for BI tools)."""

import html
import json
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from finbot import charts
from finbot.detect import Finding, detect, inr, pct, score_against_planted
from finbot.summary import build_facts, write_summary

CSS = """
:root{--ink:#1f2a44;--muted:#5b6575;--line:#e3e7ee;--bg:#f6f7f9;--hi:#d1453b;--med:#b7791f;--ok:#1a7f37}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:960px;margin:0 auto;padding:28px 18px}h1{margin:0;font-size:24px}h2{font-size:16px;margin:28px 0 10px}
.meta{color:var(--muted);font-size:13px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:16px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px}.card .k{color:var(--muted);font-size:12px}
.card .v{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}.card .d{font-size:12px;color:var(--muted)}
.box{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}table{border-collapse:collapse;width:100%;background:#fff}
td,th{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;font-size:13px;vertical-align:top}th{color:var(--muted);font-weight:600}
.n{text-align:right;font-variant-numeric:tabular-nums}.sev{font-weight:700;font-size:11px;text-transform:uppercase}
.high{color:var(--hi)}.medium{color:var(--med)}.ok{color:var(--ok)}code{background:var(--bg);padding:1px 4px;border-radius:4px;font-size:12px}
img{width:100%;border:1px solid var(--line);border-radius:10px;background:#fff;margin-bottom:10px}.small{font-size:12px;color:var(--muted)}
.wrap{overflow-x:auto}
"""


def _delta(cur, prev, as_pp=False):
    if not prev:
        return ""
    if as_pp:
        return f"{(cur - prev) * 100:+.1f} pp vs previous period"
    return f"{(cur / prev - 1) * 100:+.1f}% vs previous period" if prev else ""


def build_report(wh, start: date, end: date, out_dir: str, use_llm: bool = True,
                 planted: Optional[List[dict]] = None, llm_client=None, llm_model=None) -> Dict:
    """end is exclusive. Writes report.html, report.md, findings.json and BI exports."""
    os.makedirs(out_dir, exist_ok=True)
    length = end - start
    prev_start = start - length
    p = {"start": pd.Timestamp(start), "end": pd.Timestamp(end), "prev_start": pd.Timestamp(prev_start), "limit": 10}
    kpis = wh.query("10_kpis", p).iloc[0].to_dict()
    lo, _ = wh.data_range()
    prev = (wh.query("10_kpis", {"start": pd.Timestamp(prev_start), "end": pd.Timestamp(start)}).iloc[0].to_dict()
            if prev_start >= lo else {})
    daily = wh.query("11_daily", p)
    top = wh.query("12_top_merchants", p)
    mix = wh.query("13_method_mix", p)
    quality = wh.query("01_quality_checks")
    findings = detect(wh, start, end)
    period = (start.isoformat(), (end - timedelta(days=1)).isoformat())
    facts = build_facts(kpis, prev, findings, top, mix, period)
    summary = write_summary(facts, findings, use_llm, llm_client, llm_model)
    score = None
    if planted is not None:
        in_period = [a for a in planted if a["start"] < end.isoformat() and a["end"] >= start.isoformat()]
        score = score_against_planted(findings, in_period)

    _write_exports(wh, start, end, out_dir, findings)
    with open(os.path.join(out_dir, "findings.json"), "w", encoding="utf-8") as f:
        json.dump({"period": period, "kpis": {k: float(v) for k, v in kpis.items()},
                   "findings": [x.to_dict() for x in findings], "summary": summary, "facts": facts,
                   "detector_score": score}, f, indent=2, ensure_ascii=False)
    page = _html(period, kpis, prev, summary, findings, daily, top, mix, quality, score)
    with open(os.path.join(out_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(_markdown(period, kpis, prev, summary, findings, quality, score))
    return {"findings": findings, "summary": summary, "score": score, "kpis": kpis, "out_dir": out_dir}


def _write_exports(wh, start, end, out_dir, findings):
    """Star-schema CSVs a BI tool (Power BI, Tableau) can load directly."""
    exp = os.path.join(out_dir, "bi_exports")
    os.makedirs(exp, exist_ok=True)
    wh.con.execute("""
        COPY (SELECT created_at::DATE AS date, merchant_id, method,
                     count(*) AS attempts, count(*) FILTER (WHERE status='captured') AS captured,
                     coalesce(sum(amount) FILTER (WHERE status='captured'), 0) AS gmv,
                     sum(fee) AS fee_revenue
              FROM payments WHERE created_at >= $s AND created_at < $e GROUP BY ALL ORDER BY ALL)
        TO '""" + os.path.join(exp, "fact_payments_daily.csv").replace("\\", "/") + "' (HEADER)",
                   {"s": pd.Timestamp(start), "e": pd.Timestamp(end)})
    wh.con.execute("COPY merchants TO '" + os.path.join(exp, "dim_merchants.csv").replace("\\", "/") + "' (HEADER)")
    pd.DataFrame([{**{k: v for k, v in x.to_dict().items() if k != "facts"}, **x.facts} for x in findings]
                 ).to_csv(os.path.join(exp, "findings.csv"), index=False)


def _html(period, kpis, prev, summary, findings, daily, top, mix, quality, score) -> str:
    e = html.escape
    cards = [("Captured volume", inr(kpis["gmv"]), _delta(kpis["gmv"], prev.get("gmv"))),
             ("Success rate", pct(kpis["success_rate"]), _delta(kpis["success_rate"], prev.get("success_rate"), True)),
             ("Fee revenue", inr(kpis["fee_revenue"]), _delta(kpis["fee_revenue"], prev.get("fee_revenue"))),
             ("Refund rate", pct(kpis["refund_rate"], 2), _delta(kpis["refund_rate"], prev.get("refund_rate"), True)),
             ("Payment attempts", f"{int(kpis['attempts']):,}", _delta(kpis["attempts"], prev.get("attempts"))),
             ("Average ticket", inr(kpis["avg_ticket"]), _delta(kpis["avg_ticket"], prev.get("avg_ticket")))]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Business review {period[0]} to {period[1]}</title><style>{CSS}</style></head><body><main>",
           f"<h1>Business review</h1><div class='meta'>{period[0]} to {period[1]} · generated "
           f"{datetime.now().strftime('%d %b %Y %H:%M')} · synthetic demo data</div><div class='cards'>"]
    out += [f"<div class='card'><div class='k'>{k}</div><div class='v'>{v}</div><div class='d'>{d}</div></div>" for k, v, d in cards]
    out.append("</div><h2>Summary</h2><div class='box'>")
    if summary.get("headline"):
        out.append(f"<p><b>{e(summary['headline'])}</b></p>")
    out.append("<ul>" + "".join(f"<li>{e(b['text'])}</li>" for b in summary["bullets"]) + "</ul>")
    if summary.get("actions"):
        out.append("<p><b>Actions</b></p><ul>" + "".join(f"<li>{e(a['text'])}</li>" for a in summary["actions"]) + "</ul>")
    out.append(f"<p class='small'>Written by: {e(summary['source'])}.</p>")
    if summary.get("removed"):
        out.append("<p class='small'>Removed by the number check: " + "; ".join(
            f"“{e(r['text'][:120])}” ({e(r['reason'])})" for r in summary["removed"]) + "</p>")
    out.append("</div><h2>Findings</h2>")
    if findings:
        out.append("<div class='wrap'><table><tr><th>ID</th><th>Severity</th><th>Finding</th><th>Dates</th><th>Numbers</th><th>Query</th></tr>")
        for f in findings:
            nums = "<br>".join(f"{e(k.replace('_', ' '))}: <b>{e(v)}</b>" for k, v in f.facts.items())
            dates = f.start if f.start == f.end else f"{f.start} → {f.end}"
            out.append(f"<tr><td>{f.id}</td><td class='sev {f.severity}'>{f.severity}</td><td>{e(f.title)}</td>"
                       f"<td>{dates}</td><td>{nums}</td><td><code>sql/{f.sql}.sql</code></td></tr>")
        out.append("</table></div>")
    else:
        out.append("<div class='box ok'>No findings in this period.</div>")
    if score:
        out.append(f"<p class='small'>Detector check against the planted answer key for this period: found "
                   f"{score['found']} of {score['planted']} planted problems, {len(score['false_alarms'])} false alarm(s)"
                   + (f"; missed {', '.join(score['missed'])}" if score["missed"] else "") + ".</p>")
    out.append("<h2>Trends</h2>")
    if len(daily):
        out.append(f"<img alt='Daily captured volume' src='data:image/png;base64,{charts.daily_volume(daily, findings)}'>")
        out.append(f"<img alt='Daily success rate' src='data:image/png;base64,{charts.success_rate(daily)}'>")
    out.append("<h2>Top merchants</h2><div class='wrap'><table><tr><th>#</th><th>Merchant</th><th>Category</th>"
               "<th class='n'>Volume</th><th class='n'>Share</th><th class='n'>vs previous</th></tr>")
    for r in top.itertuples():
        chg = "" if pd.isna(r.change_vs_prev) else f"{r.change_vs_prev * 100:+.1f}%"
        out.append(f"<tr><td>{r.rank}</td><td>{e(r.merchant_id)} · {e(r.name)}</td><td>{e(r.category)}</td>"
                   f"<td class='n'>{inr(r.gmv)}</td><td class='n'>{pct(r.share)}</td><td class='n'>{chg}</td></tr>")
    out.append("</table></div><h2>Payment methods</h2><div class='wrap'><table><tr><th>Method</th><th class='n'>Attempts</th>"
               "<th class='n'>Share</th><th class='n'>Success rate</th><th class='n'>Volume</th></tr>")
    for r in mix.itertuples():
        out.append(f"<tr><td>{e(r.method.upper())}</td><td class='n'>{int(r.attempts):,}</td><td class='n'>{pct(r.share_of_attempts)}</td>"
                   f"<td class='n'>{pct(r.success_rate)}</td><td class='n'>{inr(r.gmv)}</td></tr>")
    out.append("</table></div><h2>Data quality</h2><div class='wrap'><table><tr><th>Rule</th><th class='n'>Rows breaking it</th><th>Handling</th></tr>")
    for r in quality.itertuples():
        handling = "excluded from all numbers above" if r.rows else ""
        out.append(f"<tr><td>{e(r.rule)}</td><td class='n {'high' if r.rows else 'ok'}'>{int(r.rows)}</td><td class='small'>{handling}</td></tr>")
    out.append("</table></div><p class='small'>All numbers come from the SQL files in <code>sql/</code>. "
               "BI-ready CSVs are in <code>bi_exports/</code>.</p></main></body></html>")
    return "".join(out)


def _markdown(period, kpis, prev, summary, findings, quality, score) -> str:
    lines = [f"# Business review: {period[0]} to {period[1]}", "", "_Synthetic demo data._", "",
             "| Metric | Value | Change |", "| :--- | ---: | :--- |",
             f"| Captured volume | {inr(kpis['gmv'])} | {_delta(kpis['gmv'], prev.get('gmv'))} |",
             f"| Success rate | {pct(kpis['success_rate'])} | {_delta(kpis['success_rate'], prev.get('success_rate'), True)} |",
             f"| Fee revenue | {inr(kpis['fee_revenue'])} | {_delta(kpis['fee_revenue'], prev.get('fee_revenue'))} |",
             f"| Refund rate | {pct(kpis['refund_rate'], 2)} | {_delta(kpis['refund_rate'], prev.get('refund_rate'), True)} |",
             "", "## Summary", ""]
    if summary.get("headline"):
        lines += [f"**{summary['headline']}**", ""]
    lines += [f"- {b['text']}" for b in summary["bullets"]]
    if summary.get("actions"):
        lines += ["", "**Actions**", ""] + [f"- {a['text']}" for a in summary["actions"]]
    lines += ["", f"_Written by: {summary['source']}._"]
    if summary.get("removed"):
        lines += ["", "Removed by the number check:"] + [f"- {r['text']} ({r['reason']})" for r in summary["removed"]]
    lines += ["", "## Findings", "", "| ID | Severity | Finding | Dates |", "| --- | --- | --- | --- |"]
    lines += [f"| {f.id} | {f.severity} | {f.title} | {f.start} to {f.end} |" for f in findings] or ["| - | - | none | - |"]
    if score:
        lines += ["", f"Detector vs planted answer key: {score['found']}/{score['planted']} found, "
                      f"{len(score['false_alarms'])} false alarm(s)."]
    lines += ["", "## Data quality", "", "| Rule | Rows |", "| --- | ---: |"]
    lines += [f"| {r.rule} | {int(r.rows)} |" for r in quality.itertuples()]
    return "\n".join(lines) + "\n"
