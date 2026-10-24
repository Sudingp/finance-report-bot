"""
Command line.

    python -m finbot generate                         # synthetic data with planted problems -> data/
    python -m finbot report                           # last full month in the data
    python -m finbot report --start 2026-09-21 --end 2026-09-28    # any period (end exclusive)
    python -m finbot report --all --no-llm            # whole data range, template summary
    python -m finbot benchmark --seeds 30             # detector precision/recall over many datasets
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta

from finbot.db import Warehouse
from finbot.detect import detect, score_against_planted
from finbot.generate import generate
from finbot.report import build_report


def _load_planted(data_dir):
    path = os.path.join(data_dir, "planted_anomalies.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)["anomalies"]
    return None


def main(argv=None, llm_client=None, llm_model=None) -> int:
    p = argparse.ArgumentParser(prog="finbot")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", default="data")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--no-anomalies", action="store_true")
    r = sub.add_parser("report")
    r.add_argument("--data", default="data")
    r.add_argument("--start", type=date.fromisoformat)
    r.add_argument("--end", type=date.fromisoformat, help="exclusive")
    r.add_argument("--all", action="store_true", help="the whole data range")
    r.add_argument("--out", default="reports")
    r.add_argument("--no-llm", action="store_true")
    b = sub.add_parser("benchmark")
    b.add_argument("--seeds", type=int, default=30)
    a = p.parse_args(argv)

    if a.cmd == "generate":
        stats = generate(a.out, seed=a.seed, anomalies=not a.no_anomalies)
        print(f"wrote {stats} to {a.out}/")
        return 0

    if a.cmd == "benchmark":
        found = planted = false = 0
        for seed in range(1, a.seeds + 1):
            for anomalies in (True, False):
                d = tempfile.mkdtemp()
                generate(d, seed=seed, anomalies=anomalies)
                wh = Warehouse(d)
                lo, hi = wh.data_range()
                s = score_against_planted(detect(wh, lo, hi + timedelta(days=1)), _load_planted(d))
                found, planted, false = found + s["found"], planted + s["planted"], false + len(s["false_alarms"])
                if s["missed"] or s["false_alarms"]:
                    print(f"seed {seed} anomalies={anomalies}: missed {s['missed']} false alarms {s['false_alarms']}")
        print(f"\n{a.seeds * 2} datasets: found {found}/{planted} planted problems "
              f"(recall {found / planted:.1%}), {false} false alarm(s)")
        return 0

    wh = Warehouse(a.data)
    lo, hi = wh.data_range()
    if a.all:
        start, end = lo, hi + timedelta(days=1)
    elif a.start and a.end:
        start, end = a.start, a.end
    else:  # last full calendar month in the data
        end = date(hi.year, hi.month, 1) if hi.day < 28 else (hi + timedelta(days=1)).replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
    out = os.path.join(a.out, f"{start}_to_{end - timedelta(days=1)}")
    res = build_report(wh, start, end, out, use_llm=not a.no_llm, planted=_load_planted(a.data),
                       llm_client=llm_client, llm_model=llm_model)
    print(f"period {start} to {end - timedelta(days=1)}: {len(res['findings'])} finding(s)")
    for f in res["findings"]:
        print(f"  [{f.severity}] {f.title}")
    print(f"summary: {res['summary']['source']}")
    if res["summary"]["removed"]:
        print(f"  {len(res['summary']['removed'])} LLM bullet(s) removed by the number check")
    if res["score"]:
        s = res["score"]
        print(f"detector vs answer key: {s['found']}/{s['planted']} found, {len(s['false_alarms'])} false alarm(s)")
    print(f"report: {os.path.join(out, 'report.html')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
