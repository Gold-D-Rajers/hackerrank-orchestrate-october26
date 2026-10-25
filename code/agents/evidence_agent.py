"""Evidence agent: parse every evidence file ONCE into a global observation index."""
from __future__ import annotations
import csv
import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

from schemas import Observation
from parsers.sql_parser import parse_sql
from parsers.json_parser import parse_any_json
from parsers.log_parser import parse_log
from parsers.text_parser import parse_handover
from parsers.pdf_parser import parse_pdf
from parsers.image_parser import parse_image

_LINK_RE = re.compile(
    r"linked\s+(\w+)\s+\([^)]*\)\s+to\s+([^@\s\"]+@[^@\s\"]+\.[^@\s\"]+)\s+and\s+(user_\d+)",
    re.I,
)
_LINK_RE2 = re.compile(
    r"belongs\s+only\s+to\s+([^@\s\"]+@[^@\s\"]+\.[^@\s\"]+)\s+and\s+(user_\d+)",
    re.I,
)


class EvidenceIndex:
    def __init__(self, dataset_dir: Path):
        self.dataset_dir = dataset_dir
        self.observations: List[Observation] = []
        self.manifest: Dict[str, Dict] = {}
        # (system, resource) -> observations (unsorted)
        self.by_resource: Dict[Tuple[str, str], List[Observation]] = {}
        # extra resource->user links from system_activity (token -> resource keys)
        self.activity_links: List[Tuple[str, str, str, str, str]] = []  # (token, system, rid, ts, activity_id)
        self.activity_flow_status: List[Observation] = []  # CHECK_DATA_FLOW etc.
        # evidence_id -> observations (for citation lookups)
        self.by_file: Dict[str, List[Observation]] = {}
        # (system, resource) -> last workflow treatment ("DELETE"/"ANONYMIZE"/
        # "REDACT") as submitted in system_activity record events
        self.workflow_action: Dict[Tuple[str, str], Tuple[str, str]] = {}
        # resource_id -> [activity rows] (the operational history of a record)
        self.activities: Dict[str, List[Dict]] = {}
        self.build()

    # ------------------------------------------------------------------ build
    def build(self) -> None:
        with open(self.dataset_dir / "evidence_files.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.manifest[row["file_id"]] = row
                self._parse_file(row)

        self._load_activity()

        for obs in self.observations:
            # generator artifacts: the parsers also emit synthetic
            # doc_evidence_N / img_evidence_N records for PDFs/images; gold
            # answers never reference them, so they must not become resources.
            if re.fullmatch(r"(?:doc|img)_evidence_\d+", obs.resource_id):
                continue
            fid = obs.evidence_id
            if fid in self.manifest:
                obs.captured_at = self.manifest[fid].get("captured_at") or ""
            if not obs.captured_at:
                obs.captured_at = obs.observed_at or ""
            self.by_resource.setdefault(obs.key(), []).append(obs)
            self.by_file.setdefault(fid, []).append(obs)

    def _parse_file(self, row: Dict) -> None:
        fid = row["file_id"]
        rel = row["file_path"]
        captured = row["captured_at"]
        path = self.dataset_dir / rel
        if not path.exists():
            return
        suffix = path.suffix.lower()
        try:
            if suffix == ".sql":
                text = path.read_text(encoding="utf-8", errors="replace")
                self.observations.extend(parse_sql(text, captured, fid))
            elif suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                self.observations.extend(parse_any_json(path.name, data, fid))
            elif suffix == ".log":
                text = path.read_text(encoding="utf-8", errors="replace")
                self.observations.extend(parse_log(path.name, text, fid))
            elif suffix == ".txt":
                text = path.read_text(encoding="utf-8", errors="replace")
                self.observations.extend(parse_handover(text, fid))
            elif suffix == ".pdf":
                self.observations.extend(parse_pdf(path, fid, captured))
            elif suffix in (".png", ".jpg", ".jpeg"):
                self.observations.extend(parse_image(path, fid, captured))
        except Exception as e:  # keep the pipeline alive; log to stderr
            import sys
            print(f"[evidence] parse failed {fid} ({rel}): {e}", file=sys.stderr)

    def _load_activity(self) -> None:
        with open(self.dataset_dir / "system_activity.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                et = row["event_type"]
                ts = row["event_time"]
                rid = row["resource_id"]
                sys_t = row["target_system"] or row["source_system"]
                aid = row["activity_id"]
                details = row.get("details") or ""
                self.activities.setdefault(rid, []).append({
                    "activity_id": aid,
                    "event_time": ts,
                    "event_type": et,
                    "reported_result": row.get("reported_result") or "",
                })

                if et in ("DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD"):
                    act = et.split("_", 1)[0]
                    k = (sys_t, rid)
                    prev = self.workflow_action.get(k)
                    # keep the latest event by time
                    if prev is None or ts > prev[1]:
                        self.workflow_action[k] = (act, ts)

                if et == "LINK_RESOURCE_IDENTITY":
                    m = _LINK_RE.search(details) or _LINK_RE2.search(details)
                    if m:
                        if m.lastindex and m.lastindex >= 3:
                            email, uid = m.group(2), m.group(3)
                        else:
                            email, uid = m.group(1), m.group(2)
                        self.activity_links.append((email.strip().lower(), sys_t, rid, ts, aid))
                        self.activity_links.append((uid.strip(), sys_t, rid, ts, aid))
                    continue

                # any details containing an email -> weak link token
                for em in re.findall(r"[^@\s\"]+@[^@\s\"]+\.[^@\s\"]+", details):
                    self.activity_links.append((em.strip().lower(), sys_t, rid, ts, aid))

                if et in ("CHECK_DATA_FLOW", "DISABLE_DATA_FLOW"):
                    enabled = "remains enabled" in details or "still enabled" in details
                    disabled = ("disabled" in details and "remains" not in details) or et == "DISABLE_DATA_FLOW"
                    obs = Observation(
                        system_id="integration_service",
                        resource_id=rid,
                        data_category="DATA_FLOW",
                        observed_at=ts,
                        evidence_id=aid,
                        enabled=(True if enabled else (False if disabled else None)),
                        exists=True,
                    )
                    self.activity_flow_status.append(obs)
                    self.observations.append(obs)
                elif et in ("CHECK_RESTORE_ACCESS", "BLOCK_RESTORE", "RESTORE_BACKUP"):
                    risk = ("eligible" in details.lower() or "allowed" in details.lower()
                            or et == "RESTORE_BACKUP")
                    blocked = et == "BLOCK_RESTORE" or "blocked" in details.lower()
                    obs = Observation(
                        system_id="backup_service",
                        resource_id=rid,
                        data_category="BACKUP_ARCHIVE",
                        observed_at=ts,
                        evidence_id=aid,
                        archive_present=True,
                        restore_allowed=(False if blocked else (True if risk else None)),
                        exists=True,
                    )
                    self.activity_flow_status.append(obs)
                    self.observations.append(obs)

    # ------------------------------------------------------------- citations
    def activities_for(self, resource_id: str, audit_ts: str) -> List[Dict]:
        """Activity rows for one record at/before the audit instant."""
        rows = [a for a in self.activities.get(resource_id, ())
                if a["event_time"] and a["event_time"] <= audit_ts]
        rows.sort(key=lambda a: (a["event_time"], a["activity_id"]))
        return rows

    # ------------------------------------------------------------- subject link
    def resources_for_tokens(self, tokens: Set[str], audit_ts: str) -> Set[Tuple[str, str]]:
        """Fixpoint: expand tokens across resources that match them.

        Only observations at/before audit_ts participate in state, but tokens
        observed any time on a resource that ALSO has a <= audit_ts
        observation may be used for linkage (identity, not state).
        """
        # pre-audit observation universe for state, all-obs for token expansion
        keys: Set[Tuple[str, str]] = set()
        cur_tokens = set(tokens)

        # seed from direct token match (any time, for linkage)
        changed = True
        rounds = 0
        while changed and rounds < 8:
            changed = False
            rounds += 1
            for obs in self.observations:
                if obs.key() in keys:
                    continue
                if obs.link_tokens & cur_tokens:
                    keys.add(obs.key())
                    new = obs.link_tokens - cur_tokens
                    if new:
                        cur_tokens |= new
                        changed = True

        # activity-based links (email/user -> resource)
        for token, system_id, rid, ts, aid in self.activity_links:
            if token in cur_tokens:
                k = (system_id, rid)
                if k not in keys:
                    keys.add(k)
                    changed = True
                    # pull in any observation tokens for this resource
                    for obs in self.by_resource.get(k, []):
                        new = obs.link_tokens - cur_tokens
                        if new:
                            cur_tokens |= new
        # re-close after activity expansion
        if changed:
            for obs in self.observations:
                if obs.key() in keys:
                    continue
                if obs.link_tokens & cur_tokens:
                    keys.add(obs.key())
                    cur_tokens |= obs.link_tokens

        return keys
