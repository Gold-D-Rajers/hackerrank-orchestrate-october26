#!/usr/bin/env python3
"""Ghost Data Still Exists — deletion-audit agent (deterministic-first).

Run from the repository root:
    python3 code/main.py
Produces output.csv at the repository root with one row per case.
"""
from __future__ import annotations
import csv
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from schemas import Case, IdentifierBundle, Step, ResourceState, Observation
from agents.intake_agent import authorise, classify_request
from agents.identity_agent import IdentityIndex, norm_email
from agents.evidence_agent import EvidenceIndex
from agents.account_agent import assign_accounts, assign_analytics, absence_citation
from agents.state_agent import state_at_audit
from agents.policy_agent import PolicyEngine
from agents.planner_agent import plan, status_for
from agents.verifier_agent import validate_row

DATASET = ROOT.parent / "dataset"


def load_cases(path: Path) -> List[Case]:
    cases: List[Case] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cases.append(Case(
                case_id=row["case_id"],
                email=row["email"],
                requested_at=row["requested_at"],
                reported_status=row["reported_status"],
                reported_at=row["reported_at"],
                audit_at=row["audit_at"],
                request_text=row["request_text"],
            ))
    return cases


def solve_case(case: Case, ids: IdentityIndex, ev: EvidenceIndex,
               policy: PolicyEngine,
               account_of: Dict[str, str],
               analytics_of: Dict[str, str]) -> Tuple[str, List[Step]]:
    if not authorise(case):
        return "NOT_APPLICABLE", []

    bundle = ids.resolve(case.email)
    if bundle is None:
        # No identity match at all: nothing provable; treat as COMPLETE with
        # no verifiable resources? Safer: INCOMPLETE is wrong too. The dataset
        # guarantees every email resolves; fall back to empty COMPLETE.
        return "COMPLETE", []

    tokens = bundle.tokens()
    keys = ev.resources_for_tokens(tokens, case.audit_at)
    acc = account_of.get(case.case_id)
    if acc:
        keys.add(("account_service", acc))
        # keep only the bound account record (drop spurious account hits)
        keys = {k for k in keys
                if k[0] != "account_service" or k[1] == acc}
    ana = analytics_of.get(case.case_id)
    if ana:
        keys.add(("analytics_platform", ana))

    decisions = []
    account_decided = False
    for key in sorted(keys):
        obs_list = ev.by_resource.get(key, [])
        st = state_at_audit(obs_list, case.audit_at)
        if st is None:
            continue
        ov = ev.workflow_action.get(key)
        d = policy.decide(st, ov[0] if ov else None)
        if d is not None:
            decisions.append(d)
            if acc and key == ("account_service", acc):
                account_decided = True

    if acc and not account_decided:
        # Account bound but no usable row observation: the record's absence
        # is established by omission in an evidence file captured by audit.
        st = ResourceState(
            system_id="account_service",
            resource_id=acc,
            data_category="USER_PROFILE",
            observed_at=case.audit_at,
            evidence_id=absence_citation(case, tokens, ev),
            exists=False,
            pii_exposed=False,
            anonymized=False,
            redacted=False,
            enabled=None,
            restore_allowed=None,
            archive_present=None,
            provenance=Observation(
                system_id="account_service",
                resource_id=acc,
                data_category="USER_PROFILE",
                observed_at=case.audit_at,
                evidence_id="",
                exists=False,
            ),
        )
        d = policy.decide(st)
        if d is not None:
            decisions.append(d)

    st = status_for(decisions)
    steps = plan(decisions, incomplete=(st == "INCOMPLETE"))
    return st, steps


def main() -> int:
    t0 = time.time()
    req_path = DATASET / "deletion_requests.csv"
    if len(sys.argv) > 1 and sys.argv[1] == "--sample":
        req_path = DATASET / "sample_deletion_requests.csv"

    print("Loading identities ...", file=sys.stderr)
    ids = IdentityIndex(DATASET)
    print("Indexing evidence ...", file=sys.stderr)
    ev = EvidenceIndex(DATASET)
    print(f"  {len(ev.observations)} observations, "
          f"{len(ev.by_resource)} resources", file=sys.stderr)
    policy = PolicyEngine(DATASET)

    cases = load_cases(req_path)
    # Claim competition always sees the full case universe (evaluation + sample).
    all_cases = load_cases(DATASET / "deletion_requests.csv")
    seen = {c.case_id for c in all_cases}
    for c in load_cases(DATASET / "sample_deletion_requests.csv"):
        if c.case_id not in seen:
            all_cases.append(c)
    print("Assigning account bindings ...", file=sys.stderr)
    account_of = assign_accounts(all_cases, ids, ev, DATASET)
    analytics_of = assign_analytics(all_cases, ids, ev, DATASET)
    print(f"Solving {len(cases)} cases ...", file=sys.stderr)

    rows = []
    all_errs: List[str] = []
    valid_ev = set(ev.manifest.keys())
    # activity ids are also citable evidence
    for o in ev.observations:
        if o.evidence_id.startswith("activity_"):
            valid_ev.add(o.evidence_id)

    from collections import Counter
    dist = Counter()
    for c in cases:
        status, steps = solve_case(c, ids, ev, policy, account_of, analytics_of)
        dist[status] += 1
        steps_json = json.dumps(
            [s.to_json_obj(i) for i, s in enumerate(steps, start=1)],
            separators=(",", ":"), ensure_ascii=False)
        all_errs.extend(validate_row(c.case_id, status, steps_json, valid_ev))
        rows.append((c.case_id, status, steps_json))

    rows.sort(key=lambda r: r[0])
    out_path = ROOT.parent / "output.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for r in rows:
            w.writerow(r)

    print(f"Wrote {len(rows)} rows -> {out_path}", file=sys.stderr)
    print(f"Status distribution: {dict(dist)}", file=sys.stderr)
    if all_errs:
        print(f"VALIDATION ISSUES: {len(all_errs)}", file=sys.stderr)
        for e in all_errs[:40]:
            print("  " + e, file=sys.stderr)
    else:
        print("All rows pass contract validation.", file=sys.stderr)
    print(f"Elapsed {time.time()-t0:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
