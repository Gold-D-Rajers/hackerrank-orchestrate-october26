"""Intake agent: does request_text authorise deletion?

Deterministic multilingual classifier with an optional LLM hook.
reported_status is never used as evidence of authorisation.
"""
from __future__ import annotations
import os
import re
from typing import Tuple

from schemas import Case

# Explicit non-deletion / opt-out phrasings (English + common equivalents).
_NEGATION = re.compile(
    r"(do\s+not\s+(?:delete|close|remove|erase)"
    r"|don'?t\s+(?:delete|close|remove|erase)"
    r"|not\s+asking\s+for\s+deletion"
    r"|i\s+am\s+not\s+requesting\s+deletion"
    r"|leave\s+my\s+(?:account|profile|data)"
    r"|keep\s+(?:my|the)\s+account"
    r"|without\s+removing"
    r"|sin\s+eliminar"
    r"|ne\s+pas\s+supprimer"
    r"|nicht\s+l(?:ö|oe)schen"
    r"|nao\s+excluir|não\s+excluir"
    r"|삭제\s*하지\s*마세요"
    r"|削除\s*しないで)",
    re.I,
)

# Positive deletion intent, per language family.
_DELETION = re.compile(
    r"("
    # English
    r"\bdelet(?:e|es|ed|ing|ion)s?\b"
    r"|\beras(?:e|es|ed|ing)\b|\berasure\b"
    r"|\bremov(?:e|es|ed|ing)\b(?:\s+\w+){0,2}\s+"
    r"(?:my|the|this|all|any|old)?\s*"
    r"(?:account|data|profile|records?|copies|personal|name|details|contact|information|everything)"
    r"|\bno\s+longer\s+need\s+(?:this|my)\s+account\b"
    r"|\bclos(?:e|ing|ed)\s+(?:my|the|this)\s+account\b"
    r"|\bpermanent(?:ly)?\s+(?:delete|deletion|remove|erase)"
    r"|\bright\s+to\s+be\s+forgotten\b"
    r"|\bclean\s+(?:up|out)\b.*\b(?:data|profile|details)"
    # Spanish
    r"|\beliminaci(?:ó|o)n\b|\bsupresi(?:ó|o)n\b|\beliminar\b|\bborrar\b|\bsuprimir\b"
    r"|\bcierre\s+(?:definitivo\s+)?d(?:e|el)\s+cuenta|\bcerrar\s+mi\s+cuenta"
    # French
    r"|\bsuppression\b|\bsupprimer\b|\beffacer\b|\beffacement\b"
    r"|\bcl(?:ô|o)ture\s+d(?:u|efinitive\s+du)\s+compte|\bfermer\s+mon\s+compte"
    # Portuguese
    r"|\bexclus(?:ã|a)o\b|\bexcluir\b|\beliminar\b|\bapagar\b"
    # German
    r"|\bl(?:ö|oe)schung\b|\bl(?:ö|oe)schen\b|\bentfernen\b|\bdatenl(?:ö|oe)schung"
    # Japanese
    r"|削除|退会|抹消"
    # Korean
    r"|삭제|계정\s*폐쇄"
    # misc
    r"|\bunlink\b|\bpurge\b|\bwipe\b"
    r"|\balready\s+been\s+(?:removed|deleted)\b"
    r"|\bis\s+gone\b)",
    re.I,
)

# Audit / verification phrasing: the person asks whether a (prior) deletion
# actually holds. These are authorised deletion-audit requests, not access or
# export requests.
_AUDIT_QUESTION = re.compile(
    r"(?:inspect|check|verify|confirm|tell\s+me\s+whether|find\s+out)"
    r"[\s\S]{0,160}"
    r"(?:state|gone|deleted|removed|left\s+behind|identifi|no\s+longer|"
    r"remaining\s+records|already)",
    re.I,
)

# Access / export / correction / support / marketing-only signals that must NOT
# be classified as deletion even if the word "delete" appears somewhere.
_NON_DELETION = re.compile(
    r"(what\s+data\s+do\s+you\s+hold"
    r"|download\s+my\s+(?:data|account|information)"
    r"|copy\s+of\s+(?:the\s+)?(?:information|data|invoices?|records?)"
    r"|portable\s+format"
    r"|reset(?:ting)?\s+the\s+login"
    r"|can'?t\s+access\s+my\s+profile"
    r"|out\s+of\s+date"
    r"|replace\s+it\s+with"
    r"|misspelled"
    r"|correct\s+it"
    r"|please\s+change\s+the\s+email"
    r"|stop\s+the\s+promotional\s+emails"
    r"|opt[\s-]?out\s+of\s+(?:marketing|promotions|emails)"
    r"|send\s+me\s+a\s+copy"
    r"|\bcorrigir\b|\bcorrection\b|\bupdate\s+my\s+(?:phone|email|address)\b"
    r"|access\s+my\s+(?:data|account)\b"
    r"|export\s+my\s+data\b)",
    re.I,
)


def classify_request(text: str) -> Tuple[bool, str, str]:
    """Return (authorised, language_guess, reason)."""
    t = text or ""
    if _NEGATION.search(t) and not re.search(
        r"(delete|deletion|erase|erasure|eliminar|suppression|exclus|削除|삭제)", t, re.I
    ):
        return False, "unknown", "explicit non-deletion phrasing"

    neg = bool(_NEGATION.search(t))
    pos = bool(_DELETION.search(t))
    non = bool(_NON_DELETION.search(t))
    audit = bool(_AUDIT_QUESTION.search(t))

    if pos and not neg:
        return True, "unknown", "deletion intent"
    if pos and neg:
        # e.g. "do not delete anything else, but delete my account"
        # Prefer explicit do-not-delete statements only when they dominate.
        if re.search(r"(do\s+not\s+delete|don'?t\s+delete|삭제\s*하지\s*마세요|削除\s*しないで)", t, re.I):
            return False, "unknown", "negated deletion"
        return True, "unknown", "deletion intent despite negation words"
    if audit and not neg and not non:
        return True, "unknown", "deletion-state audit question"
    if non and not pos:
        return False, "unknown", "non-deletion request type"
    return False, "unknown", "no deletion intent"


def optional_llm_intake(text: str) -> Tuple[bool, str, str] | None:
    """Optional LLM hook (temperature=0). Returns None when no provider is set."""
    if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from .llm_client import complete_json  # lazy import
        from pathlib import Path
        prompt = (Path(__file__).parent / "prompts" / "intake.md").read_text()
        prompt = prompt.replace("{{request_text}}", text)
        out = complete_json(prompt)
        return bool(out.get("authorised")), str(out.get("language", "unknown")), str(out.get("reason", ""))
    except Exception:
        return None


def authorise(case: Case) -> bool:
    det = classify_request(case.request_text)
    llm = optional_llm_intake(case.request_text)
    if llm is not None:
        return bool(llm[0])
    return bool(det[0])
