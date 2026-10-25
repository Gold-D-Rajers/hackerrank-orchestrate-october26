"""JSON evidence parsers.

Covers:
  * service_resource_export_*.json      (multi-system point-in-time readbacks)
  * customer_data_snapshot_bundle_*.json (snapshots[].records[])
  * connector_configurations_*.json / customer_export_manifest_*.json /
    security_audit_ledger_*.json         (records[] with result flags)
  * customer_analytics_report_*.json / customer_export_jobs_*.json
"""
from __future__ import annotations
import json
from typing import Dict, List, Optional

from schemas import Observation
from parsers.sql_parser import _obs_from_record, COLLECTION_SYSTEM, _is_null, _truthy


def _sys_for(system: str, coll: str = "") -> tuple:
    m = COLLECTION_SYSTEM.get(coll)
    if m and m[0] != "unknown":
        return m
    return (system, None)


# --- service_resource_export -------------------------------------------------

def _obs_service_export(system_id: str, category: str, item: Dict,
                        exported_at: str, evidence_id: str) -> Optional[Observation]:
    rec = dict(item)
    rid = rec.get("resource_id")
    if not rid:
        return None

    # account_service profile_lookups
    if system_id == "account_service":
        prof = rec.get("profile")
        status = rec.get("status_code")
        exists = prof is not None or (status not in (None, 404))
        if prof:
            rec = dict(prof)
            rec.setdefault("resource_id", rid)
        else:
            rec = {"resource_id": rid}
        obs = _obs_from_record("account_service", "USER_PROFILE", rec,
                               rec.get("observed_at") or item.get("observed_at") or exported_at,
                               evidence_id)
        if obs:
            obs.exists = exists
            if not exists:
                obs.pii_exposed = False
            obs.observed_at = item.get("observed_at") or exported_at
        return obs

    if system_id == "search_service":
        hits = rec.get("hits") or {}
        docs = hits.get("documents") if isinstance(hits, dict) else None
        if docs:
            doc = docs[0] if docs else {}
            merged = dict(doc)
            merged.setdefault("resource_id", rid)
            obs = _obs_from_record("search_service", "SEARCH_INDEX_ENTRY", merged,
                                   item.get("observed_at") or exported_at, evidence_id)
            return obs
        total = hits.get("total") if isinstance(hits, dict) else None
        obs = _obs_from_record("search_service", "SEARCH_INDEX_ENTRY",
                               {"resource_id": rid},
                               item.get("observed_at") or exported_at, evidence_id)
        if obs:
            obs.exists = bool(total)
            obs.pii_exposed = bool(total)
        return obs

    if system_id == "analytics_platform":
        return _obs_from_record("analytics_platform", "ANALYTICS_PROFILE", rec,
                                item.get("observed_at") or exported_at, evidence_id)

    if system_id == "support_platform":
        return _obs_from_record("support_platform", "SUPPORT_MESSAGE", rec,
                                item.get("observed_at") or exported_at, evidence_id)

    if system_id == "export_service":
        return _obs_from_record("export_service", "USER_EXPORT", rec,
                                item.get("observed_at") or exported_at, evidence_id)

    if system_id == "integration_service":
        return _obs_from_record("integration_service", "DATA_FLOW", rec,
                                item.get("observed_at") or exported_at, evidence_id)

    if system_id == "backup_service":
        obs = _obs_from_record("backup_service", "BACKUP_ARCHIVE", rec,
                               item.get("observed_at") or exported_at, evidence_id)
        if obs:
            obs.archive_present = True
        return obs

    if system_id == "security_service":
        return _obs_from_record("security_service", "SECURITY_AUDIT_LOG", rec,
                                item.get("observed_at") or exported_at, evidence_id)

    return _obs_from_record(system_id, category or "UNKNOWN", rec,
                            item.get("observed_at") or exported_at, evidence_id)


def parse_service_export(data: Dict, evidence_id: str) -> List[Observation]:
    exported_at = data.get("exported_at") or ""
    out: List[Observation] = []
    se = data.get("service_exports") or {}
    for system, colls in se.items():
        for coll, items in colls.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                obs = _obs_service_export(system, "", item, exported_at, evidence_id)
                if obs:
                    out.append(obs)
    return out


# --- snapshot bundles --------------------------------------------------------

def parse_snapshot_bundle(data: Dict, evidence_id: str) -> List[Observation]:
    out: List[Observation] = []
    for snap in data.get("snapshots") or []:
        store = snap.get("data_store") or ""
        coll = snap.get("collection") or ""
        t = snap.get("captured_at") or data.get("exported_at") or ""
        sys_guess, cat = COLLECTION_SYSTEM.get(coll, (store, "UNKNOWN"))
        system_id = store if store not in ("", "unknown") else sys_guess
        # identity_service deleted_customer_references -> tombstone
        if coll == "deleted_customer_references":
            system_id = "identity_service"
            cat = "ACCOUNT_TOMBSTONE"
        if coll == "customer_profiles":
            system_id = "account_service"
            cat = "USER_PROFILE"
        for rec in snap.get("records") or []:
            if not isinstance(rec, dict):
                continue
            obs = _obs_from_record(system_id, cat, rec, t, evidence_id)
            if obs:
                out.append(obs)
    return out


# --- records[] ledgers (connector configs / export manifest / security ledger)

KNOWN_CATEGORIES = {
    "USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY",
    "SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT", "ANALYTICS_PROFILE",
    "PRODUCT_TELEMETRY", "USER_EXPORT", "MARKETING_PROFILE",
    "EMAIL_DELIVERY_LOG", "SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD",
    "BACKUP_ARCHIVE", "ACCOUNT_TOMBSTONE", "DATA_FLOW",
}

CATEGORY_SYSTEM_HINT = {
    "USER_PROFILE": "account_service",
    "SECONDARY_IDENTIFIER": "account_service",
    "SEARCH_INDEX_ENTRY": "search_service",
    "CACHE_ENTRY": "cache_service",
    "SUPPORT_MESSAGE": "support_platform",
    "SUPPORT_ATTACHMENT": "support_platform",
    "ANALYTICS_PROFILE": "analytics_platform",
    "PRODUCT_TELEMETRY": "analytics_platform",
    "USER_EXPORT": "export_service",
    "MARKETING_PROFILE": "marketing_platform",
    "EMAIL_DELIVERY_LOG": "email_delivery_service",
    "SECURITY_AUDIT_LOG": "security_service",
    "LEGAL_HOLD_RECORD": "legal_service",
    "BACKUP_ARCHIVE": "backup_service",
    "ACCOUNT_TOMBSTONE": "identity_service",
    "DATA_FLOW": "integration_service",
}

_LEDGER_SYSTEM = {
    "connector_configurations": ("integration_service", "DATA_FLOW"),
    "customer_export_manifest": ("export_service", "USER_EXPORT"),
    "security_audit_ledger": ("security_service", "SECURITY_AUDIT_LOG"),
}


def parse_ledger(data: Dict, evidence_id: str, kind: str) -> List[Observation]:
    exported_at = data.get("exported_at") or data.get("captured_at") or ""
    default_system, default_cat = _LEDGER_SYSTEM.get(kind, ("unknown", "UNKNOWN"))
    out: List[Observation] = []
    for rec in data.get("records") or []:
        if not isinstance(rec, dict):
            continue
        system_id = (rec.get("system") or default_system or "unknown").lower()
        # Ledger rows carry their own record_type: a security ledger can list
        # account profiles, an export manifest can list search documents, etc.
        # The row's own type always wins over the file's default category.
        rtype = (rec.get("record_type") or "").upper()
        cat = rtype if rtype in KNOWN_CATEGORIES else default_cat
        if rtype in CATEGORY_SYSTEM_HINT and system_id in ("unknown", "", "data_catalog"):
            system_id = CATEGORY_SYSTEM_HINT[rtype]
        rid = rec.get("resource_id")
        if not rid:
            continue
        t = rec.get("recorded_at") or exported_at
        result = (rec.get("result") or "").upper()
        sub = rec.get("subject_reference")

        merged: Dict = {"resource_id": rid}
        if sub and not _is_null(sub):
            if "@" in str(sub):
                merged["email"] = sub
            else:
                merged["subject_reference"] = sub

        obs = _obs_from_record(system_id, cat, merged, t, evidence_id)
        if not obs:
            continue
        if result in ("NOT_FOUND", "ABSENT", "MISS"):
            obs.exists = False
            obs.pii_exposed = False
            obs.redacted = False
            obs.anonymized = False
            if cat == "DATA_FLOW":
                obs.enabled = False
            if cat == "BACKUP_ARCHIVE":
                obs.archive_present = False
        elif result in ("FOUND", "PRESENT", "ACTIVE", "HIT"):
            obs.exists = True
            if cat == "DATA_FLOW":
                # "ACTIVE"/found connector means enabled unless stated otherwise
                en = rec.get("enabled")
                obs.enabled = _truthy(en) if en is not None else True
            if cat == "BACKUP_ARCHIVE":
                obs.archive_present = True
                ra = rec.get("restore_allowed")
                obs.restore_allowed = _truthy(ra) if ra is not None else None
        # connector_configurations result may be DISABLED/ENABLED
        elif result in ("DISABLED", "STOPPED", "BLOCKED"):
            obs.exists = True
            if cat == "DATA_FLOW":
                obs.enabled = False
            if cat == "BACKUP_ARCHIVE":
                obs.restore_allowed = False
        elif result in ("ENABLED", "DELIVERING"):
            obs.exists = True
            if cat == "DATA_FLOW":
                obs.enabled = True
        obs.extra.update({k: v for k, v in rec.items()})
        out.append(obs)
    return out


# --- analytics report / export jobs ------------------------------------------

def parse_analytics_report(data: Dict, evidence_id: str) -> List[Observation]:
    t = data.get("captured_at") or ""
    out: List[Observation] = []
    for rec in data.get("records") or []:
        obs = _obs_from_record("analytics_platform", "ANALYTICS_PROFILE", rec, t, evidence_id)
        if obs:
            out.append(obs)
    return out


def parse_export_jobs(data: Dict, evidence_id: str) -> List[Observation]:
    t = data.get("captured_at") or ""
    out: List[Observation] = []
    for rec in data.get("records") or []:
        merged = dict(rec)
        if rec.get("download_available") is False:
            merged["_absent"] = True
        obs = _obs_from_record("export_service", "USER_EXPORT", merged, t, evidence_id)
        if obs:
            if rec.get("download_available") is False:
                obs.exists = False
                obs.pii_exposed = False
            out.append(obs)
    return out


def parse_any_json(path_name: str, data, evidence_id: str) -> List[Observation]:
    if isinstance(data, dict) and "service_exports" in data:
        return parse_service_export(data, evidence_id)
    if isinstance(data, dict) and "snapshots" in data:
        return parse_snapshot_bundle(data, evidence_id)
    if isinstance(data, dict) and "records" in data:
        origin = (data.get("origin") or data.get("source") or path_name).lower()
        if "connector" in origin or "connector" in path_name:
            return parse_ledger(data, evidence_id, "connector_configurations")
        if "export" in origin or "export" in path_name:
            if "jobs" in path_name or data.get("collection") == "customer_export_jobs":
                return parse_export_jobs(data, evidence_id)
            return parse_ledger(data, evidence_id, "customer_export_manifest")
        if "security" in origin or "security" in path_name:
            return parse_ledger(data, evidence_id, "security_audit_ledger")
        if "analytics" in origin or "analytics" in path_name:
            return parse_analytics_report(data, evidence_id)
        return parse_ledger(data, evidence_id, "unknown")
    return []
