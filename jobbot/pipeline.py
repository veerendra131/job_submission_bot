"""search -> filter -> de-duplicate -> tailor resume -> apply, for one job at a time."""
from __future__ import annotations

import random
import time
from contextlib import nullcontext
from pathlib import Path

import anthropic
from rich.console import Console

from .apply import apply_job
from .browser import Browser
from .c2c import c2c_status, excluded_reason
from .db import FINAL_STATUSES, Tracker
from .models import Job
from .resume import Resume, load_master, render_docx
from .sources import get_source
from .tailor import Tailor, TailorError, TailorResult

console = Console()


class LimitReached(Exception):
    pass


class Pipeline:
    def __init__(self, cfg: dict, mode: str, max_applications: int | None = None):
        self.cfg = cfg
        self.mode = mode
        self.tracker = Tracker()
        self.master: Resume = load_master()
        self.tailor = Tailor(cfg)
        self.browser: Browser | None = None
        self.max_applications = max_applications
        self.applied_this_run = 0
        self.stats: dict[str, int] = {}

    # ---- entry points ---------------------------------------------------
    def run_search(self, sources: list[str]) -> None:
        s = self.cfg["search"]
        needs_browser = self.mode != "dry-run" or "indeed" in sources
        with (Browser(self.cfg) if needs_browser else nullcontext()) as browser:
            self.browser = browser
            try:
                for name in sources:
                    src = get_source(name)
                    for query in s["queries"]:
                        q = f"{query} {s.get('c2c_query_suffix') or ''}".strip()
                        for loc in s["locations"]:
                            console.rule(f"[bold]{name}[/] · {q} · {loc}")
                            try:
                                for job in src.search(q, loc, self.cfg, s["max_results_per_query"],
                                                      browser=browser, skip=self._already_final):
                                    self.process(job)
                            except LimitReached:
                                raise
                            except Exception as e:
                                console.print(f"[red]  {name} search error: {type(e).__name__}: {e}[/]")
            except LimitReached as e:
                console.print(f"[yellow]{e}[/]")
        self._summary()

    def run_pending(self, statuses: list[str]) -> None:
        rows = [r for st in statuses for r in self.tracker.rows(st, limit=500)]
        console.print(f"{len(rows)} job(s) with status {', '.join(statuses)}")
        with (Browser(self.cfg) if self.mode != "dry-run" else nullcontext()) as browser:
            self.browser = browser
            try:
                for r in rows:
                    self.process(_job_from_row(r))
            except LimitReached as e:
                console.print(f"[yellow]{e}[/]")
        self._summary()

    # ---- per job ----------------------------------------------------------
    def _already_final(self, source: str, source_id: str) -> bool:
        row = self.tracker.conn.execute(
            "SELECT status FROM jobs WHERE source=? AND source_id=?", (source, source_id)
        ).fetchone()
        return bool(row and row["status"] in FINAL_STATUSES)

    def _count(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    def process(self, job: Job) -> None:
        f = self.cfg["filters"]
        row = self.tracker.record_seen(job)
        rid = row["id"]
        if row["status"] in FINAL_STATUSES:
            return

        prior = self.tracker.find_prior_application(job, f["duplicate_window_days"])
        if prior is not None:
            self.tracker.set_status(rid, "duplicate",
                                    reason=f"already applied: {prior['source']} #{prior['id']} on {prior['applied_at'][:10]}")
            console.print(f"  [dim]dup      {job.label} (applied via {prior['source']} {prior['applied_at'][:10]})[/]")
            return self._count("duplicate")

        reason = excluded_reason(job, self.cfg)
        c2c, evidence = c2c_status(job)
        if not reason and c2c == "not_allowed":
            reason = f"not C2C: '{evidence}'"
        if not reason and c2c == "unclear" and not f.get("include_unclear_c2c"):
            reason = "posting never mentions C2C"
        if not reason and len(job.description) < 150:
            reason = "no job description"
        if reason:
            self.tracker.set_status(rid, "filtered", reason=reason)
            console.print(f"  [dim]skip     {job.label} - {reason}[/]")
            return self._count("filtered")

        # Tailor (reuse an earlier result for this job so re-runs cost nothing).
        try:
            result, resume_path = self._tailored(job, row)
        except (TailorError, anthropic.APIError) as e:
            self.tracker.set_status(rid, "failed", reason=f"tailoring failed: {e}")
            console.print(f"  [red]error    {job.label} - tailoring failed: {e}[/]")
            if isinstance(e, anthropic.AuthenticationError):
                raise SystemExit("ANTHROPIC_API_KEY is missing or invalid (.env)")
            return self._count("failed")

        if result.c2c_assessment == "not_allowed":
            self.tracker.set_status(rid, "filtered", reason="Claude: posting does not accept C2C",
                                    match_score=result.match_score)
            console.print(f"  [dim]skip     {job.label} - not C2C (Claude)[/]")
            return self._count("filtered")
        if result.blockers or result.match_score < f["min_match_score"]:
            why = "; ".join(result.blockers) or f"score {result.match_score} < {f['min_match_score']}"
            self.tracker.set_status(rid, "low_match", reason=why, match_score=result.match_score,
                                    resume_path=str(resume_path))
            console.print(f"  [yellow]low      {job.label} - {why}[/]")
            return self._count("low_match")

        self.tracker.set_status(rid, "tailored", match_score=result.match_score,
                                resume_path=str(resume_path), reason="")
        console.print(f"  [green]match {result.match_score:>3}[/] {job.label}\n           resume: {resume_path.name}")
        if result.guard_notes:
            console.print(f"           [dim]guard: {'; '.join(result.guard_notes)}[/]")
        if self.mode == "dry-run":
            return self._count("tailored")

        if not self._check_limits(job.source):
            console.print(f"           [dim]{job.source} daily limit reached - kept for a later run[/]")
            return self._count("deferred")
        outcome = apply_job(job, resume_path, result.recruiter_note, self.cfg, self.mode, self.browser)
        if outcome.status == "deferred":
            console.print(f"           [dim]{outcome.message}[/]")
            return self._count("deferred")
        fields = {"reason": outcome.message}
        if outcome.via:
            fields["applied_via"] = outcome.via
        self.tracker.set_status(rid, outcome.status, **fields)
        color = {"applied": "green", "skipped": "dim"}.get(outcome.status, "yellow")
        console.print(f"           [{color}]{outcome.status}[/] {outcome.message}")
        self._count(outcome.status)
        if outcome.status == "applied":
            self.applied_this_run += 1
            lo, hi = self.cfg["apply"].get("delay_seconds", [25, 70])
            time.sleep(random.uniform(lo, hi))

    def _tailored(self, job: Job, row) -> tuple[TailorResult, Path]:
        if row["resume_path"]:
            meta = Path(row["resume_path"]).with_suffix(".json")
            if meta.exists() and Path(row["resume_path"]).exists():
                return TailorResult.model_validate_json(meta.read_text(encoding="utf-8")), Path(row["resume_path"])
        result = self.tailor.tailor(self.master, job)
        path = render_docx(result.resume, job.company, job.title, f"{job.source}{row['id']}")
        path.with_suffix(".json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result, path

    def _check_limits(self, source: str) -> bool:
        """Raise when the whole run must stop; return False when only this portal is capped."""
        a = self.cfg["apply"]
        if self.max_applications is not None and self.applied_this_run >= self.max_applications:
            raise LimitReached(f"Reached --max {self.max_applications} applications for this run.")
        if self.tracker.applied_today() >= a.get("daily_limit", 30):
            raise LimitReached(f"Daily limit of {a['daily_limit']} applications reached.")
        per = a.get("per_portal_daily_limit", {}).get(source)
        return per is None or self.tracker.applied_today(source) < per

    def _summary(self) -> None:
        if self.stats:
            console.rule("run summary")
            console.print("  " + "   ".join(f"{k}: {v}" for k, v in sorted(self.stats.items())))


def _job_from_row(r) -> Job:
    return Job(
        source=r["source"], source_id=r["source_id"], url=r["url"], title=r["title"] or "",
        company=r["company"] or "", location=r["location"] or "", description=r["description"] or "",
        posted=r["posted"] or "", contact_email=r["contact_email"] or "",
        employment_type=r["employment_type"] or "",
    )
