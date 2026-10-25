"""Policy agent: execute deletion_rules.csv deterministically."""
from __future__ import annotations
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from schemas import ResourceState

# data_category -> required_action (overridden by deletion_rules.csv when present)
DEFAULT_CATEGORY_ACTION = {
    "USER_PROFILE": "DELETE",
    "SECONDARY_IDENTIFIER": "DELETE",
    "SEARCH_INDEX_ENTRY": "DELETE",
    "CACHE_ENTRY": "DELETE",
    "SUPPORT_ATTACHMENT": "DELETE",
    "USER_EXPORT": "DELETE",
    "MARKETING_PROFILE": "DELETE",
    "SUPPORT_MESSAGE": "REDACT",
    "EMAIL_DELIVERY_LOG": "REDACT",
    "ACCOUNT_TOMBSTONE": "REDACT",
    "ANALYTICS_PROFILE": "ANONYMIZE",
    "PRODUCT_TELEMETRY": "ANONYMIZE",
    "SECURITY_AUDIT_LOG": "RETAIN",
    "LEGAL_HOLD_RECORD": "RETAIN",
    "BACKUP_ARCHIVE": "RETAIN",
    "DATA_FLOW": "DISABLE_FLOW",      # special: flow must be off
}


@dataclass
class Decision:
    system_id: str
    resource_id: str
    data_category: str
    required_action: str
    compliant: bool
    finding_op: Optional[str]      # REPORT_* if non-compliant
    finding_state: Optional[str]
    remediation_op: Optional[str]  # DELETE_RECORD / ... ; "" none
    verification_op: str
    verification_state: str
    evidence_id: str


class PolicyEngine:
    def __init__(self, dataset_dir: Path):
        self.category_action = dict(DEFAULT_CATEGORY_ACTION)
        self._load_rules(dataset_dir)

    def _load_rules(self, dataset_dir: Path) -> None:
        with open(dataset_dir / "deletion_rules.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cat = (row.get("data_category") or "").strip()
                act = (row.get("required_action") or "").strip().upper()
                if cat and act:
                    self.category_action[cat] = act

    def decide(self, st: ResourceState,
               override: Optional[str] = None) -> Optional[Decision]:
        cat = st.data_category
        action = self.category_action.get(cat)
        if action is None:
            return None
        # The workflow's own planned treatment (from system_activity record
        # events) may correct a category-level guess: e.g. analytics_9914 was
        # submitted for DELETE, support_002 was submitted for DELETE. Never
        # override retention/flow categories.
        if (override in ("DELETE", "ANONYMIZE", "REDACT")
                and cat not in ("SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD",
                                "BACKUP_ARCHIVE", "DATA_FLOW")):
            action = override
        sys_id, rid = st.system_id, st.resource_id

        # ---- data flow -----------------------------------------------------
        if action == "DISABLE_FLOW":
            enabled = st.enabled
            compliant = (enabled is False) or (enabled is None and not st.exists)
            if compliant:
                return Decision(sys_id, rid, cat, action, True, None, None, "",
                                 "VERIFY_DATA_FLOW_DISABLED", "DISABLED", st.evidence_id)
            return Decision(sys_id, rid, cat, action, False,
                            "REPORT_ACTIVE_DATA_FLOW", "ACTIVE",
                            "DISABLE_DATA_FLOW",
                            "VERIFY_DATA_FLOW_DISABLED", "DISABLED", st.evidence_id)

        # ---- backup archive ------------------------------------------------
        if cat == "BACKUP_ARCHIVE" or action == "RETAIN" and cat == "BACKUP_ARCHIVE":
            archive = st.archive_present if st.archive_present is not None else st.exists
            restore_ok = bool(st.restore_allowed)
            compliant = bool(archive) and not restore_ok
            if compliant:
                return Decision(sys_id, rid, cat, "RETAIN", True, None, None, "",
                                 "VERIFY_RESTORE_BLOCKED", "BLOCKED", st.evidence_id)
            if archive and restore_ok:
                return Decision(sys_id, rid, cat, "RETAIN", False,
                                "REPORT_RESTORE_RISK", "ACTIVE",
                                "BLOCK_RESTORE",
                                "VERIFY_RESTORE_BLOCKED", "BLOCKED", st.evidence_id)
            # archive missing entirely -> restore cannot happen; still verify blocked
            return Decision(sys_id, rid, cat, "RETAIN", False,
                            "REPORT_RESTORE_RISK", "ACTIVE",
                            "BLOCK_RESTORE",
                            "VERIFY_RESTORE_BLOCKED", "BLOCKED", st.evidence_id)

        # ---- retain (security audit / legal hold) --------------------------
        if action == "RETAIN":
            compliant = st.exists and not st.pii_exposed
            # retained records must remain present; they are not redacted
            if compliant:
                return Decision(sys_id, rid, cat, "RETAIN", True, None, None, "",
                                 "VERIFY_RECORD_PRESENT", "PRESENT", st.evidence_id)
            # a missing retained record is unusual; report remaining if present with PII
            if st.exists and st.pii_exposed:
                return Decision(sys_id, rid, cat, "RETAIN", False,
                                "REPORT_EXPOSED_PII", "PRESENT",
                                "REDACT_RECORD",
                                "VERIFY_RECORD_REDACTED", "REDACTED", st.evidence_id)
            # absent retained record: treat as compliant-only verification of presence
            return Decision(sys_id, rid, cat, "RETAIN", False,
                            None, None, "",
                            "VERIFY_RECORD_PRESENT", "PRESENT", st.evidence_id)

        # ---- delete --------------------------------------------------------
        if action == "DELETE":
            if not st.exists:
                return Decision(sys_id, rid, cat, "DELETE", True, None, None, "",
                                 "VERIFY_RECORD_ABSENT", "ABSENT", st.evidence_id)
            return Decision(sys_id, rid, cat, "DELETE", False,
                            "REPORT_REMAINING_RECORD", "PRESENT",
                            "DELETE_RECORD",
                            "VERIFY_RECORD_ABSENT", "ABSENT", st.evidence_id)

        # ---- redact --------------------------------------------------------
        if action == "REDACT":
            if cat == "ACCOUNT_TOMBSTONE":
                if st.exists and not st.pii_exposed:
                    return Decision(sys_id, rid, cat, "REDACT", True, None, None, "",
                                     "VERIFY_RECORD_REDACTED", "REDACTED", st.evidence_id)
                return Decision(sys_id, rid, cat, "REDACT", False,
                                "REPORT_EXPOSED_PII", "PRESENT",
                                "REDACT_RECORD",
                                "VERIFY_RECORD_REDACTED", "REDACTED", st.evidence_id)
            if not st.exists:
                # record gone entirely satisfies redact
                return Decision(sys_id, rid, cat, "REDACT", True, None, None, "",
                                 "VERIFY_RECORD_REDACTED", "REDACTED", st.evidence_id)
            if st.redacted or not st.pii_exposed:
                return Decision(sys_id, rid, cat, "REDACT", True, None, None, "",
                                 "VERIFY_RECORD_REDACTED", "REDACTED", st.evidence_id)
            return Decision(sys_id, rid, cat, "REDACT", False,
                            "REPORT_EXPOSED_PII", "PRESENT",
                            "REDACT_RECORD",
                            "VERIFY_RECORD_REDACTED", "REDACTED", st.evidence_id)

        # ---- anonymize -----------------------------------------------------
        if action == "ANONYMIZE":
            if not st.exists:
                return Decision(sys_id, rid, cat, "ANONYMIZE", True, None, None, "",
                                 "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", st.evidence_id)
            if not st.pii_exposed or (st.anonymized and not st.pii_exposed):
                # no direct identifiers remain -> effectively anonymized
                return Decision(sys_id, rid, cat, "ANONYMIZE", True, None, None, "",
                                 "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", st.evidence_id)
            return Decision(sys_id, rid, cat, "ANONYMIZE", False,
                            "REPORT_EXPOSED_PII", "PRESENT",
                            "ANONYMIZE_RECORD",
                            "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", st.evidence_id)

        return None
