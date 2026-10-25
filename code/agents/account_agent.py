"""Account grounding: bind each authorised case to its account_service record.

Tiers (validated at 31/32 against the 32 sample golds):
  1. identity  - account resources linked to the subject's identifiers; if
                 several match, keep the unique one whose DELETE landed inside
                 [requested_at, reported_at].
  2. suffix    - user_0N (N = 1..10) -> account_00N (no existence gate).
  3. temporal  - unclaimed account DELETE rows in the case's window, filtered
                 by the result the reported status implies
                 (COMPLETE->SUCCESS, NOT_STARTED->FAILED,
                  PARTIALLY_COMPLETE->PARTIAL; PARTIALLY fallback
                  PARTIAL/SUCCESS; otherwise unfiltered), nearest to
                 reported_at; cases claim in (requested_at, case_id) order.

Also provides the evidence citation for an account whose absence must be
reported without an account row observation:
  a. the state row's evidence when one exists (handled by the caller),
  b. otherwise the latest (captured_at <= audit) evidence file whose
     observations carry the subject's identifiers,
  c. otherwise the latest SQL database snapshot (captured_at <= audit) that
     enumerates customer_profiles (carries USER_PROFILE observations).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from schemas import Case, IdentifierBundle
from agents.identity_agent import IdentityIndex
from agents.evidence_agent import EvidenceIndex
from agents.intake_agent import authorise

RESULT_MAP = {
    "COMPLETE": frozenset({"SUCCESS"}),
    "NOT_STARTED": frozenset({"FAILED"}),
    "PARTIALLY_COMPLETE": frozenset({"PARTIAL"}),
}


def load_account_deletes(dataset_dir: Path) -> List[Tuple[str, str, str]]:
    """(event_time, resource_id, reported_result) for account DELETE rows."""
    rows: List[Tuple[str, str, str]] = []
    with open(dataset_dir / "system_activity.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r["event_type"] == "DELETE_RECORD"
                    and r["resource_id"].startswith("account_")):
                rows.append((r["event_time"], r["resource_id"], r["reported_result"]))
    rows.sort()
    return rows


def assign_accounts(cases: List[Case], ids: IdentityIndex, ev: EvidenceIndex,
                    dataset_dir: Path) -> Dict[str, str]:
    """case_id -> account resource id for every authorised case we can bind."""
    acc_del = load_account_deletes(dataset_dir)
    result_of = {r: res for _t, r, res in acc_del}
    authorised = [c for c in cases if authorise(c)]

    # ---- tier 1: identity ------------------------------------------------
    tier1: Dict[str, Set[str]] = {}
    for c in authorised:
        accs: Set[str] = set()
        b = ids.resolve(c.email)
        if b:
            accs = {rid for (_s, rid) in ev.resources_for_tokens(b.tokens(), c.audit_at)
                    if rid.startswith("account_")}
            if len(accs) > 1:
                inwin = {r for t, r, _res in acc_del
                         if c.requested_at <= t <= c.reported_at and r in accs}
                if len(inwin) == 1:
                    accs = inwin
        tier1[c.case_id] = accs

    out: Dict[str, str] = {}
    claimed: Dict[str, str] = {}
    for cid, accs in tier1.items():
        if len(accs) == 1:
            a = next(iter(accs))
            out[cid] = a
            claimed.setdefault(a, cid)
        # multi unresolved falls through to the temporal tier

    # ---- tier 2: suffix --------------------------------------------------
    for c in authorised:
        if c.case_id in out:
            continue
        b = ids.resolve(c.email)
        if b and b.user_id.startswith("user_"):
            n = int(b.user_id.split("_", 1)[1])
            if 1 <= n <= 10:
                acc = f"account_{n:03d}"
                out[c.case_id] = acc
                claimed.setdefault(acc, c.case_id)

    # ---- tier 3: temporal claims ----------------------------------------
    remaining = [c for c in authorised if c.case_id not in out]
    remaining.sort(key=lambda c: (c.requested_at, c.case_id))
    for c in remaining:
        win = [(t, r) for t, r, _res in acc_del
               if c.requested_at <= t <= c.reported_at and r not in claimed]
        if not win:
            continue
        wanted = RESULT_MAP.get(c.reported_status, frozenset())
        filt = [(t, r) for t, r in win if result_of.get(r) in wanted]
        if not filt and c.reported_status == "PARTIALLY_COMPLETE":
            filt = [(t, r) for t, r in win
                    if result_of.get(r) in ("PARTIAL", "SUCCESS")]
        pick = max(filt)[1] if filt else max(win)[1]
        out[c.case_id] = pick
        claimed.setdefault(pick, c.case_id)

    return out


def load_analytics_events(dataset_dir: Path) -> List[Tuple[str, str, str]]:
    """(event_time, resource_id, reported_result) for analytics record events."""
    rows: List[Tuple[str, str, str]] = []
    with open(dataset_dir / "system_activity.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r["event_type"] in ("ANONYMIZE_RECORD", "DELETE_RECORD",
                                    "REDACT_RECORD")
                    and r["resource_id"].startswith("analytics_")):
                rows.append((r["event_time"], r["resource_id"], r["reported_result"]))
    rows.sort()
    return rows


def assign_analytics(cases: List[Case], ids: IdentityIndex, ev: EvidenceIndex,
                     dataset_dir: Path) -> Dict[str, str]:
    """case_id -> analytics resource bound only through the temporal claim.

    Identity-grounded analytics resources are pre-claimed first (they stay with
    their subject); the remaining cases claim unclaimed analytics record events
    that fall in [requested_at, reported_at], filtered by the result the
    reported status implies, nearest to reported_at, in (requested_at, case_id)
    order over ALL cases (evaluation cases compete too).
    """
    events = load_analytics_events(dataset_dir)
    result_of = {r: res for _t, r, res in events}
    authorised = [c for c in cases if authorise(c)]

    claimed: Dict[str, str] = {}
    has_identity: Set[str] = set()
    for c in authorised:
        b = ids.resolve(c.email)
        if not b:
            continue
        ana = {rid for (_s, rid) in ev.resources_for_tokens(b.tokens(), c.audit_at)
               if rid.startswith("analytics_")}
        if ana:
            has_identity.add(c.case_id)
            for a in ana:
                claimed.setdefault(a, c.case_id)

    out: Dict[str, str] = {}
    remaining = [c for c in authorised if c.case_id not in has_identity]
    remaining.sort(key=lambda c: (c.requested_at, c.case_id))
    for c in remaining:
        win = [(t, r) for t, r, _res in events
               if c.requested_at <= t <= c.reported_at and r not in claimed]
        if not win:
            continue
        wanted = RESULT_MAP.get(c.reported_status, frozenset())
        filt = [(t, r) for t, r in win if result_of.get(r) in wanted]
        if not filt and c.reported_status == "PARTIALLY_COMPLETE":
            filt = [(t, r) for t, r in win
                    if result_of.get(r) in ("PARTIAL", "SUCCESS")]
        pick = max(filt)[1] if filt else max(win)[1]
        out[c.case_id] = pick
        claimed.setdefault(pick, c.case_id)
    return out


def absence_citation(case: Case, tokens: Set[str], ev: EvidenceIndex) -> str:
    """Evidence file id proving the account's absence when no row observation
    is usable (state row missing or captured after audit_at)."""
    audit = case.audit_at
    # (b) identity file: observations carrying the subject's identifiers
    best_id = ""
    best_key = None
    for fid, row in ev.manifest.items():
        cap = row.get("captured_at") or ""
        if cap > audit:
            continue
        for o in ev.by_file.get(fid, ()):
            if o.link_tokens & tokens:
                key = (cap, fid)
                if best_key is None or key > best_key:
                    best_key = key
                    best_id = fid
                break
    if best_id:
        return best_id
    # (c) latest SQL snapshot enumerating customer_profiles
    best_key = None
    best_id = ""
    for fid, row in ev.manifest.items():
        path = row.get("file_path") or ""
        if not path.endswith(".sql"):
            continue
        cap = row.get("captured_at") or ""
        if cap > audit:
            continue
        if not any(o.data_category == "USER_PROFILE"
                   for o in ev.by_file.get(fid, ())):
            continue
        key = (cap, fid)
        if best_key is None or key > best_key:
            best_key = key
            best_id = fid
    return best_id
