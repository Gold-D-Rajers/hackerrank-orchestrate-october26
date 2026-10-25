#!/usr/bin/env python3
"""Scorer: compare predictions against dataset/sample_output.csv (32 cases).

Metrics:
  * case-level deletion-status accuracy
  * step-level accuracy  (exact step matches / golden step count)
  * field-level accuracy (per-field match over aligned steps)
  * sequence accuracy    (ordered operation sequence matches exactly)
"""
from __future__ import annotations
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
DATASET = ROOT / "dataset"

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path: Path) -> Dict[str, dict]:
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["case_id"]] = {
                "status": row["deletion_status"],
                "steps": json.loads(row["steps_json"] or "[]"),
            }
    return out


def score(pred_path: Path) -> dict:
    gold = load(DATASET / "sample_output.csv")
    pred = load(pred_path)

    n = len(gold)
    status_ok = 0
    step_exact_cases = 0
    seq_ok = 0
    field_total = 0
    field_ok = 0
    step_match_total = 0
    golden_step_total = 0

    per_case = []
    for cid, g in sorted(gold.items()):
        p = pred.get(cid)
        if p is None:
            per_case.append((cid, "MISSING", 0, 0))
            continue
        s_ok = p["status"] == g["status"]
        status_ok += s_ok

        gs, ps = g["steps"], p["steps"]
        golden_step_total += len(gs)
        # exact step match count (by position)
        m = 0
        for a, b in zip(gs, ps):
            if all(a.get(k) == b.get(k) for k in FIELDS):
                m += 1
        step_match_total += m
        if len(gs) == len(ps) and m == len(gs):
            step_exact_cases += 1

        gseq = [s["operation"] for s in gs]
        pseq = [s["operation"] for s in ps]
        if gseq == pseq:
            seq_ok += 1

        # field-level over max(len) aligned positions
        L = max(len(gs), len(ps))
        for i in range(L):
            for k in FIELDS:
                field_total += 1
                a = gs[i].get(k) if i < len(gs) else None
                b = ps[i].get(k) if i < len(ps) else None
                if a == b:
                    field_ok += 1
        per_case.append((cid, "OK" if (s_ok and m == len(gs) == len(ps)) else "DIFF",
                         m, len(gs)))

    return {
        "n": n,
        "status_acc": status_ok / n if n else 0.0,
        "step_case_acc": step_exact_cases / n if n else 0.0,
        "step_match_ratio": step_match_total / golden_step_total if golden_step_total else 0.0,
        "field_acc": field_ok / field_total if field_total else 0.0,
        "seq_acc": seq_ok / n if n else 0.0,
        "per_case": per_case,
    }


def main() -> int:
    pred = ROOT / "output.csv"
    if len(sys.argv) > 1:
        pred = Path(sys.argv[1])
    r = score(pred)
    print(f"cases                        : {r['n']}")
    print(f"status accuracy              : {r['status_acc']*100:.1f}%")
    print(f"step-level (exact case)      : {r['step_case_acc']*100:.1f}%")
    print(f"step-level (match ratio)     : {r['step_match_ratio']*100:.1f}%")
    print(f"field-level accuracy         : {r['field_acc']*100:.1f}%")
    print(f"sequence accuracy            : {r['seq_acc']*100:.1f}%")
    diffs = [p for p in r["per_case"] if p[1] != "OK"]
    print(f"\nnon-perfect cases: {len(diffs)}")
    for cid, kind, m, tot in diffs[:40]:
        print(f"  {cid}: {kind} matched {m}/{tot} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
