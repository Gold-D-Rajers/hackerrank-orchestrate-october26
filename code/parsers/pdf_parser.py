"""PDF evidence parser: deterministic text extraction via pypdf + regex mining."""
from __future__ import annotations
import re
from typing import List, Optional

from schemas import Observation

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_USER_RE = re.compile(r"\buser_\d+\b")
_RID_RE = re.compile(r"\b((?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|marketing|object)_\w+)\b")
_PHONE_RE = re.compile(r"\+\d[\d\s()-]{6,}\d")


def _extract_text(path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception:
        return ""


def _detect_category(text: str, rids: List[str]) -> tuple:
    low = text.lower()
    if "delivery address correction" in low or "attachment" in low:
        return ("support_platform", "SUPPORT_ATTACHMENT")
    if "compliance journal" in low or "retain until" in low:
        return ("security_service", "SECURITY_AUDIT_LOG")
    if "disputed orders" in low or "tax records" in low or "retain until 20" in low:
        return ("legal_service", "LEGAL_HOLD_RECORD")
    if "customer statement" in low or "statement" in low:
        return ("account_service", "USER_PROFILE")
    if rids:
        r0 = rids[0]
        if r0.startswith("audit_record"):
            return ("security_service", "SECURITY_AUDIT_LOG")
        if r0.startswith("support"):
            return ("support_platform", "SUPPORT_MESSAGE")
        if r0.startswith("account"):
            return ("account_service", "USER_PROFILE")
        if r0.startswith("search"):
            return ("search_service", "SEARCH_INDEX_ENTRY")
    return ("unknown", "UNKNOWN")


def parse_pdf(path, evidence_id: str, captured_at: str) -> List[Observation]:
    text = _extract_text(path)
    if not text.strip():
        return []
    rids = list(dict.fromkeys(_RID_RE.findall(text)))
    emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))
    users = list(dict.fromkeys(_USER_RE.findall(text)))
    phones = list(dict.fromkeys(_PHONE_RE.findall(text)))

    if not rids and not emails and not users:
        return []

    system_id, category = _detect_category(text, rids)

    # If multiple resource ids of the same system appear (legal register with many
    # user rows), emit one observation per user row.
    out: List[Observation] = []
    if category == "LEGAL_HOLD_RECORD":
        for u in users:
            obs = Observation(
                system_id=system_id,
                resource_id=f"legal_{u}",       # stable pseudo id from content
                data_category=category,
                observed_at=captured_at,
                evidence_id=evidence_id,
                user_ref=u,
                exists=True,
            )
            obs.link_tokens.add(u)
            out.append(obs)
        return out

    rid = rids[0] if rids else (f"doc_{evidence_id}")
    obs = Observation(
        system_id=system_id,
        resource_id=rid,
        data_category=category,
        observed_at=captured_at,
        evidence_id=evidence_id,
        exists=True,
    )
    if emails:
        obs.email = emails[0]
        obs.link_tokens.add(emails[0].lower())
    if users:
        obs.user_ref = users[0]
        obs.link_tokens.add(users[0])
    if phones:
        obs.phone = phones[0]
        obs.link_tokens.add(re.sub(r"\D", "", phones[0]))

    if category == "SUPPORT_ATTACHMENT":
        obs.pii_exposed = True
        obs.redacted = False
    elif category == "SECURITY_AUDIT_LOG":
        obs.exists = True
        obs.pii_exposed = False
    elif category == "LEGAL_HOLD_RECORD":
        obs.exists = True
    else:
        obs.pii_exposed = bool(obs.email or obs.phone)
    out.append(obs)
    return out
