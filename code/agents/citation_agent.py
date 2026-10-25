"""Citation agent: make sure every step can point at participant-visible proof.

The contract is strict about citations:

* ``FINDING`` steps and every ``VERIFICATION`` step of a ``COMPLETE`` case must
  name a ``file_id``, ``activity_id`` or ``audit_id``;
* remediation and post-remediation verification steps must be empty.

The state agent already returns the artefact that proves the state, but that
artefact is only admissible when it was captured at/before ``audit_at``.  Where
no such artefact names the record, this module falls back - in order - to

1. the record's own operational history: a ``FAILED``/``PARTIAL`` action is
   evidence that the required treatment did not happen (findings), a
   ``SUCCESS`` action is evidence that it did (compliant verification);
2. an *absence* citation: an artefact captured before the audit that enumerates
   the record's collection and therefore proves the record is missing from it.

A decision that still has no admissible citation is dropped: reporting an
uncited finding would be an unsupported claim, which is exactly the failure
mode this audit is meant to catch.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Set

from schemas import Case
from agents.evidence_agent import EvidenceIndex

FINDING_OPS = {
    "REPORT_REMAINING_RECORD",
    "REPORT_EXPOSED_PII",
    "REPORT_ACTIVE_DATA_FLOW",
    "REPORT_RESTORE_RISK",
}

ABSENT_VERIFICATIONS = {"VERIFY_RECORD_ABSENT"}

# Systems whose artefacts can testify about one another: the account record is
# replaced by a tombstone in the identity service, support attachments live in
# object storage, and so on.
SYSTEM_FAMILY = {
    "account_service": {"account_service", "identity_service"},
    "identity_service": {"identity_service", "account_service"},
    "support_platform": {"support_platform", "object_storage"},
    "object_storage": {"object_storage", "support_platform"},
    "security_service": {"security_service"},
    "analytics_platform": {"analytics_platform"},
    "search_service": {"search_service"},
    "integration_service": {"integration_service"},
    "backup_service": {"backup_service"},
    "export_service": {"export_service"},
    "marketing_platform": {"marketing_platform"},
}


def _family(system_id: str) -> Set[str]:
    return SYSTEM_FAMILY.get(system_id, {system_id})


def activity_citation(ev: EvidenceIndex, resource_id: str, audit_ts: str,
                      prefer_success: bool) -> str:
    """Cite the record's own operational history at/before the audit instant."""
    rows = ev.activities_for(resource_id, audit_ts)
    if not rows:
        return ""
    wanted = ("SUCCESS",) if prefer_success else ("FAILED", "PARTIAL")
    for row in reversed(rows):
        if row["reported_result"] in wanted:
            return row["activity_id"]
    return rows[-1]["activity_id"]


def absence_citation(case: Case, tokens: Set[str], ev: EvidenceIndex,
                     system_id: Optional[str] = None,
                     category: Optional[str] = None) -> str:
    """An artefact captured at/before the audit instant that proves absence.

    Order of strength:

    a. an artefact that enumerates the record's own system *and* was produced
       by a lookup carrying one of the subject's identifiers;
    b. a SQL snapshot that enumerates the record's collection (absence by
       omission from a complete dump);
    c. an artefact that enumerates the record's own system for other subjects;
    d. any artefact carrying one of the subject's identifiers.
    """
    audit = case.audit_at

    def latest(predicate):
        best_key = None
        best_id = ""
        for fid, row in ev.manifest.items():
            cap = row.get("captured_at") or ""
            if cap > audit:
                continue
            if predicate(fid, row):
                key = (cap, fid)
                if best_key is None or key > best_key:
                    best_key, best_id = key, fid
        return best_id

    fam = _family(system_id) if system_id else set()
    if fam:
        # a) subject lookup recorded inside the record's own system family
        hit = latest(lambda fid, row: any(
            o.system_id in fam and (o.link_tokens & tokens)
            for o in ev.by_file.get(fid, ())))
        if hit:
            return hit

    # b) complete dump of the record's collection
    want = category or "USER_PROFILE"
    hit = latest(lambda fid, row: (row.get("file_path") or "").endswith(".sql")
                 and any(o.data_category == want for o in ev.by_file.get(fid, ())))
    if hit:
        return hit

    if fam:
        # c) any artefact enumerating the record's own system family
        hit = latest(lambda fid, row: any(
            o.system_id in fam for o in ev.by_file.get(fid, ())))
        if hit:
            return hit

    # d) any artefact carrying the subject's identifiers
    return latest(lambda fid, row: any(
        o.link_tokens & tokens for o in ev.by_file.get(fid, ())))


def cite_decision(case: Case, tokens: Set[str], decision, ev: EvidenceIndex) -> str:
    """Best admissible citation for one policy decision ("" if none exists)."""
    if decision.evidence_id:
        return decision.evidence_id
    op = decision.finding_op if decision.finding_op else decision.verification_op
    rid = decision.resource_id
    audit = case.audit_at

    if op in FINDING_OPS:
        cited = activity_citation(ev, rid, audit, prefer_success=False)
        return cited

    # verification of a compliant record
    if op in ABSENT_VERIFICATIONS:
        cited = absence_citation(case, tokens, ev, system_id=decision.system_id,
                                 category=decision.data_category)
        if cited:
            return cited
    return activity_citation(ev, rid, audit, prefer_success=True)
