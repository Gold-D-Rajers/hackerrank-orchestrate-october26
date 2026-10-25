"""Custom evaluation cases (hand-built, beyond the provided sample).

Each case builds synthetic point-in-time observations and drives the real
pipeline components (intake -> state -> policy -> citation -> planner).  They
are *not* copies of the sample: they target decisions that the sample barely
exercises.

  C1  non-deletion request (marketing opt-out)          -> NOT_APPLICABLE / []
  C2  account tombstone keeps user_id, PII removed      -> COMPLETE, VERIFY_RECORD_REDACTED
  C3  analytics record still carries an email           -> INCOMPLETE, REPORT_EXPOSED_PII
  C4  failed account deletion, profile still present    -> INCOMPLETE, REPORT_REMAINING_RECORD
  C5  active sync connector + blocked backup            -> INCOMPLETE, flow + restore ordering
  C6  evidence captured after audit_at                  -> state not provable, no citation
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))

from schemas import Case, IdentifierBundle, Observation  # noqa: E402
from agents.intake_agent import classify_request  # noqa: E402
from agents.state_agent import state_at_audit  # noqa: E402
from agents.policy_agent import PolicyEngine  # noqa: E402
from agents.planner_agent import plan, status_for  # noqa: E402

DATASET = CODE.parent / "dataset"


def _case(**kw) -> Case:
    base = dict(case_id="custom", email="subject@example.com",
                requested_at="2026-05-01T00:00:00Z", reported_status="COMPLETE",
                reported_at="2026-05-02T00:00:00Z", audit_at="2026-05-10T00:00:00Z",
                request_text="Please delete my account.")
    base.update(kw)
    return Case(**base)


def _obs(**kw) -> Observation:
    base = dict(system_id="account_service", resource_id="account_900",
                data_category="USER_PROFILE", observed_at="2026-05-02T00:00:00Z",
                evidence_id="evidence_92", captured_at="2026-05-02T00:00:00Z",
                exists=True)
    base.update(kw)
    return Observation(**base)


def _run(obs_list: List[Observation], case: Case):
    policy = PolicyEngine(DATASET)
    decisions = []
    for o in obs_list:
        st = state_at_audit([x for x in obs_list if x.key() == o.key()], case.audit_at)
        if st is None:
            continue
        d = policy.decide(st)
        if d is not None and d.resource_id not in {x.resource_id for x in decisions}:
            decisions.append(d)
    status = status_for(decisions)
    steps = plan(decisions, incomplete=(status == "INCOMPLETE"))
    return status, steps


def _expect(name: str, got, want) -> Tuple[str, bool, str]:
    ok = got == want
    return (name, ok, "" if ok else f"expected {want!r}, got {got!r}")


def run_custom_cases() -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []

    # ---- C1: non-deletion request -----------------------------------------
    authorised, _, _ = classify_request(
        "Please remove me from marketing lists only. I still want receipts "
        "and service messages for this account.")
    results.append(_expect("C1 non-deletion intake is not authorised",
                           authorised, False))

    # ---- C2: tombstone keeps the stable reference, identifiers removed -----
    case = _case()
    obs = [_obs(system_id="identity_service", resource_id="tombstone_900",
                data_category="ACCOUNT_TOMBSTONE", user_ref="user_900",
                pii_exposed=False, redacted=True)]
    status, steps = _run(obs, case)
    seq = [s.operation for s in steps]
    results.append(_expect("C2 tombstone status", status, "COMPLETE"))
    results.append(_expect("C2 tombstone sequence", seq, ["VERIFY_RECORD_REDACTED"]))

    # ---- C3: analytics profile still attributable --------------------------
    obs = [_obs(system_id="analytics_platform", resource_id="analytics_900",
                data_category="ANALYTICS_PROFILE", email="subject@example.com",
                pii_exposed=True)]
    status, steps = _run(obs, case)
    seq = [s.operation for s in steps]
    results.append(_expect("C3 analytics status", status, "INCOMPLETE"))
    results.append(_expect("C3 analytics sequence", seq,
                           ["REPORT_EXPOSED_PII", "ANONYMIZE_RECORD",
                            "VERIFY_RECORD_ANONYMIZED"]))

    # ---- C4: failed deletion, profile still present ------------------------
    obs = [_obs(email="subject@example.com", pii_exposed=True)]
    status, steps = _run(obs, case)
    seq = [s.operation for s in steps]
    results.append(_expect("C4 remaining profile status", status, "INCOMPLETE"))
    results.append(_expect("C4 remaining profile sequence", seq,
                           ["REPORT_REMAINING_RECORD", "DELETE_RECORD",
                            "VERIFY_RECORD_ABSENT"]))

    # ---- C5: live connector + restore-allowed archive ----------------------
    obs = [
        _obs(system_id="integration_service", resource_id="data_flow_900",
             data_category="DATA_FLOW", enabled=True),
        _obs(system_id="backup_service", resource_id="backup_900",
             data_category="BACKUP_ARCHIVE", archive_present=True,
             restore_allowed=True),
        _obs(exists=False, pii_exposed=False),
    ]
    status, steps = _run(obs, case)
    seq = [(s.phase, s.operation) for s in steps]
    results.append(_expect("C5 flow + restore status", status, "INCOMPLETE"))
    results.append(_expect("C5 remediation precedes record deletion", seq, [
        ("FINDING", "REPORT_ACTIVE_DATA_FLOW"),
        ("FINDING", "REPORT_RESTORE_RISK"),
        ("REMEDIATION", "DISABLE_DATA_FLOW"),
        ("REMEDIATION", "BLOCK_RESTORE"),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT"),
        ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED"),
        ("VERIFICATION", "VERIFY_RESTORE_BLOCKED"),
    ]))

    # ---- C6: only post-audit evidence describes the record -----------------
    post = _obs(observed_at="2026-06-01T00:00:00Z",
                captured_at="2026-06-01T00:00:00Z",
                email="subject@example.com", pii_exposed=True)
    st = state_at_audit([post], "2026-05-10T00:00:00Z")
    results.append(_expect("C6 post-audit capture is not state at audit", st, None))

    # and a record whose only description predates the audit but was captured
    # later must not be citable
    late = _obs(observed_at="2026-05-05T00:00:00Z",
                captured_at="2026-06-01T00:00:00Z", exists=False)
    st = state_at_audit([late], "2026-05-10T00:00:00Z")
    results.append(_expect("C6 late capture yields no citation",
                           "" if st is None else st.evidence_id, ""))
    return results


if __name__ == "__main__":
    for name, ok, msg in run_custom_cases():
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else " :: " + msg))
