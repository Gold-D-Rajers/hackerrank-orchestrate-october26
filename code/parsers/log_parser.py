"""Log evidence parsers.

Handles three observed shapes:
  1. key=value trace logs (resource_lookup_trace_*.log, service_events_*.log,
     support_replica.log, syslog/application_trace/operations_stream/cloud_runtime)
  2. JSON-lines structured service events
  3. Correlated lookup->resource associations via trace_id
"""
from __future__ import annotations
import json
import re
from typing import Dict, List, Optional, Tuple

from schemas import Observation
from parsers.sql_parser import _is_null, _truthy

_KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
_EMAIL_RE = re.compile(r"[^@\s\"']+@[^@\s\"']+\.[^@\s\"']+")
_USER_RE = re.compile(r"\buser_\d+\b")
_RID_RE = re.compile(r"\b(?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|marketing|object)_\w+\b")


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"')
    return v


def _ts(line: str) -> Optional[str]:
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", line)
    return m.group(1) if m else None


def _tokens_from_kv(kv: Dict) -> set:
    toks = set()
    for key in ("lookup_key", "match_key", "correlation_key", "subject_key",
                "owner_key", "customer_email", "email", "customer_reference",
                "subject_ref", "subject_reference", "subject_selector", "user_ref"):
        v = kv.get(key)
        if not v or _is_null(v):
            continue
        s = str(v).strip()
        if "@" in s:
            toks.add(s.lower())
        elif s.startswith("user_"):
            toks.add(s)
        else:
            digits = re.sub(r"\D", "", s)
            if digits and digits != s:
                toks.add(digits)
            elif s.startswith("+"):
                toks.add(digits)
    return toks


_SERVICE_SYSTEM = {
    "account_service": ("account_service", "USER_PROFILE"),
    "search_service": ("search_service", "SEARCH_INDEX_ENTRY"),
    "analytics_platform": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support_platform": ("support_platform", "SUPPORT_MESSAGE"),
    "support_replica": ("support_platform", "SUPPORT_MESSAGE"),
    "export_service": ("export_service", "USER_EXPORT"),
    "integration_service": ("integration_service", "DATA_FLOW"),
    "backup_service": ("backup_service", "BACKUP_ARCHIVE"),
    "security_service": ("security_service", "SECURITY_AUDIT_LOG"),
    "identity_service": ("identity_service", "ACCOUNT_TOMBSTONE"),
    "marketing_platform": ("marketing_platform", "MARKETING_PROFILE"),
    "object_storage": ("object_storage", "SUPPORT_ATTACHMENT"),
}


def _system_from_service(svc: str) -> Tuple[str, str]:
    if not svc:
        return ("unknown", "UNKNOWN")
    return _SERVICE_SYSTEM.get(svc, (svc, "UNKNOWN"))


def _obs_from_kv(kv: Dict, ts: str, evidence_id: str,
                 forced_rid: Optional[str] = None) -> Optional[Observation]:
    svc = kv.get("service") or kv.get("logger") or kv.get("function") or ""
    system_id, category = _system_from_service(str(svc))
    rid = forced_rid or kv.get("resource_id")
    if not rid:
        return None
    if system_id not in _SERVICE_SYSTEM:
        # infer from the resource prefix (e.g. profile_service lookups -> account)
        pref = str(rid).rsplit("_", 1)[0] if "_" in str(rid) else ""
        guess = _SERVICE_SYSTEM.get(pref)
        if guess:
            system_id, category = guess
        elif str(rid).startswith("account_"):
            system_id, category = "account_service", "USER_PROFILE"
    event = str(kv.get("event") or "")

    obs = Observation(
        system_id=system_id,
        resource_id=str(rid),
        data_category=category,
        observed_at=ts,
        evidence_id=evidence_id,
        link_tokens=_tokens_from_kv(kv),
    )

    email = kv.get("email") or kv.get("customer_email") or kv.get("requester_email") or kv.get("owner_email")
    if email and not _is_null(email):
        obs.email = str(email)
        obs.link_tokens.add(str(email).lower())
    phone = kv.get("phone") or kv.get("requester_phone")
    if phone and not _is_null(phone):
        obs.phone = str(phone)
        obs.link_tokens.add(re.sub(r"\D", "", str(phone)))
    h = kv.get("email_hash")
    if h and not _is_null(h):
        obs.email_hash = str(h)
        obs.link_tokens.add(str(h).lower())
    name = kv.get("requester_name") or kv.get("display_name") or kv.get("full_name")
    if name and not _is_null(name):
        obs.name = str(name)
    uref = kv.get("customer_reference") or kv.get("subject_reference") or kv.get("subject_ref") or kv.get("customer_ref")
    if uref and not _is_null(uref):
        obs.user_ref = str(uref)
        obs.link_tokens.add(str(uref))

    # --- state interpretation
    rows = kv.get("rows")
    http = kv.get("http_status")
    result = kv.get("result")
    eligible = kv.get("eligible")

    obs.exists = True
    obs.pii_exposed = False

    if system_id == "account_service":
        if rows is not None and str(rows) == "0":
            obs.exists = False
        elif http is not None and str(http) == "404":
            obs.exists = False
        elif result in ("miss",):
            obs.exists = False
        if obs.exists:
            obs.pii_exposed = bool(obs.email or obs.phone or obs.name)
    elif system_id == "search_service":
        if result in ("miss", "not_found"):
            obs.exists = False
        elif result in ("hit", "found"):
            obs.exists = True
        if obs.exists:
            obs.pii_exposed = bool(obs.email or obs.phone or obs.name or obs.email_hash)
    elif system_id == "analytics_platform":
        if "scan" in event and result in ("miss",):
            obs.exists = False
        obs.pii_exposed = bool(obs.email or obs.phone or obs.email_hash)
        obs.anonymized = obs.exists and not obs.pii_exposed and not obs.user_ref
    elif system_id == "support_platform":
        obs.pii_exposed = bool(obs.email or obs.phone or obs.name)
        obs.redacted = obs.exists and not obs.pii_exposed
        if "rows" in kv and str(kv.get("rows")) == "0":
            obs.exists = False
    elif system_id == "export_service":
        av = kv.get("available")
        if av is not None:
            obs.exists = _truthy(av)
        obs.pii_exposed = bool(obs.email)
        if obs.exists is False:
            obs.pii_exposed = False
    elif system_id == "integration_service":
        en = kv.get("enabled")
        if en is not None:
            obs.enabled = _truthy(en)
        elif "stopped" in str(kv.get("last_delivery_status") or ""):
            obs.enabled = False
        else:
            obs.enabled = None
        obs.pii_exposed = bool(obs.email)
        if "connector_config_read" in event and obs.enabled is None:
            obs.enabled = True
    elif system_id == "backup_service":
        ra = kv.get("restore_allowed")
        if ra is not None:
            obs.restore_allowed = _truthy(ra)
        ap = kv.get("archive_present")
        if ap is not None:
            obs.archive_present = _truthy(ap)
        if eligible is not None:
            # restore_candidate_evaluated eligible=true => restore risk signal
            obs.extra["eligible"] = _truthy(eligible)
            if _truthy(eligible):
                obs.restore_allowed = True
                obs.archive_present = True
        db = kv.get("deletion_block")
        if db is not None and _truthy(db):
            obs.restore_allowed = False
        obs.pii_exposed = bool(obs.email)
    elif system_id == "security_service":
        rws = kv.get("rows")
        if rws is not None:
            obs.exists = str(rws) != "0"
        else:
            obs.exists = True
    elif system_id == "identity_service":
        obs.pii_exposed = bool(obs.email or obs.phone or obs.name or obs.email_hash)
        obs.redacted = obs.exists and not obs.pii_exposed
    elif system_id == "object_storage":
        obs.exists = True
        obs.pii_exposed = True

    # PII scan events prove exposure
    if event.endswith("_pii_scan") or "pii_scan" in event or event == "message_pii_scan":
        mv = kv.get("matched_value")
        if mv and not _is_null(mv):
            s = str(mv)
            obs.exists = True
            if "@" in s:
                obs.email = s
                obs.link_tokens.add(s.lower())
            obs.pii_exposed = True
            obs.redacted = False
            if system_id == "analytics_platform":
                obs.anonymized = False
    return obs


def parse_kv_log(text: str, evidence_id: str) -> List[Observation]:
    """Parse key=value logs; correlate via trace_id so lookup keys attach
    to the resource_id revealed by the completed event."""
    out: List[Observation] = []
    traces: Dict[str, Dict] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        ts = _ts(line)
        if not ts:
            continue
        kv = {m.group(1): _unquote(m.group(2)) for m in _KV_RE.finditer(line)}
        # strip timestamp token noise
        kv.pop("Z", None)
        # some exports nest all fields inside message="..." — parse them too
        msg = kv.get("message")
        if msg and "=" in msg:
            for m in _KV_RE.finditer(msg):
                k2, v2 = m.group(1), _unquote(m.group(2))
                if k2 not in kv or kv[k2] in ("", None):
                    kv[k2] = v2
        tid = kv.get("trace_id") or kv.get("requestId")
        if tid:
            slot = traces.setdefault(tid, {"tokens": set(), "rid": None,
                                           "system": None, "ts": ts})
            slot["tokens"] |= _tokens_from_kv(kv)
            if kv.get("resource_id"):
                slot["rid"] = kv["resource_id"]
                slot["system"] = kv.get("service") or kv.get("logger")
            slot["ts"] = max(slot["ts"], ts)
        obs = _obs_from_kv(kv, ts, evidence_id)
        if obs:
            out.append(obs)

    # second pass: attach trace tokens to the trace's resource
    for tid, slot in traces.items():
        if not slot["rid"] or not slot["tokens"]:
            continue
        system_id, category = _system_from_service(str(slot["system"] or ""))
        for o in out:
            if (o.resource_id == slot["rid"] and o.evidence_id == evidence_id
                    and (o.system_id == system_id or system_id == "unknown")):
                o.link_tokens |= slot["tokens"]
                if not o.user_ref:
                    for t in slot["tokens"]:
                        if t.startswith("user_"):
                            o.user_ref = t
                            break
                # if the completed line carried no email but the trace did, attach
                if not o.email:
                    for t in slot["tokens"]:
                        if "@" in t:
                            o.email = t
                            break
                if o.pii_exposed is False and o.email:
                    o.pii_exposed = True
                    o.redacted = False
                    if o.data_category == "ANALYTICS_PROFILE":
                        o.anonymized = False
    return out


def parse_jsonl_log(text: str, evidence_id: str) -> List[Observation]:
    out: List[Observation] = []
    traces: Dict[str, Dict] = {}
    for line in text.splitlines():
        line = line.strip()
        brace = line.find("{")
        if brace < 0:
            continue
        ts_m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", line)
        ts = ts_m.group(1) if ts_m else ""
        line = line[brace:]
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        kv = {k: v for k, v in obj.items()}
        if not ts:
            ts = str(kv.get("timestamp") or kv.get("time") or kv.get("@timestamp") or "")
        tid = kv.get("trace_id")
        if tid:
            slot = traces.setdefault(tid, {"tokens": set(), "rid": None,
                                           "system": None, "ts": ts})
            slot["tokens"] |= _tokens_from_kv(kv)
            if kv.get("resource_id"):
                slot["rid"] = kv["resource_id"]
                slot["system"] = kv.get("service") or kv.get("logger")
            slot["ts"] = max(slot["ts"], ts)
        obs = _obs_from_kv(kv, ts, evidence_id)
        if obs:
            out.append(obs)
    for tid, slot in traces.items():
        if not slot["rid"] or not slot["tokens"]:
            continue
        system_id, _ = _system_from_service(str(slot["system"] or ""))
        for o in out:
            if (o.resource_id == slot["rid"] and o.evidence_id == evidence_id
                    and (o.system_id == system_id or system_id == "unknown")):
                o.link_tokens |= slot["tokens"]
                if not o.email:
                    for t in slot["tokens"]:
                        if "@" in t:
                            o.email = t
                            break
                if not o.user_ref:
                    for t in slot["tokens"]:
                        if t.startswith("user_"):
                            o.user_ref = t
                            break
                if o.pii_exposed is False and o.email:
                    o.pii_exposed = True
                    o.redacted = False
                    if o.data_category == "ANALYTICS_PROFILE":
                        o.anonymized = False
    return out


def parse_log(path_name: str, text: str, evidence_id: str) -> List[Observation]:
    # decide by content: JSON lines vs key=value
    sample = "\n".join(text.splitlines()[:20])
    if sample.count("{") >= 3 and '"severity"' in sample:
        return parse_jsonl_log(text, evidence_id)
    return parse_kv_log(text, evidence_id)
