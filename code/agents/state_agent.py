"""State agent: reconstruct each resource's state at the case's audit_at."""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

from schemas import Observation, ResourceState


def state_at_audit(observations: List[Observation], audit_ts: str) -> Optional[ResourceState]:
    """Latest observation at/before audit_ts wins.

    State is reconstructed from observations timestamped at/before audit_ts.
    The citation is separate: it may only name an artefact that was already
    captured at/before audit_ts (otherwise "" for post-audit-only support).
    """
    eligible = [o for o in observations if o.observed_at and o.observed_at <= audit_ts]
    if not eligible:
        return None
    eligible.sort(key=lambda o: (o.observed_at, o.evidence_id))
    obs = eligible[-1]

    citable = [o for o in eligible if not o.captured_at or o.captured_at <= audit_ts]
    citeable_evidence = citable[-1].evidence_id if citable else ""

    exists = True if obs.exists is None else obs.exists
    pii = bool(obs.pii_exposed) if obs.pii_exposed is not None else bool(
        obs.email or obs.phone or obs.email_hash)
    anonymized = bool(obs.anonymized) if obs.anonymized is not None else False
    redacted = bool(obs.redacted) if obs.redacted is not None else False

    if not exists:
        pii = False
        anonymized = False
        redacted = False

    return ResourceState(
        system_id=obs.system_id,
        resource_id=obs.resource_id,
        data_category=obs.data_category,
        observed_at=obs.observed_at,
        evidence_id=citeable_evidence,
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
    elig.sort(key=lambda o: (o.observed_at, o.evidence_id))
    return elig
