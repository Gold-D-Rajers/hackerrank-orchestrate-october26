"""SQL snapshot parser: executes SQLite-compatible evidence in memory.

Handles the observed dialects:
  * customer_data_inventory_*.sql     -> per-service tables + snapshot_scope
  * customer_data_{read,reporting,failover,regional}_replica*.sql
      -> customer_data_primary (+ replica) with record_json blobs
  * customer_accounts_failover_replica_*.sql -> customer_profiles_primary/_failover
  * customer_search_primary_*.sql     -> search_documents_primary
"""
from __future__ import annotations
import json
import re
import sqlite3
from typing import Dict, List, Optional

from schemas import Observation

# table -> (system_id, data_category)
TABLE_MAP = {
    "customer_profiles": ("account_service", "USER_PROFILE"),
    "customer_profiles_primary": ("account_service", "USER_PROFILE"),
    "customer_profiles_failover_replica": ("account_service", "USER_PROFILE"),
    "customer_search_documents": ("search_service", "SEARCH_INDEX_ENTRY"),
    "search_documents_primary": ("search_service", "SEARCH_INDEX_ENTRY"),
    "customer_analytics_profiles": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support_records": ("support_platform", "SUPPORT_MESSAGE"),
    "customer_exports": ("export_service", "USER_EXPORT"),
    "connector_subscriptions": ("integration_service", "DATA_FLOW"),
    "backup_restore_entries": ("backup_service", "BACKUP_ARCHIVE"),
    "security_audit_records": ("security_service", "SECURITY_AUDIT_LOG"),
    "customer_data_primary": None,      # handled via record_json
    "customer_data_read_replica": None,
    "customer_data_reporting_replica": None,
    "customer_data_failover_replica": None,
    "customer_data_replica_ap_south": None,
    "customer_data_replica_eu_west": None,
    "snapshot_scope": None,
}

COLLECTION_SYSTEM = {
    "customer_profiles": ("account_service", "USER_PROFILE"),
    "customer_analytics_reports": ("analytics_platform", "ANALYTICS_PROFILE"),
    "customer_search_documents": ("search_service", "SEARCH_INDEX_ENTRY"),
    "support_conversations": ("support_platform", "SUPPORT_MESSAGE"),
    "ticket_attachments": ("support_platform", "SUPPORT_ATTACHMENT"),
    "customer_export_jobs": ("export_service", "USER_EXPORT"),
    "connector_configurations": ("integration_service", "DATA_FLOW"),
    "backup_catalog": ("backup_service", "BACKUP_ARCHIVE"),
    "security_audit_events": ("security_service", "SECURITY_AUDIT_LOG"),
    "deleted_customer_references": ("identity_service", "ACCOUNT_TOMBSTONE"),
    "retained_order_records": ("legal_service", "LEGAL_HOLD_RECORD"),
    "operational_record_history": ("unknown", "UNKNOWN"),
    "customer_analytics_profiles": ("analytics_platform", "ANALYTICS_PROFILE"),
}

NULLY = {"", "null", "none", "nil", "0", "false"}


def _truthy(v) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s not in NULLY and s != "0"


def _is_null(v) -> bool:
    if v is None:
        return True
    s = str(v).strip().lower()
    return s in ("", "null", "none", "nil")


def _tokens_from(ident: Dict) -> set:
    toks = set()
    for k in ("email", "owner_email", "customer_email", "requester_email"):
        v = ident.get(k)
        if v and not _is_null(v):
            toks.add(str(v).strip().lower())
    for k in ("phone", "owner_phone", "requester_phone"):
        v = ident.get(k)
        if v and not _is_null(v):
            digits = re.sub(r"\D", "", str(v))
            if digits:
                toks.add(digits)
    for k in ("email_hash",):
        v = ident.get(k)
        if v and not _is_null(v):
            toks.add(str(v).strip().lower())
    for k in ("user_id", "customer_reference", "subject_reference", "user_ref"):
        v = ident.get(k)
        if v and not _is_null(v):
            toks.add(str(v).strip())
    # name intentionally excluded (decoy risk)
    return toks


def _obs_from_record(system_id: str, category: str, rec: Dict, observed_at: str,
                     evidence_id: str) -> Optional[Observation]:
    rid = rec.get("resource_id")
    if not rid:
        return None
    email = rec.get("email") or rec.get("owner_email") or rec.get("requester_email") or rec.get("customer_email")
    phone = rec.get("phone") or rec.get("owner_phone") or rec.get("requester_phone")
    h = rec.get("email_hash")
    name = rec.get("full_name") or rec.get("display_name") or rec.get("requester_name") or rec.get("name")
    uref = rec.get("user_id") or rec.get("customer_reference") or rec.get("subject_reference") or rec.get("subject_reference")

    obs = Observation(
        system_id=system_id,
        resource_id=str(rid),
        data_category=category,
        observed_at=observed_at,
        evidence_id=evidence_id,
        email=str(email) if email and not _is_null(email) else None,
        name=str(name) if name and not _is_null(name) else None,
        phone=str(phone) if phone and not _is_null(phone) else None,
        email_hash=str(h) if h and not _is_null(h) else None,
        user_ref=str(uref) if uref and not _is_null(uref) else None,
        exists=True,
        extra=dict(rec),
    )

    ids_present = [obs.email, obs.phone, obs.email_hash, obs.name]
    any_id = any(x for x in ids_present)

    if category in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"):
        # attributable iff any identifier or user ref present
        attributable = any_id or bool(uref)
        obs.anonymized = not attributable
        obs.pii_exposed = bool(obs.email or obs.phone or obs.email_hash)
        if obs.pii_exposed:
            obs.anonymized = False
    elif category == "SUPPORT_MESSAGE":
        obs.redacted = not any_id and not uref
        obs.pii_exposed = bool(obs.email or obs.phone or obs.name)
        # a support record that only carries customer_reference may still be
        # considered redacted of *direct* identifiers; keep redacted=not any_id
        if uref and not any_id:
            obs.redacted = True
            obs.pii_exposed = False
    elif category == "SUPPORT_ATTACHMENT":
        obs.pii_exposed = any_id or True   # attachment belongs to subject
        obs.redacted = False
    elif category == "ACCOUNT_TOMBSTONE":
        obs.redacted = not any_id
        obs.pii_exposed = any_id
    elif category in ("USER_PROFILE", "SEARCH_INDEX_ENTRY", "USER_EXPORT",
                      "MARKETING_PROFILE", "SECONDARY_IDENTIFIER", "CACHE_ENTRY"):
        obs.pii_exposed = any_id
        obs.redacted = False
    elif category == "BACKUP_ARCHIVE":
        ra = rec.get("restore_allowed")
        obs.restore_allowed = _truthy(ra) if ra is not None else None
        obs.archive_present = True
        obs.pii_exposed = bool(email)
        uref2 = rec.get("customer_reference") or rec.get("subject_reference")
        if uref2 and not _is_null(uref2):
            obs.user_ref = str(uref2)
    elif category == "DATA_FLOW":
        en = rec.get("enabled")
        obs.enabled = _truthy(en) if en is not None else None
        obs.pii_exposed = bool(email)
        uref2 = rec.get("customer_reference") or rec.get("subject_reference") or rec.get("subject_selector")
        if uref2 and not _is_null(uref2):
            obs.user_ref = str(uref2)
    elif category == "SECURITY_AUDIT_LOG":
        obs.exists = True
    elif category == "LEGAL_HOLD_RECORD":
        obs.exists = True

    obs.link_tokens = _tokens_from(rec)
    if obs.user_ref:
        obs.link_tokens.add(obs.user_ref)
    return obs


def parse_sql(text: str, captured_at: str, evidence_id: str) -> List[Observation]:
    out: List[Observation] = []
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(text)
    except sqlite3.Error:
        # fall back: try statement-by-statement
        for stmt in re.split(r";\s*\n", text):
            s = stmt.strip()
            if not s or s.upper().startswith(("BEGIN", "COMMIT", "PRAGMA", "CREATE INDEX", "--")):
                continue
            try:
                conn.execute(s)
            except sqlite3.Error:
                continue

    # snapshot scope override
    scope_time = captured_at
    try:
        for r in conn.execute("SELECT * FROM snapshot_scope"):
            scope_time = r["captured_at"] or scope_time
            break
    except sqlite3.Error:
        pass

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]

    for t in tables:
        if t == "snapshot_scope":
            continue
        try:
            rows = conn.execute(f'SELECT * FROM "{t}"').fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            rec = dict(row)
            if t.startswith("customer_data_"):
                # record_json blob dialect
                system_id = rec.get("origin_system") or "unknown"
                coll = rec.get("collection_name") or ""
                sys_guess, cat = COLLECTION_SYSTEM.get(coll, (system_id, "UNKNOWN"))
                system_id = system_id if system_id != "unknown" else sys_guess
                raw = rec.get("record_json")
                try:
                    inner = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    inner = {}
                merged = dict(inner)
                # flatten nested sub-objects (ev05-style search rows carry
                # identifiers under a nested `document` blob)
                for _k, _v in list(merged.items()):
                    if isinstance(_v, dict):
                        for _ik, _iv in _v.items():
                            merged.setdefault(_ik, _iv)
                merged.setdefault("resource_id", rec.get("resource_id"))
                obs = _obs_from_record(system_id, cat, merged,
                                       rec.get("updated_at") or scope_time, evidence_id)
                if obs:
                    out.append(obs)
                continue

            mapped = TABLE_MAP.get(t)
            if not mapped:
                continue
            system_id, category = mapped
            obs = _obs_from_record(system_id, category, rec,
                                   rec.get("updated_at") or scope_time, evidence_id)
            if obs:
                out.append(obs)
    conn.close()
    return out
