"""Planner agent: build ordered steps_json from policy decisions.

Deterministic ordering (array order is graded):
  phase: FINDING -> REMEDIATION -> VERIFICATION
  then operation order from the spec table, then system_id, then resource_id.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from schemas import PHASE_ORDER, OPERATION_ORDER, Step
from agents.policy_agent import Decision

_REM_STATE = {
    "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED",
    "ANONYMIZE_RECORD": "ANONYMIZED",
    "DISABLE_DATA_FLOW": "DISABLED",
    "BLOCK_RESTORE": "BLOCKED",
}


def _sort(steps: List[Step]) -> List[Step]:
    steps.sort(key=lambda s: (PHASE_ORDER[s.phase],
                              OPERATION_ORDER.get(s.operation, 99),
                              s.system_id, s.resource_id))
    return steps


def plan(decisions: List[Decision], incomplete: bool) -> List[Step]:
    """Build the full step list.

    incomplete=True  -> findings cite evidence; remediation and verification
                        use evidence_id="" (future work).
    incomplete=False -> verification-only, every step cites the evidence that
                        proves the compliant state.
    """
    steps: List[Step] = []

    if incomplete:
        for d in decisions:
            if d.compliant or not d.finding_op:
                continue
            steps.append(Step("FINDING", d.system_id, d.resource_id,
                              d.finding_op, d.finding_state or "PRESENT",
                              d.evidence_id))
        for d in decisions:
            if d.compliant or not d.remediation_op:
                continue
            steps.append(Step("REMEDIATION", d.system_id, d.resource_id,
                              d.remediation_op, _REM_STATE[d.remediation_op], ""))
        for d in decisions:
            steps.append(Step("VERIFICATION", d.system_id, d.resource_id,
                              d.verification_op, d.verification_state, ""))
    else:
        for d in decisions:
            steps.append(Step("VERIFICATION", d.system_id, d.resource_id,
                              d.verification_op, d.verification_state,
                              d.evidence_id))

    # de-duplicate identical (phase, system, resource, operation) keeping first
    seen = set()
    dedup: List[Step] = []
    for s in steps:
        k = (s.phase, s.system_id, s.resource_id, s.operation)
        if k in seen:
            continue
        seen.add(k)
        dedup.append(s)
    return _sort(dedup)


def status_for(decisions: List[Decision]) -> str:
    """COMPLETE only if every decision is compliant."""
    if not decisions:
        return "COMPLETE"
    return "COMPLETE" if all(d.compliant for d in decisions) else "INCOMPLETE"
