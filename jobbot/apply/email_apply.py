"""Apply by email (C2C boards where the posting lists the recruiter's address)."""
from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

from ..config import OUTPUT_DIR
from ..models import Job


def build_email(job: Job, cfg: dict, resume_path: Path, note: str) -> EmailMessage:
    p = cfg.get("profile", {})
    name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    sender = os.getenv("SMTP_USER") or p.get("email", "")
    details = [
        ("Rate", f"${p['hourly_rate']}/hr on C2C" if p.get("hourly_rate") else ""),
        ("Work authorization", p.get("work_authorization", "")),
        ("Employer", p.get("employer_company", "")),
        ("Employer contact", p.get("employer_contact", "")),
        ("Location", ", ".join(x for x in [p.get("city"), p.get("state")] if x)),
        ("Availability", p.get("notice_period", "")),
    ]
    lines = ["Hi,", "", note or f"Please find my resume attached for the {job.title} role.", ""]
    filled = [f"  {k}: {v}" for k, v in details if v]
    if filled:
        lines += ["C2C details:", *filled, ""]
    lines += ["Thanks,", *[x for x in (name, p.get("phone"), p.get("email")) if x], "", f"Job: {job.url}"]
    msg = EmailMessage()
    msg["Subject"] = f"C2C Submission: {job.title} - {name}"
    msg["From"] = sender
    msg["To"] = job.contact_email
    msg.set_content("\n".join(lines))
    msg.add_attachment(
        resume_path.read_bytes(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=resume_path.name,
    )
    return msg


def save_draft(msg: EmailMessage, job: Job) -> Path:
    """Write a .eml that Outlook/Mail opens as an editable, unsent draft."""
    out = OUTPUT_DIR / "emails"
    out.mkdir(parents=True, exist_ok=True)
    msg["X-Unsent"] = "1"
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{job.company}_{job.title}")[:60]
    path = out / f"{safe}_{job.source_id[-12:]}.eml"
    path.write_bytes(bytes(msg))
    return path


def send(msg: EmailMessage) -> None:
    host = os.getenv("SMTP_HOST", "")
    user, pwd = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", "")
    if not (host and user and pwd):
        raise RuntimeError("SMTP_HOST / SMTP_USER / SMTP_PASSWORD are not set in .env")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=60) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
