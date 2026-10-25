"""Image evidence parser: OCR via tesseract when available."""
from __future__ import annotations
import re
import shutil
from typing import List

from schemas import Observation

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_USER_RE = re.compile(r"\buser_\d+\b")
_RID_RE = re.compile(r"\b((?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|marketing|object)_\w+)\b")
_PHONE_RE = re.compile(r"\+\d[\d\s()-]{6,}\d")


def _ocr(path) -> str:
    if not shutil.which("tesseract"):
        return ""
    try:
        import subprocess
        r = subprocess.run(["tesseract", str(path), "stdout"],
                           capture_output=True, text=True, timeout=60)
        return r.stdout or ""
    except Exception:
        return ""


def _category_from_text(text: str) -> tuple:
    low = text.lower()
    if "object storage" in low or "bucket" in low:
        return ("object_storage", "SUPPORT_ATTACHMENT")
    if "connector" in low or "integration" in low:
        return ("integration_service", "DATA_FLOW")
    if "backup" in low or "restore" in low:
        return ("backup_service", "BACKUP_ARCHIVE")
    if "support" in low or "ticket" in low:
        return ("support_platform", "SUPPORT_MESSAGE")
    if "export" in low:
        return ("export_service", "USER_EXPORT")
    if "analytic" in low:
        return ("analytics_platform", "ANALYTICS_PROFILE")
    if "search" in low or "index" in low:
        return ("search_service", "SEARCH_INDEX_ENTRY")
    if "security" in low or "audit" in low:
        return ("security_service", "SECURITY_AUDIT_LOG")
    if "marketing" in low or "promotion" in low:
        return ("marketing_platform", "MARKETING_PROFILE")
    if "account" in low or "customer operations" in low or "customers" in low:
        return ("account_service", "USER_PROFILE")
    return ("unknown", "UNKNOWN")


def parse_image(path, evidence_id: str, captured_at: str) -> List[Observation]:
    text = _ocr(path)
    if not text.strip():
        return []
    rids = list(dict.fromkeys(_RID_RE.findall(text)))
    emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))
    users = list(dict.fromkeys(_USER_RE.findall(text)))
    phones = list(dict.fromkeys(_PHONE_RE.findall(text)))
    if not rids and not emails and not users:
        return []

    system_id, category = _category_from_text(text)

    # pick the primary resource: first rid whose prefix matches the category system
    prefix_prefer = {
        "account_service": "account_",
        "search_service": "search_",
        "analytics_platform": "analytics_",
        "support_platform": "support_",
        "export_service": "export_",
        "integration_service": "data_flow_",
        "backup_service": "backup_",
        "security_service": "audit_record_",
        "identity_service": "tombstone_",
        "marketing_platform": "marketing_",
        "object_storage": "object_",
    }.get(system_id)
    rid = None
    if prefix_prefer:
        for r in rids:
            if r.startswith(prefix_prefer):
                rid = r
                break
    if rid is None and rids:
        rid = rids[0]
    if rid is None:
        rid = f"img_{evidence_id}"

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

    low = text.lower()
    if "not found" in low or "absent" in low or "deleted" in low and "status" in low:
        obs.exists = False
        obs.pii_exposed = False
    elif category in ("DATA_FLOW",):
        obs.enabled = ("disabled" not in low) and ("enabled" in low or "active" in low or bool(obs.email))
        if "disabled" in low:
            obs.enabled = False
        elif "enabled" in low or "active" in low:
            obs.enabled = True
        obs.pii_exposed = bool(obs.email)
    elif category == "BACKUP_ARCHIVE":
        obs.archive_present = True
        obs.restore_allowed = not ("blocked" in low)
        if "restore blocked" in low or "restore_allowed false" in low:
            obs.restore_allowed = False
        elif "restore allowed" in low or "restore_allowed true" in low:
            obs.restore_allowed = True
    elif category == "SUPPORT_ATTACHMENT":
        obs.pii_exposed = True
    elif category == "ANALYTICS_PROFILE":
        obs.pii_exposed = bool(obs.email or obs.phone)
        obs.anonymized = obs.exists and not obs.pii_exposed
    else:
        obs.pii_exposed = bool(obs.email or obs.phone) and obs.exists
    return [obs]
