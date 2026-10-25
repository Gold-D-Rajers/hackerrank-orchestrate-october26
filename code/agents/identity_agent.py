"""Identity agent: resolve a request email to the full IdentifierBundle."""
from __future__ import annotations
import csv
import re
from pathlib import Path
from typing import Dict, Optional

from schemas import IdentifierBundle

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def norm_email(e: str) -> str:
    return (e or "").strip().lower()


def norm_phone(p: str) -> str:
    return re.sub(r"\D", "", p or "")


class IdentityIndex:
    def __init__(self, dataset_dir: Path):
        self.by_email: Dict[str, str] = {}          # email -> user_id
        self.bundles: Dict[str, IdentifierBundle] = {}
        self._load(dataset_dir)

    def _bundle(self, user_id: str) -> IdentifierBundle:
        if user_id not in self.bundles:
            self.bundles[user_id] = IdentifierBundle(user_id=user_id)
        return self.bundles[user_id]

    def _load(self, dataset_dir: Path) -> None:
        with open(dataset_dir / "user_identities.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                uid = row["user_id"]
                b = self._bundle(uid)
                e = norm_email(row.get("email", ""))
                if e:
                    b.emails.add(e)
                    self.by_email.setdefault(e, uid)
                ph = norm_phone(row.get("phone", ""))
                if ph:
                    b.phones.add(ph)
                h = (row.get("email_hash") or "").strip().lower()
                if h:
                    b.hashes.add(h)
                name = (row.get("name") or "").strip()
                if name:
                    b.names.add(name)

        with open(dataset_dir / "user_audit_log.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                uid = row["user_id"]
                b = self._bundle(uid)
                b.audit_ids.add(row["audit_id"])
                field = (row.get("field_name") or "").lower()
                for val in (row.get("old_value"), row.get("new_value")):
                    if not val:
                        continue
                    if field == "email":
                        e = norm_email(val)
                        b.emails.add(e)
                        self.by_email.setdefault(e, uid)
                    elif field == "phone":
                        p = norm_phone(val)
                        if p:
                            b.phones.add(p)
                    elif field in ("email_hash", "hash"):
                        b.hashes.add(val.strip().lower())
                    elif field == "name":
                        b.names.add(val.strip())

    def resolve(self, email: str) -> Optional[IdentifierBundle]:
        return self.bundles.get(self.by_email.get(norm_email(email), ""), None)
