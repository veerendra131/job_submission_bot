"""SQLite tracker: every job ever seen, and whether/when it was applied to.

A job is never applied to twice. A posting counts as already applied if any
previous application matches it by:
  1. same board + same board job id,
  2. same normalized URL, or
  3. same company + job title + city within `duplicate_window_days`
     (catches reposts and the same role cross-posted on several boards).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import DATA_DIR
from .models import Job

# Statuses after which a job is never touched again.
FINAL_STATUSES = {"applied", "skipped", "filtered", "low_match", "duplicate"}
# Statuses that may be retried on a later run.
RETRY_STATUSES = {"new", "tailored", "needs_manual", "failed"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    url           TEXT,
    url_norm      TEXT,
    fingerprint   TEXT,
    title         TEXT,
    company       TEXT,
    location      TEXT,
    description   TEXT,
    posted        TEXT,
    employment_type TEXT,
    contact_email TEXT,
    status        TEXT NOT NULL DEFAULT 'new',
    reason        TEXT,
    match_score   INTEGER,
    resume_path   TEXT,
    applied_via   TEXT,
    applied_at    TEXT,
    first_seen    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS ix_jobs_url_norm ON jobs(url_norm);
CREATE INDEX IF NOT EXISTS ix_jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
"""

_COMPANY_NOISE = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|pvt|private|plc|group|"
    r"technologies|technology|solutions|services|consulting|systems|global|usa|us)\b\.?"
)
_TITLE_NOISE = re.compile(
    r"\b(c2c|corp\s*to\s*corp|w2|1099|only|contract|contractor|remote|hybrid|onsite|on\s*site|"
    r"local|urgent|immediate|need|needed|hiring|position|role|opening|long\s*term)\b"
)
_LOCATION_NOISE = re.compile(r"\b(remote|hybrid|onsite|on\s*site|in|or|and)\b")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/").lower()
    keep = ""
    q = parse_qs(parts.query)
    for key in ("jk", "currentJobId", "jobId"):  # ids some boards carry in the query
        if key in q:
            keep = f"?{key}={q[key][0]}"
            break
    return f"{host}{path}{keep}"


def _norm_words(text: str, noise: re.Pattern) -> str:
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", (text or "").lower())
    text = noise.sub(" ", text)
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    return " ".join(text.split())


def fingerprint(company: str, title: str, location: str = "") -> str:
    """Company + title + city. Survives reposts and cross-posting, but keeps a vendor's
    identical titles for different client cities apart."""
    c = _norm_words(company, _COMPANY_NOISE)
    t = _norm_words(re.split(r"\s[-|@–]\s|\|\||\s@", title or "")[0], _TITLE_NOISE)
    if not c or not t:
        return ""
    city = _norm_words((location or "").split(",")[0], _LOCATION_NOISE)
    if city in {"united states", "usa", "us", "anywhere"}:
        city = ""
    return hashlib.sha1(f"{c}|{t}|{city}".encode()).hexdigest()[:16]


class Tracker:
    def __init__(self, path: Path | None = None):
        path = path or DATA_DIR / "jobs.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- lookups -------------------------------------------------------
    def get(self, job: Job) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE source=? AND source_id=?", (job.source, job.source_id)
        ).fetchone()

    def get_by_id(self, row_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (row_id,)).fetchone()

    def find_prior_application(self, job: Job, window_days: int) -> sqlite3.Row | None:
        """Return the earlier *applied* row this job duplicates, if any."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE status='applied' AND source=? AND source_id=?",
            (job.source, job.source_id),
        ).fetchone()
        if row:
            return row
        un = normalize_url(job.url)
        if un:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE status='applied' AND url_norm=?", (un,)
            ).fetchone()
            if row:
                return row
        fp = fingerprint(job.company, job.title, job.location)
        if fp:
            since = (datetime.now() - timedelta(days=window_days)).isoformat(timespec="seconds")
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE status='applied' AND fingerprint=? AND applied_at>=? "
                "AND NOT (source=? AND source_id=?)",
                (fp, since, job.source, job.source_id),
            ).fetchone()
        return row

    def applied_today(self, source: str | None = None) -> int:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        sql, args = "SELECT COUNT(*) FROM jobs WHERE status='applied' AND applied_at>=?", [start]
        if source:
            sql += " AND source=?"
            args.append(source)
        return self.conn.execute(sql, args).fetchone()[0]

    def rows(self, status: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM jobs", []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(sql, args).fetchall()

    def counts(self) -> dict[str, int]:
        return {
            r[0]: r[1]
            for r in self.conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
        }

    # ---- writes --------------------------------------------------------
    def record_seen(self, job: Job) -> sqlite3.Row:
        """Insert the job if new (refreshing its details if not); return its row."""
        now = _now()
        self.conn.execute(
            """INSERT INTO jobs (source, source_id, url, url_norm, fingerprint, title, company,
                   location, description, posted, employment_type, contact_email, status,
                   first_seen, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?, ?)
               ON CONFLICT(source, source_id) DO UPDATE SET
                   description = CASE WHEN length(excluded.description) > length(coalesce(jobs.description,''))
                                      THEN excluded.description ELSE jobs.description END,
                   contact_email = coalesce(nullif(excluded.contact_email,''), jobs.contact_email)""",
            (
                job.source, job.source_id, job.url, normalize_url(job.url),
                fingerprint(job.company, job.title, job.location), job.title, job.company, job.location,
                job.description, job.posted, job.employment_type, job.contact_email, now, now,
            ),
        )
        self.conn.commit()
        return self.get(job)

    def set_status(self, row_id: int, status: str, **fields) -> None:
        fields["status"] = status
        fields["updated_at"] = _now()
        if status == "applied" and "applied_at" not in fields:
            fields["applied_at"] = fields["updated_at"]
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), row_id))
        self.conn.commit()

    def mark_applied_manually(self, url: str, title: str = "", company: str = "") -> int:
        """Record an application you made yourself so the bot never repeats it."""
        un = normalize_url(url)
        row = self.conn.execute("SELECT id FROM jobs WHERE url_norm=?", (un,)).fetchone()
        if row:
            self.set_status(row["id"], "applied", applied_via="manual")
            return row["id"]
        job = Job(source="manual", source_id=un or url, url=url, title=title, company=company)
        row = self.record_seen(job)
        self.set_status(row["id"], "applied", applied_via="manual")
        return row["id"]
