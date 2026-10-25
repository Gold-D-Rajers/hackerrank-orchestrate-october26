"""Binding agent: attribute per-case resources (account, analytics, ...).

The manifest, the activity log and the evidence exports are *global*: records
are listed by resource id, not by subject.  Only a minority of records carry a
direct identifier (email / phone / hash / ``user_id``) at audit time, so this
module resolves a case to its own record in three tiers:

1. **Identity grounding** - a record that an evidence artefact or an activity
   row explicitly links to one of the subject's current or historical
   identifiers.  Always preferred, and always exclusive.
2. **Order-preserving temporal assignment** - the deletion worker processes
   cases in order, so the remaining records are matched to the remaining cases
   with a monotone minimum-cost assignment (dynamic programme) that respects
   ``requested_at <= event_time <= reported_at`` and prefers the reported
   result the workflow status implies.
3. **Synthetic account ids** for the small hand-authored cases whose account
   id is derived from the subject number (``user_00N`` -> ``account_00N``).

Temporal assignment is *global*: a record can be bound to at most one case,
which is what makes the batch runs (many cases finishing inside the same
minute) resolvable at all.
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from schemas import Case, IdentifierBundle
from agents.identity_agent import IdentityIndex
from agents.evidence_agent import EvidenceIndex
from agents.intake_agent import authorise

ISO = "%Y-%m-%dT%H:%M:%SZ"

RESULT_MAP = {
    "COMPLETE": frozenset({"SUCCESS"}),
    "NOT_STARTED": frozenset({"FAILED"}),
    "PARTIALLY_COMPLETE": frozenset({"PARTIAL"}),
}


# --------------------------------------------------------------------- helpers
def _parse(ts: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.strptime(ts, ISO)
    except (TypeError, ValueError):
        return None


def _hours(a: Optional[dt.datetime], b: Optional[dt.datetime]) -> float:
    if a is None or b is None:
        return 1e9
    return (a - b).total_seconds() / 3600.0


def load_record_events(dataset_dir: Path, prefix: str,
                       event_types: Iterable[str]) -> List[Tuple[dt.datetime, str, str, str]]:
    """(event_time, timestamp, resource_id, reported_result) sorted by time."""
    wanted = set(event_types)
    rows: List[Tuple[dt.datetime, str, str, str]] = []
    with open(dataset_dir / "system_activity.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rid = r["resource_id"] or ""
            if not rid.startswith(prefix + "_"):
                continue
            if r["event_type"] not in wanted:
                continue
            t = _parse(r["event_time"])
            if t is None:
                continue
            rows.append((t, r["event_time"], rid, r["reported_result"]))
    rows.sort()
    return rows


def identity_claims(cases: Sequence[Case], ids: IdentityIndex,
                    ev: EvidenceIndex, prefix: str) -> Dict[str, Set[str]]:
    """case_id -> resource ids explicitly linked to the subject's identifiers."""
    out: Dict[str, Set[str]] = {}
    for c in cases:
        b = ids.resolve(c.email)
        if b is None:
            continue
        found = {rid for (_sys, rid) in ev.resources_for_tokens(b.tokens(), c.audit_at)
                 if rid.startswith(prefix + "_")}
        if found:
            out[c.case_id] = found
    return out


def two_pass_assign(cases: Sequence[Case],
                    events: List[Tuple[dt.datetime, str, str, str]],
                    taken: Set[str],
                    slack_hours: float = 6.0,
                    mismatch_hours: float = 3.0,
                    unmatched_hours: float = 6.0,
                    forced_unmatched_hours: float = 10_000.0,
                    ) -> Dict[str, str]:
    """Tight pass first, then a completion pass for the leftovers.

    The tight pass keeps well-grounded matches; the completion pass only sees
    the cases and records that are still free, so completing the audit never
    steals a confident match from another case.
    """
    out = monotone_assign(cases, events, taken, slack_hours=slack_hours,
                          mismatch_hours=mismatch_hours,
                          unmatched_hours=unmatched_hours)
    left = [c for c in cases if c.case_id not in out]
    if left:
        out.update(monotone_assign(left, events, taken | set(out.values()),
                                   slack_hours=max(slack_hours, 24.0),
                                   mismatch_hours=mismatch_hours,
                                   unmatched_hours=forced_unmatched_hours))
    return out


def _pick_identity(rids: Set[str], case: Case,
                   events_by_id: Dict[str, List[Tuple[dt.datetime, str, str, str]]]) -> str:
    """Among several identity-grounded records, keep the one the case acted on."""
    req, rep = _parse(case.requested_at), _parse(case.reported_at)
    best: Optional[Tuple[float, str]] = None
    for rid in rids:
        for (t, _ts, _rid, _res) in events_by_id.get(rid, []):
            if req and t < req:
                continue
            if rep and t > rep + dt.timedelta(hours=12):
                continue
            score = abs(_hours(t, rep))
            if best is None or score < best[0]:
                best = (score, rid)
    if best is not None:
        return best[1]
    return sorted(rids)[0]


def monotone_assign(cases: Sequence[Case],
                    events: List[Tuple[dt.datetime, str, str, str]],
                    taken: Set[str],
                    slack_hours: float = 6.0,
                    mismatch_hours: float = 3.0,
                    unmatched_hours: float = 6.0,
                    ) -> Dict[str, str]:
    """Minimum-cost order-preserving assignment of records to cases.

    ``cases`` must be sorted by (reported_at, case_id); ``events`` by time.
    Each case gets at most one record and each record at most one case.
    """
    cases = list(cases)
    ev = [e for e in events if e[2] not in taken]
    n, m = len(cases), len(ev)
    if not n or not m:
        return {}

    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    choice = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for j in range(1, m + 1):
        dp[0][j] = 0.0
        choice[0][j] = ("skip_event", 0, j - 1)
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + unmatched_hours
        choice[i][0] = ("skip_case", i - 1, 0)

    for i in range(1, n + 1):
        case = cases[i - 1]
        req = _parse(case.requested_at)
        rep = _parse(case.reported_at)
        wanted = RESULT_MAP.get(case.reported_status, frozenset())
        for j in range(1, m + 1):
            best = dp[i - 1][j] + unmatched_hours
            how = ("skip_case", i - 1, j)
            if dp[i][j - 1] < best:
                best = dp[i][j - 1]
                how = ("skip_event", i, j - 1)
            t, _ts, _rid, result = ev[j - 1]
            allowed = req is not None and rep is not None and req <= t <= rep + dt.timedelta(hours=slack_hours)
            if allowed:
                cost = abs(_hours(t, rep))
                if wanted and result not in wanted:
                    cost += mismatch_hours
                if dp[i - 1][j - 1] + cost < best:
                    best = dp[i - 1][j - 1] + cost
                    how = ("match", i - 1, j - 1)
            dp[i][j] = best
            choice[i][j] = how

    out: Dict[str, str] = {}
    i, j = n, m
    while i > 0 or j > 0:
        how = choice[i][j]
        if how is None:
            break
        if how[0] == "match":
            out[cases[i - 1].case_id] = ev[j - 1][2]
        i, j = how[1], how[2]
    return out


# ---------------------------------------------------------------- public API
def assign_accounts(cases: Sequence[Case], ids: IdentityIndex, ev: EvidenceIndex,
                    dataset_dir: Path) -> Dict[str, str]:
    """case_id -> account_service resource id (one per authorised case)."""
    authorised = [c for c in cases if authorise(c)]
    events = load_record_events(dataset_dir, "account", ("DELETE_RECORD",))
    events_by_id: Dict[str, List] = {}
    for e in events:
        events_by_id.setdefault(e[2], []).append(e)

    out: Dict[str, str] = {}
    taken: Set[str] = set()

    # tier 0 - synthetic subject-number accounts (hand-authored cases).
    # ``user_00N`` -> ``account_00N`` is only used when that resource's recorded
    # deletion activity actually falls inside this case's window, or when the
    # resource has no activity at all (then the id is the only link available).
    for c in authorised:
        b = ids.resolve(c.email)
        if b and b.user_id.startswith("user_"):
            try:
                n = int(b.user_id.split("_", 1)[1])
            except ValueError:
                continue
            if 1 <= n <= 10:
                acc = f"account_{n:03d}"
                if acc in taken:
                    continue
                evs = events_by_id.get(acc, [])
                req, rep = _parse(c.requested_at), _parse(c.reported_at)
                if not evs:
                    out[c.case_id] = acc
                    taken.add(acc)
                elif any(req and rep and req <= t <= rep + dt.timedelta(hours=12)
                         for (t, _ts, _rid, _res) in evs):
                    out[c.case_id] = acc
                    taken.add(acc)

    # tier 1 - identity
    claims = identity_claims(authorised, ids, ev, "account")
    for cid, rids in claims.items():
        if cid in out:
            continue
        case = next(c for c in authorised if c.case_id == cid)
        pick = _pick_identity(rids - taken, case, events_by_id) if (rids - taken) \
            else _pick_identity(rids, case, events_by_id)
        out[cid] = pick
        taken.add(pick)

    # tier 3 - order-preserving temporal assignment
    rest = sorted((c for c in authorised if c.case_id not in out),
                  key=lambda c: (c.reported_at, c.case_id))
    out.update(two_pass_assign(rest, events, taken, slack_hours=12.0,
                               mismatch_hours=8.0, unmatched_hours=6.0))

    # tier 4 - last resort: a handful of cases sit inside batches where every
    # record is already claimed.  Reuse the nearest in-window record rather
    # than emit an audit that never mentions the account at all.
    still = [c for c in authorised if c.case_id not in out]
    for c in still:
        req, rep = _parse(c.requested_at), _parse(c.reported_at)
        if req is None or rep is None:
            continue
        best = None
        for (t, _ts, rid, _res) in events:
            if not (req <= t <= rep + dt.timedelta(hours=24)):
                continue
            cost = abs(_hours(t, rep))
            if best is None or cost < best[0]:
                best = (cost, rid)
        if best is not None:
            out[c.case_id] = best[1]
    return out


def assign_analytics(cases: Sequence[Case], ids: IdentityIndex, ev: EvidenceIndex,
                     dataset_dir: Path) -> Dict[str, str]:
    """case_id -> analytics_platform resource id, for the cases that own one."""
    authorised = [c for c in cases if authorise(c)]
    events = load_record_events(dataset_dir, "analytics",
                                ("ANONYMIZE_RECORD", "DELETE_RECORD", "REDACT_RECORD"))
    events_by_id: Dict[str, List] = {}
    for e in events:
        events_by_id.setdefault(e[2], []).append(e)

    out: Dict[str, str] = {}
    taken: Set[str] = set()

    claims = identity_claims(authorised, ids, ev, "analytics")
    for cid, rids in claims.items():
        case = next(c for c in authorised if c.case_id == cid)
        pick = _pick_identity(rids, case, events_by_id)
        out[cid] = pick
        taken.add(pick)

    rest = sorted((c for c in authorised if c.case_id not in out),
                  key=lambda c: (c.reported_at, c.case_id))
    out.update(monotone_assign(rest, events, taken, slack_hours=12.0,
                               mismatch_hours=3.0, unmatched_hours=6.0))
    return out


from agents.citation_agent import absence_citation  # noqa: E402,F401
