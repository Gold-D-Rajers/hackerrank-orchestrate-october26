#!/usr/bin/env python3
"""Ghost Data Still Exists - deletion-audit agent (deterministic-first).

Run from the repository root:

    python3 code/main.py                 # full dataset  -> ./output.csv
    python3 code/main.py --sample        # 32 sample rows -> ./output_sample.csv
    python3 code/main.py --out some.csv

Pipeline (see ARCHITECTURE.md):

    intake      classify the request text          (is deletion authorised?)
    identity    resolve current + historical ids   (emails, phones, hashes, user_id)
    evidence    parse every evidence artefact once into point-in-time observations
    binding     attribute per-case records (identity, then temporal assignment)
    state       reconstruct each record's state at audit_at (capture-time gated)
    policy      apply dataset/deletion_rules.csv to every record
    citation    attach admissible evidence ids, drop unsupportable claims
    planner     order findings -> remediation -> verification
    verifier    contract checks on every emitted row

No network access, no API keys, no LLM calls: the whole audit is reproducible
from the participant-visible files in ``dataset/``.
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

from schemas import Case, IdentifierBundle, ResourceState, Step  # noqa: E402
from agents.intake_agent import authorise  # noqa: E402
from agents.identity_agent import IdentityIndex  # noqa: E402
from agents.evidence_agent import EvidenceIndex  # noqa: E402
from agents.account_agent import (assign_accounts, assign_analytics,  # noqa: E402
                                  absence_citation)
from agents.citation_agent import cite_decision  # noqa: E402
from agents.state_agent import state_at_audit  # noqa: E402
from agents.policy_agent import PolicyEngine  # noqa: E402
from agents.planner_agent import plan, status_for  # noqa: E402
from agents.verifier_agent import validate_row  # noqa: E402

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


def collect_decisions(case: Case, tokens: set, keys, ev: EvidenceIndex,
                      policy: PolicyEngine, account_id: Optional[str]) -> List:
    decisions = []
    account_decided = False
    for key in sorted(keys):
        obs_list = ev.by_resource.get(key, [])
        st = state_at_audit(obs_list, case.audit_at)
        if st is None:
            continue
        override = ev.workflow_action.get(key)
        d = policy.decide(st, override[0] if override else None)
        if d is None:
            continue
        decisions.append(d)
        if account_id and key == ("account_service", account_id):
            account_decided = True

    if account_id and not account_decided:
        # The bound account has no row-level observation: its absence is
        # established by omission in an artefact captured before the audit.
        st = ResourceState(
            system_id="account_service",
            resource_id=account_id,
            data_category="USER_PROFILE",
            observed_at=case.audit_at,
            evidence_id=absence_citation(case, tokens, ev,
                                         system_id="account_service",
                                         category="USER_PROFILE"),
            exists=False,
            pii_exposed=False,
            anonymized=False,
            redacted=False,
            enabled=None,
            restore_allowed=None,
            archive_present=None,
            provenance=None,  # type: ignore[arg-type]
        )
        d = policy.decide(st)
        if d is not None:
            decisions.append(d)
    return decisions


def citation_filter(case: Case, tokens: set, decisions: List, ev: EvidenceIndex,
                    status: str) -> List:
    """Attach admissible citations, drop claims that cannot be supported.

    Findings and COMPLETE verifications need a citation.  Post-remediation
    verification steps of an INCOMPLETE case must stay uncited (they describe
    future work), so they are left untouched here.
    """
    needs_citation = status == "COMPLETE"
    kept = []
    for d in decisions:
        if status == "COMPLETE":
            d.evidence_id = cite_decision(case, tokens, d, ev)
            if not d.evidence_id:
                continue
        elif not d.compliant:
            cited = cite_decision(case, tokens, d, ev)
            if not cited:
                # an uncited finding is exactly the kind of unsupported claim
                # this audit exists to catch - drop the whole treatment.
                continue
            d.evidence_id = cited
        kept.append(d)
    return kept


def solve_case(case: Case, ids: IdentityIndex, ev: EvidenceIndex,
               policy: PolicyEngine,
               account_of: Dict[str, str],
               analytics_of: Dict[str, str]) -> Tuple[str, List[Step]]:
    if not authorise(case):
        return "NOT_APPLICABLE", []

    bundle = ids.resolve(case.email)
    if bundle is None:
        return "COMPLETE", []

    tokens = bundle.tokens()
    keys = ev.resources_for_tokens(tokens, case.audit_at)

    acc = account_of.get(case.case_id)
    if acc:
        keys.add(("account_service", acc))
        # keep only the bound account record (drop spurious account hits)
        keys = {k for k in keys if k[0] != "account_service" or k[1] == acc}

    ana = analytics_of.get(case.case_id)
    if ana:
        keys.add(("analytics_platform", ana))
        keys = {k for k in keys if k[0] != "analytics_platform" or k[1] == ana}

    decisions = collect_decisions(case, tokens, keys, ev, policy, acc)
    status = status_for(decisions)
    decisions = citation_filter(case, tokens, decisions, ev, status)
    status = status_for(decisions)
    steps = plan(decisions, incomplete=(status == "INCOMPLETE"))
    return status, steps


def main() -> int:
    t0 = time.time()
    args = sys.argv[1:]
    req_path = DATASET / "deletion_requests.csv"
    out_path = ROOT.parent / "output.csv"
    if "--sample" in args:
        req_path = DATASET / "sample_deletion_requests.csv"
        out_path = ROOT.parent / "output_sample.csv"
    if "--out" in args:
        out_path = Path(args[args.index("--out") + 1]).resolve()

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
    print("Binding records to subjects ...", file=sys.stderr)
    account_of = assign_accounts(all_cases, ids, ev, DATASET)
    analytics_of = assign_analytics(all_cases, ids, ev, DATASET)
    print(f"Solving {len(cases)} cases ...", file=sys.stderr)

    rows = []
    all_errs: List[str] = []
    valid_ev = set(ev.manifest.keys())
    for o in ev.observations:
        if o.evidence_id.startswith("activity_"):
            valid_ev.add(o.evidence_id)
    valid_ev.update(ev.activities.keys() and
                    {a["activity_id"] for rows in ev.activities.values() for a in rows})

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
