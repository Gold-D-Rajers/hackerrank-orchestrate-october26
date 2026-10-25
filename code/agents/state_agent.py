"""State agent: reconstruct each resource's state at the case's audit_at.

Two-level gating (this is the core "claims vs. facts" rule of the challenge):

1. An artefact captured *after* ``audit_at`` is not evidence of the state at
   ``audit_at``.  Its contents may not be used as the observed state and may
   never be cited.  We therefore prefer, for every resource, the latest
   observation coming from an artefact whose ``captured_at`` is at/before the
   audit instant.
2. Only when no such artefact exists do we fall back to a *later-captured*
   artefact that still describes an earlier point in time (``recorded_at`` /
   row timestamp <= audit_at).  That fallback can establish mere existence
   (needed for policy-retained records that must remain present), but it is
   uncitable and can never establish that PII was removed.
"""
from __future__ import annotations
from typing import List, Optional

from schemas import Observation, ResourceState


def _sort_key(o: Observation):
    return (o.observed_at or "", o.evidence_id or "")


def state_at_audit(observations: List[Observation], audit_ts: str) -> Optional[ResourceState]:
    if not observations:
        return None
    observed_before = [o for o in observations if o.observed_at and o.observed_at <= audit_ts]
    if not observed_before:
        return None

    # Tier 1: state as captured at/before the audit instant (citable).
    citable = [o for o in observed_before
               if not o.captured_at or o.captured_at <= audit_ts]
    if citable:
        citable.sort(key=_sort_key)
        obs = citable[-1]
        citation = obs.evidence_id
    else:
        # Tier 2: only post-audit captures describe the record.  Use them to
        # learn that the record existed, but never to claim PII removal and
        # never as a citation.
        observed_before.sort(key=_sort_key)
        obs = observed_before[-1]
        citation = ""

    exists = True if obs.exists is None else obs.exists
    if citation:
        pii = bool(obs.pii_exposed) if obs.pii_exposed is not None else bool(
            obs.email or obs.phone or obs.email_hash)
        anonymized = bool(obs.anonymized) if obs.anonymized is not None else False
        redacted = bool(obs.redacted) if obs.redacted is not None else False
    else:
        # uncitable fallback: assume the worst about identifiers so the audit
        # never certifies a redaction it cannot prove.
        pii = bool(obs.pii_exposed) if obs.pii_exposed else False
        anonymized = False
        redacted = False
        if not pii and not obs.pii_exposed:
            # unknown exposure from an uncitable source: treat identifiers as
            # still exposed for redact/anonymize categories unless the source
            # explicitly states they are gone.
            pii = bool(obs.email or obs.phone or obs.email_hash)

    if not exists:
        pii = False
        anonymized = False
        redacted = False

    return ResourceState(
        system_id=obs.system_id,
        resource_id=obs.resource_id,
        data_category=obs.data_category,
        observed_at=obs.observed_at,
        evidence_id=citation,
        exists=exists,
        pii_exposed=pii,
        anonymized=anonymized,
        redacted=redacted,
        enabled=obs.enabled,
        restore_allowed=obs.restore_allowed,
        archive_present=obs.archive_present,
        provenance=obs,
    )


def timeline(observations: List[Observation], audit_ts: str) -> List[Observation]:
    elig = [o for o in observations if o.observed_at and o.observed_at <= audit_ts]
    elig.sort(key=_sort_key)
    return elig
