"""PDF evidence parser: deterministic text extraction via pypdf + regex mining."""
from __future__ import annotations
import base64
import re
import zlib
from typing import List, Optional

from schemas import Observation

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_USER_RE = re.compile(r"\buser_\d+\b")
_RID_RE = re.compile(r"\b((?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|marketing|object)_\w+)\b")
_PHONE_RE = re.compile(r"\+\d[\d\s()-]{6,}\d")


def _decode_streams(data: bytes) -> List[bytes]:
    """Decode every PDF stream we can (Flate, ASCII85, or both)."""
    out: List[bytes] = []
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = data[start:end].rstrip(b"\r\n")
        for attempt in ("flate", "a85+flate", "a85"):
            try:
                if attempt == "flate":
                    out.append(zlib.decompress(raw))
                elif attempt == "a85+flate":
                    out.append(zlib.decompress(base64.a85decode(raw.rstrip(), adobe=True)))
                else:
                    out.append(base64.a85decode(raw.rstrip(), adobe=True))
                break
            except Exception:
                continue
    return out


_STR_RE = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)


def _unescape_pdf_string(s: bytes) -> str:
    s = s[1:-1]
    out = []
    i = 0
    while i < len(s):
        c = s[i:i + 1]
        if c == b"\\" and i + 1 < len(s):
            nxt = s[i + 1:i + 2]
            mapping = {b"n": b"\n", b"r": b"\r", b"t": b"\t",
                       b"(": b"(", b")": b")", b"\\": b"\\"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            if nxt.isdigit():  # octal escape
                oct_digits = re.match(rb"[0-7]{1,3}", s[i + 1:i + 4])
                if oct_digits:
                    out.append(bytes([int(oct_digits.group(0), 8) & 0xFF]))
                    i += 1 + len(oct_digits.group(0))
                    continue
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return b"".join(out).decode("latin-1")


def _extract_text_fallback(path) -> str:
    """Minimal pure-Python text extraction (no third-party dependencies).

    Handles the shapes used by the supplied artefacts: Flate and/or ASCII85
    encoded page content streams drawing text with ``(..) Tj`` / ``[..] TJ``.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    lines: List[str] = []
    for content in _decode_streams(data):
        if b"BT" not in content or (b"Tj" not in content and b"TJ" not in content):
            continue
        for token in re.finditer(
                rb"\[(?:[^\]\\]|\\.)*\]\s*TJ|\((?:[^()\\]|\\.)*\)\s*Tj|T\*|Td|TD|ET", content):
            tok = token.group(0)
            if tok.endswith(b"TJ") or tok.endswith(b"Tj"):
                parts = [_unescape_pdf_string(s) for s in _STR_RE.findall(tok)]
                hl = re.findall(rb"<([0-9A-Fa-f\s]+)>", tok)
                for h in hl:
                    try:
                        parts.append(bytes.fromhex(
                            h.replace(b" ", b"").decode("ascii")).decode("latin-1"))
                    except ValueError:
                        pass
                if parts:
                    lines.append("".join(parts))
            elif tok in (b"T*", b"Td", b"TD", b"ET"):
                lines.append("\n")
    text = "".join(lines)
    # collapse the separator flood
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _extract_text(path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None  # type: ignore[assignment]
    if PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
            if text.strip():
                return text
        except Exception:
            pass
    return _extract_text_fallback(path)



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
