"""Plain-text handover parser.

Format (evidence_03 style):
  - 2026-10-01T12:00:00Z | account_service | account_146 | USER_PROFILE | NOT_FOUND | e.lindberg18@yahoo.com
  - 2026-10-01T12:00:00Z | security_service | audit_record_2019 | SECURITY_AUDIT_LOG | FOUND | user_35
"""
from __future__ import annotations
import re
from typing import List

from schemas import Observation
from parsers.sql_parser import _is_null

_LINE_RE = re.compile(
    r"^\s*-?\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*$"
)

_STATE_ABSENT = {"NOT_FOUND", "ABSENT", "DELETED", "MISS", "GONE"}
_STATE_PRESENT = {"FOUND", "PRESENT", "ACTIVE", "IDENTIFIABLE", "EXPOSED", "HIT"}


def parse_handover(text: str, evidence_id: str) -> List[Observation]:
    out: List[Observation] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        ts, system_id, rid, category, state, ident = m.groups()
        system_id = system_id.strip()
        category = category.strip().upper().replace(" ", "_")
        state_u = state.strip().upper()

        # normalise system/category
        cat_map = {
            "USER_PROFILE": ("account_service", "USER_PROFILE"),
            "SEARCH_INDEX_ENTRY": ("search_service", "SEARCH_INDEX_ENTRY"),
            "ANALYTICS_PROFILE": ("analytics_platform", "ANALYTICS_PROFILE"),
            "SUPPORT_MESSAGE": ("support_platform", "SUPPORT_MESSAGE"),
            "SUPPORT_ATTACHMENT": ("support_platform", "SUPPORT_ATTACHMENT"),
            "USER_EXPORT": ("export_service", "USER_EXPORT"),
            "DATA_FLOW": ("integration_service", "DATA_FLOW"),
            "BACKUP_ARCHIVE": ("backup_service", "BACKUP_ARCHIVE"),
            "SECURITY_AUDIT_LOG": ("security_service", "SECURITY_AUDIT_LOG"),
            "ACCOUNT_TOMBSTONE": ("identity_service", "ACCOUNT_TOMBSTONE"),
            "MARKETING_PROFILE": ("marketing_platform", "MARKETING_PROFILE"),
        }
        sys_guess, cat = cat_map.get(category, (system_id, category))
        if system_id in ("", "unknown"):
            system_id = sys_guess

        obs = Observation(
            system_id=system_id.strip(),
            resource_id=rid.strip(),
            data_category=cat,
            observed_at=ts,
            evidence_id=evidence_id,
        )
        ident = (ident or "").strip()
        if ident and not _is_null(ident) and ident.upper() not in ("NO DIRECT IDENTIFIER", "NONE", "N/A"):
            if "@" in ident:
                obs.email = ident
                obs.link_tokens.add(ident.lower())
            elif ident.startswith("user_"):
                obs.user_ref = ident
                obs.link_tokens.add(ident)
            else:
                digits = re.sub(r"\D", "", ident)
                if digits:
                    obs.phone = ident
                    obs.link_tokens.add(digits)

        obs.exists = True
        if state_u in _STATE_ABSENT:
            obs.exists = False
            obs.pii_exposed = False
        elif state_u == "ANONYMIZED":
            obs.anonymized = True
            obs.pii_exposed = False
            obs.redacted = False
        elif state_u == "REDACTED":
            obs.redacted = True
            obs.pii_exposed = False
        elif state_u == "BLOCKED":
            obs.restore_allowed = False
            obs.archive_present = True
            obs.pii_exposed = False
        elif state_u == "ACTIVE":
            if cat == "DATA_FLOW":
                obs.enabled = True
            elif cat == "BACKUP_ARCHIVE":
                obs.restore_allowed = True
                obs.archive_present = True
            else:
                obs.exists = True
        elif state_u == "DISABLED":
            if cat == "DATA_FLOW":
                obs.enabled = False
            else:
                obs.exists = True
        elif state_u in _STATE_PRESENT:
            if cat == "DATA_FLOW":
                obs.enabled = True
            elif cat == "BACKUP_ARCHIVE":
                obs.restore_allowed = True
                obs.archive_present = True

        if cat in ("ANALYTICS_PROFILE",):
            obs.pii_exposed = bool(obs.email or obs.phone or obs.email_hash)
            obs.anonymized = not obs.pii_exposed and obs.exists
        elif cat == "ACCOUNT_TOMBSTONE":
            obs.pii_exposed = bool(obs.email or obs.phone or obs.name)
            obs.redacted = obs.exists and not obs.pii_exposed
        elif cat == "SUPPORT_MESSAGE":
            obs.pii_exposed = bool(obs.email or obs.phone or obs.name)
            obs.redacted = obs.exists and not obs.pii_exposed and state_u != "IDENTIFIABLE"
            if state_u == "IDENTIFIABLE":
                obs.pii_exposed = True
                obs.redacted = False
        elif cat in ("USER_PROFILE", "SEARCH_INDEX_ENTRY", "USER_EXPORT",
                     "SUPPORT_ATTACHMENT", "MARKETING_PROFILE"):
            obs.pii_exposed = obs.exists and bool(obs.email or obs.phone or obs.name or state_u in _STATE_PRESENT)
        out.append(obs)
    return out
