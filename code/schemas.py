"""Typed schemas shared by all agents. No agent invents fields outside these."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")

OPERATION_ORDER = {
    # FINDING
    "REPORT_REMAINING_RECORD": 1,
    "REPORT_EXPOSED_PII": 2,
    "REPORT_ACTIVE_DATA_FLOW": 3,
    "REPORT_RESTORE_RISK": 4,
    # REMEDIATION
    "DISABLE_DATA_FLOW": 1,
    "BLOCK_RESTORE": 2,
    "DELETE_RECORD": 3,
    "REDACT_RECORD": 4,
    "ANONYMIZE_RECORD": 5,
    # VERIFICATION
    "VERIFY_RECORD_ABSENT": 1,
    "VERIFY_RECORD_PRESENT": 2,
    "VERIFY_RECORD_REDACTED": 3,
    "VERIFY_RECORD_ANONYMIZED": 4,
    "VERIFY_DATA_FLOW_DISABLED": 5,
    "VERIFY_RESTORE_BLOCKED": 6,
}

PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}

ALLOWED_OP_STATE = {
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
    ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
    ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
    ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
    ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
}

# Operation used to remediate / verify a given required treatment.
TREATMENT_TO_REMEDIATION = {
    "DELETE": "DELETE_RECORD",
    "REDACT": "REDACT_RECORD",
    "ANONYMIZE": "ANONYMIZE_RECORD",
    "RETAIN": None,
}

TREATMENT_TO_VERIFICATION = {
    "DELETE": "VERIFY_RECORD_ABSENT",
    "REDACT": "VERIFY_RECORD_REDACTED",
    "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED",
    "RETAIN": "VERIFY_RECORD_PRESENT",
}

# Flow / backup special-cases
VERIFY_FLOW = "VERIFY_DATA_FLOW_DISABLED"
VERIFY_BACKUP = "VERIFY_RESTORE_BLOCKED"


@dataclass
class Case:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass
class IdentifierBundle:
    user_id: str
    emails: Set[str] = field(default_factory=set)   # lower-cased
    phones: Set[str] = field(default_factory=set)   # digit-normalised
    hashes: Set[str] = field(default_factory=set)
    names: Set[str] = field(default_factory=set)
    audit_ids: Set[str] = field(default_factory=set)

    def tokens(self) -> Set[str]:
        out = {self.user_id}
        out |= {e for e in self.emails if e}
        out |= {p for p in self.phones if p}
        out |= {h for h in self.hashes if h}
        return out


@dataclass
class Observation:
    """One point-in-time fact about one resource from one evidence artefact."""
    system_id: str
    resource_id: str
    data_category: str
    observed_at: str                    # ISO-8601 Z
    evidence_id: str
    # capture time of the evidence artefact (manifest captured_at); gates
    # eligibility at audit time. Falls back to observed_at for activity rows.
    captured_at: str = ""
    # identifier payloads carried by the record (may be None)
    email: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email_hash: Optional[str] = None
    user_ref: Optional[str] = None      # user_XX or raw subject reference
    # state signals
    exists: Optional[bool] = None       # True present / False absent
    pii_exposed: Optional[bool] = None  # any direct identifier still on record
    anonymized: Optional[bool] = None
    redacted: Optional[bool] = None
    enabled: Optional[bool] = None      # data-flow connectors
    restore_allowed: Optional[bool] = None
    archive_present: Optional[bool] = None
    extra: Dict = field(default_factory=dict)
    # tokens used for subject linkage (emails, phones, hashes, user ids)
    link_tokens: Set[str] = field(default_factory=set)

    def key(self):
        return (self.system_id, self.resource_id)


@dataclass
class ResourceState:
    system_id: str
    resource_id: str
    data_category: str
    observed_at: str
    evidence_id: str
    exists: bool
    pii_exposed: bool
    anonymized: bool
    redacted: bool
    enabled: Optional[bool]
    restore_allowed: Optional[bool]
    archive_present: Optional[bool]
    provenance: Observation


@dataclass
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str

    def to_json_obj(self, step_no: int) -> Dict:
        return {
            "step_no": step_no,
            "phase": self.phase,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "operation": self.operation,
            "state": self.state,
            "evidence_id": self.evidence_id,
        }


@dataclass
class CaseResult:
    case_id: str
    deletion_status: str
    steps: List[Step]
