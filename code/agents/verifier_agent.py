"""Verifier agent: contract checks on every row before writing output.csv."""
from __future__ import annotations
import json
from typing import Dict, List, Tuple

from schemas import ALLOWED_OP_STATE, PHASES, Step


def validate_row(case_id: str, status: str, steps_json: str,
                 valid_evidence_ids: set) -> List[str]:
    errs: List[str] = []
    if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
        errs.append(f"{case_id}: bad status {status}")
    try:
        raw = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return [f"{case_id}: invalid JSON ({e})"]
    if not isinstance(raw, list):
        return [f"{case_id}: steps_json not a list"]

    if status == "NOT_APPLICABLE":
        if raw != []:
            errs.append(f"{case_id}: NOT_APPLICABLE must have []")
        return errs
    if not raw:
        errs.append(f"{case_id}: empty steps for {status}")
        return errs

    expected_phases = []
    for i, s in enumerate(raw, start=1):
        need = {"step_no", "phase", "system_id", "resource_id",
                "operation", "state", "evidence_id"}
        missing = need - set(s)
        extra = set(s) - need
        if missing or extra:
            errs.append(f"{case_id}: step {i} fields mismatch miss={missing} extra={extra}")
            continue
        if s["step_no"] != i:
            errs.append(f"{case_id}: step_no not consecutive at {i}")
        if (s["phase"], s["operation"], s["state"]) not in ALLOWED_OP_STATE:
            errs.append(f"{case_id}: illegal op combo {s['phase']}/{s['operation']}/{s['state']}")
        expected_phases.append(s["phase"])
        ev = s["evidence_id"]
        if ev and ev not in valid_evidence_ids:
            errs.append(f"{case_id}: unknown evidence_id {ev}")

    # phase ordering
    order = [PHASES.index(p) for p in expected_phases if p in PHASES]
    if order != sorted(order):
        errs.append(f"{case_id}: phases out of order")

    if status == "COMPLETE":
        if any(s["phase"] != "VERIFICATION" for s in raw):
            errs.append(f"{case_id}: COMPLETE must be verification-only")
        if any(not s["evidence_id"] for s in raw):
            errs.append(f"{case_id}: COMPLETE verification missing citation")
    elif status == "INCOMPLETE":
        if any(s["phase"] == "FINDING" and not s["evidence_id"] for s in raw):
            errs.append(f"{case_id}: INCOMPLETE finding without citation")
        if any(s["phase"] in ("REMEDIATION",) and s["evidence_id"] != "" for s in raw):
            errs.append(f"{case_id}: remediation must have evidence_id=''")
        # post-remediation verification must be uncited
        first_verif = next((i for i, s in enumerate(raw) if s["phase"] == "VERIFICATION"), None)
        if first_verif is not None:
            for s in raw[first_verif:]:
                if s["phase"] == "VERIFICATION" and s["evidence_id"] != "":
                    errs.append(f"{case_id}: INCOMPLETE verification must have evidence_id=''")
    return errs
