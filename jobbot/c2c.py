"""Cheap, local pre-filters that run before any Claude call."""
from __future__ import annotations

import re

from .models import Job

# A posting that says any of these is NOT open to C2C, whatever else it says.
_NEGATIVE = [
    r"\bno\s*(c2c|corp[\s-]*(to|2)[\s-]*corp|third[\s-]*part(y|ies)|3rd[\s-]*party)\b",
    r"\b(c2c|corp[\s-]*(to|2)[\s-]*corp|third[\s-]*party)\s*(is\s*)?(not\s*(allowed|accepted|considered|possible)|n/?a)\b",
    r"\bnot\s*(open\s*(to|for)|accepting)\s*(c2c|corp[\s-]*(to|2)[\s-]*corp|third[\s-]*part(y|ies))\b",
    r"@?\s*\bw-?2\s*only\b",
    r"\bonly\s*(on\s*)?w-?2\b",
    r"\bw-?2\s*(candidates|contract)?\s*only\b",
    r"\bfull[\s-]*time\s*(employees?\s*)?only\b",
    r"\bcannot\s*(work\s*with|accept)\s*(c2c|third[\s-]*part(y|ies))\b",
]
# Explicit signals that C2C / third-party is accepted.
_POSITIVE = [
    r"\bc2c\b",
    r"\bcorp[\s-]*(to|2)[\s-]*corp\b",
    r"\bthird[\s-]*party\b",
    r"\b3rd[\s-]*party\b",
    r"\bc2h\s*/\s*c2c\b",
]
# Weaker signals: only decisive when the posting never mentions C2C at all.
_WEAK_NEGATIVE = [
    r"\bw-?2\b",
    r"\bfull[\s-]*time\s*(position|role|employment|opportunity|employee)\b",
    r"\bdirect\s*hire\b",
]
_NEG_RE = [re.compile(p, re.I) for p in _NEGATIVE]
_WEAK_NEG_RE = [re.compile(p, re.I) for p in _WEAK_NEGATIVE]
_POS_RE = [re.compile(p, re.I) for p in _POSITIVE]
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IGNORED_EMAIL_DOMAINS = ("example.com", "dice.com", "linkedin.com", "indeed.com", "corptocorp.org")


def c2c_status(job: Job) -> tuple[str, str]:
    """Return ('allowed' | 'not_allowed' | 'unclear', evidence)."""
    text = " ".join([job.title, job.employment_type, job.description])
    for rx in _NEG_RE:
        m = rx.search(text)
        if m:
            return "not_allowed", m.group(0).strip()
    for rx in _POS_RE:
        m = rx.search(text)
        if m:
            return "allowed", m.group(0).strip()
    if job.source == "corptocorp":  # the whole board is C2C
        return "allowed", "corptocorp.org listing"
    for rx in _WEAK_NEG_RE:
        m = rx.search(text)
        if m:
            return "not_allowed", f"{m.group(0).strip()} (no C2C mention)"
    return "unclear", ""


def excluded_reason(job: Job, cfg: dict) -> str:
    """Return why the job should be skipped by keyword rules, or '' to keep it."""
    f = cfg["filters"]
    title = job.title.lower()
    for kw in f.get("exclude_title_keywords", []):
        if kw.lower() in title:
            return f"title contains '{kw}'"
    blob = f"{job.title}\n{job.description}".lower()
    for kw in f.get("exclude_keywords", []):
        if kw.lower() in blob:
            return f"posting mentions '{kw}'"
    return ""


def find_contact_email(text: str) -> str:
    for email in _EMAIL_RE.findall(text or ""):
        email = email.strip(".").lower()
        if not email.endswith(_IGNORED_EMAIL_DOMAINS):
            return email
    return ""
