#!/usr/bin/env python3
"""Evaluation harness for the Ghost Data audit agent.

Three suites:

1. **Sample agreement** - compares a predictions file with
   ``dataset/sample_output.csv`` (the 32 provided answers) and reports
   case-level status accuracy, step-level accuracy (exact case and step match
   ratio), per-field accuracy and sequence accuracy.
2. **Custom behaviour cases** - ``custom_cases.py`` drives the pipeline with
   hand-built situations the sample hardly covers.
3. **Contract audit** - re-validates every emitted row against the output
   contract (phases, operations, cites, ordering, step numbering).

Usage:
    python3 code/evaluation/main.py [predictions.csv] [--full]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Dict, List

CODE = Path(__file__).resolve().parent.parent
ROOT = CODE.parent
DATASET = ROOT / "dataset"
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "evaluation"))

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]

PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
OP_ORDER = {
    "REPORT_REMAINING_RECORD": 1, "REPORT_EXPOSED_PII": 2,
    "REPORT_ACTIVE_DATA_FLOW": 3, "REPORT_RESTORE_RISK": 4,
    "DISABLE_DATA_FLOW": 1, "BLOCK_RESTORE": 2, "DELETE_RECORD": 3,
    "REDACT_RECORD": 4, "ANONYMIZE_RECORD": 5,
    "VERIFY_RECORD_ABSENT": 1, "VERIFY_RECORD_PRESENT": 2,
    "VERIFY_RECORD_REDACTED": 3, "VERIFY_RECORD_ANONYMIZED": 4,
    "VERIFY_DATA_FLOW_DISABLED": 5, "VERIFY_RESTORE_BLOCKED": 6,
}


def load(path: Path) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["case_id"]] = {
                "status": row["deletion_status"],
                "steps": json.loads(row["steps_json"] or "[]"),
            }
    return out


def score(pred_path: Path, gold_path: Path = DATASET / "sample_output.csv") -> dict:
    gold = load(gold_path)
    pred = load(pred_path)

    n = len(gold)
    status_ok = 0
    step_exact_cases = 0
    seq_ok = 0
    field_total = 0
    field_ok = 0
    field_ok_by_name = {k: 0 for k in FIELDS}
    field_total_by_name = {k: 0 for k in FIELDS}
    golden_step_total = 0
    step_match_total = 0
    per_case = []

    for cid in sorted(gold):
        g, p = gold[cid], pred.get(cid)
        if p is None:
            per_case.append((cid, "MISSING", 0, len(g["steps"])))
            continue
        s_ok = p["status"] == g["status"]
        status_ok += s_ok

        gs, ps = g["steps"], p["steps"]
        golden_step_total += len(gs)
        matched = 0
        for a, b in zip(gs, ps):
            if all(a.get(k) == b.get(k) for k in FIELDS):
                matched += 1
        step_match_total += matched
        if len(gs) == len(ps) and matched == len(gs):
            step_exact_cases += 1

        if [s["operation"] for s in gs] == [s["operation"] for s in ps]:
            seq_ok += 1

        for i in range(max(len(gs), len(ps))):
            for k in FIELDS:
                field_total += 1
                field_total_by_name[k] += 1
                a = gs[i].get(k) if i < len(gs) else None
                b = ps[i].get(k) if i < len(ps) else None
                if a == b:
                    field_ok += 1
                    field_ok_by_name[k] += 1
        per_case.append((cid, "OK" if (s_ok and len(gs) == len(ps) and matched == len(gs))
                         else "DIFF", matched, len(gs)))

    return {
        "n": n,
        "status_acc": status_ok / n if n else 0.0,
        "step_case_acc": step_exact_cases / n if n else 0.0,
        "step_match_ratio": step_match_total / golden_step_total if golden_step_total else 0.0,
        "field_acc": field_ok / field_total if field_total else 0.0,
        "field_acc_by_name": {k: (field_ok_by_name[k] / field_total_by_name[k]
                                   if field_total_by_name[k] else 0.0) for k in FIELDS},
        "seq_acc": seq_ok / n if n else 0.0,
        "gold_steps": golden_step_total,
        "matched_steps": step_match_total,
        "per_case": per_case,
    }


def contract_audit(path: Path) -> List[str]:
    """Re-check the submission contract on a predictions file."""
    errs: List[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errs.append(f"bad header {header}")
    ids = [r[0] for r in rows]
    if ids != sorted(ids):
        errs.append("rows not sorted by case_id")
    if len(set(ids)) != len(ids):
        errs.append("duplicate case_id rows")
    valid_files = {r["file_id"] for r in csv.DictReader(open(DATASET / "evidence_files.csv", newline=""))}
    valid_act = {r["activity_id"] for r in csv.DictReader(open(DATASET / "system_activity.csv", newline=""))}
    for cid, status, steps_json in rows:
        try:
            steps = json.loads(steps_json)
        except json.JSONDecodeError:
            errs.append(f"{cid}: steps_json is not JSON")
            continue
        if not isinstance(steps, list):
            errs.append(f"{cid}: steps_json is not a list")
            continue
        if status == "NOT_APPLICABLE":
            if steps:
                errs.append(f"{cid}: NOT_APPLICABLE must be []")
            continue
        if not steps:
            errs.append(f"{cid}: no steps for {status}")
        order_keys = []
        for i, s in enumerate(steps, start=1):
            if s.get("step_no") != i:
                errs.append(f"{cid}: step_no {s.get('step_no')} at position {i}")
            if s.get("phase") not in PHASE_ORDER:
                errs.append(f"{cid}: bad phase {s.get('phase')}")
                continue
            if s.get("operation") not in OP_ORDER:
                errs.append(f"{cid}: bad operation {s.get('operation')}")
            order_keys.append((PHASE_ORDER[s["phase"]], OP_ORDER[s["operation"]],
                               s.get("system_id", ""), s.get("resource_id", "")))
            ev = s.get("evidence_id", "")
            needs = s["phase"] == "FINDING" or (s["phase"] == "VERIFICATION" and status == "COMPLETE")
            if needs and not ev:
                errs.append(f"{cid}: step {i} needs a citation")
            if not needs and ev:
                errs.append(f"{cid}: step {i} must not carry a citation")
            if ev and ev not in valid_files and ev not in valid_act:
                errs.append(f"{cid}: unknown evidence id {ev}")
        if order_keys != sorted(order_keys):
            errs.append(f"{cid}: steps out of the documented order")
        if status == "COMPLETE" and any(s["phase"] != "VERIFICATION" for s in steps):
            errs.append(f"{cid}: COMPLETE with non-verification steps")
    return errs


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pred_path = Path(args[0]) if args else ROOT / "output.csv"
    if not pred_path.is_absolute():
        pred_path = Path.cwd() / pred_path
    if not pred_path.exists():
        print(f"missing predictions file: {pred_path}")
        return 2

    pred = load(pred_path)
    has_sample = bool(set(pred) & set(load(DATASET / "sample_output.csv")))

    print("== sample agreement ==")
    if has_sample:
        r = score(pred_path)
        print(f"cases                        : {r['n']}")
        print(f"status accuracy              : {r['status_acc']*100:.1f}%")
        print(f"step-level (exact case)      : {r['step_case_acc']*100:.1f}%")
        print(f"step-level (match ratio)     : {r['step_match_ratio']*100:.1f}% "
              f"({r['matched_steps']}/{r['gold_steps']} steps)")
        print(f"field-level accuracy         : {r['field_acc']*100:.1f}%")
        for k in FIELDS:
            print(f"    {k:<12s}            : {r['field_acc_by_name'][k]*100:.1f}%")
        print(f"sequence accuracy            : {r['seq_acc']*100:.1f}%")
        diffs = [p for p in r["per_case"] if p[1] != "OK"]
        print(f"\nnon-perfect cases: {len(diffs)}")
        for cid, kind, m, tot in diffs:
            print(f"  {cid}: {kind} matched {m}/{tot} steps")
    else:
        print(f"{pred_path.name} holds no sample case ids "
              "(full-dataset run): sample agreement is not applicable.")

    print("\n== custom behaviour cases ==")
    from custom_cases import run_custom_cases
    custom = run_custom_cases()
    for name, ok, msg in custom:
        print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else " :: " + msg))
    print(f"  {sum(1 for _, ok, _ in custom if ok)}/{len(custom)} custom checks pass")

    if "--full" in sys.argv:
        print("\n== contract audit ==")
        errs = contract_audit(pred_path)
        if errs:
            print(f"  {len(errs)} contract problems")
            for e in errs[:40]:
                print("   - " + e)
        else:
            print("  all rows satisfy the output contract")

    stats = {
        "rows": len(pred),
        "status": {},
        "steps": 0,
    }
    for v in pred.values():
        stats["status"][v["status"]] = stats["status"].get(v["status"], 0) + 1
        stats["steps"] += len(v["steps"])
    print("\n== predictions summary ==")
    print(f"  rows: {stats['rows']}, total steps: {stats['steps']}, "
          f"status mix: {stats['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
