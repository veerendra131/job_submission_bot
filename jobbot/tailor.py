"""Claude-powered resume import and per-job tailoring.

Tailoring may only reword, reorder, and re-emphasize what is already in the master
resume. Employers, titles, dates, education and certifications are copied from the
master verbatim, and anything Claude returns is checked before it is used:
  * skills not present anywhere in the master resume are dropped;
  * a rewritten bullet list containing a number (%, $, years, counts) that does not
    appear in that job's original bullets is reverted to the original;
  * a summary/headline with numbers not in the master resume is reverted.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import anthropic
import yaml
from pydantic import BaseModel

from .models import Job
from .resume import Resume, SkillGroup, extract_text, resume_text

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class TailorError(RuntimeError):
    pass


class ExperienceRewrite(BaseModel):
    index: int
    bullets: list[str]


class TailorOutput(BaseModel):
    match_score: int
    c2c_assessment: Literal["allowed", "not_allowed", "unclear"]
    blockers: list[str]
    headline: str
    summary: str
    skills: list[SkillGroup]
    experience: list[ExperienceRewrite]
    changes: list[str]
    recruiter_note: str


class TailorResult(BaseModel):
    resume: Resume
    match_score: int
    c2c_assessment: str
    blockers: list[str]
    changes: list[str]
    guard_notes: list[str]
    recruiter_note: str


IMPORT_PROMPT = """Convert this resume into the structured format.
Copy content faithfully: keep every job, bullet, skill, degree and certification, and do not
invent or embellish anything. Group skills into sensible categories. Use "" for anything the
resume does not state. Order experience newest first.

<resume>
{text}
</resume>"""

TAILOR_SYSTEM = """You tailor a contractor's resume to a specific job posting for Corp-to-Corp (C2C) \
IT contract roles in the US.

Your output is used directly in a real job application, so it must stay truthful. You may:
- rewrite the headline and summary to lead with the experience this job cares about;
- reorder skill groups and the items inside them, rename groups, and drop irrelevant skills;
- for each job in `experience`, reorder bullets so the most relevant come first, reword them
  to use the posting's terminology where it describes the same thing, and trim weak or
  irrelevant bullets (keep at least 3 per job when the original has 3 or more).

You must not add skills, tools, employers, responsibilities, metrics, numbers, domains or
certifications that the master resume does not support. If the posting asks for something
the candidate lacks, leave it out and list it in `blockers` only when it is a hard requirement.

Also judge the posting:
- match_score: 0-100, how well the candidate's actual experience fits the must-have requirements.
- c2c_assessment: "allowed" if the posting accepts C2C / corp-to-corp / third-party candidates,
  "not_allowed" if it says W2 only, full-time only, or no C2C/third party, otherwise "unclear".
- blockers: hard requirements the candidate clearly does not meet, e.g. security clearance,
  citizenship-only, must be local to a city the candidate is not in, a required certification
  they do not hold, far more years than they have. Empty if none.
- experience: one entry per job in the master resume, using that job's `index` (0-based).
- changes: short list of what you changed and why.
- recruiter_note: a 3-5 sentence plain-text note to the recruiter for a C2C submission:
  role name, the 2-3 most relevant strengths, and availability. No placeholders.

<master_resume>
{resume_yaml}
</master_resume>"""

_NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s*(?:%|\+|k|m|x)?", re.I)


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m.group(0).lower()) for m in _NUM_RE.finditer(text)}


class Tailor:
    def __init__(self, cfg: dict):
        c = cfg["claude"]
        self.model = c.get("model", "claude-opus-5-5")
        self.effort = c.get("effort", "medium")
        self.client = anthropic.Anthropic(max_retries=4)

    def _call(self, system, user: str, output_format, max_tokens: int = 16000):
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user}],
            output_format=output_format,
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if system:
            kwargs["system"] = system
        resp = self.client.beta.messages.parse(**kwargs)
        if resp.stop_reason == "refusal":
            raise TailorError("Claude declined this request")
        if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
            raise TailorError(f"No usable output (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    # ---- one-time import ------------------------------------------------
    def import_resume(self, path: Path) -> Resume:
        text = extract_text(path)
        if len(text.strip()) < 200:
            raise TailorError(
                f"Could only read {len(text.strip())} characters from {path.name}. "
                "If it is a scanned PDF, export it as .docx or text first."
            )
        return self._call(None, IMPORT_PROMPT.format(text=text), Resume)

    # ---- per job --------------------------------------------------------
    def tailor(self, master: Resume, job: Job) -> TailorResult:
        master_yaml = yaml.safe_dump(
            {**master.model_dump(), "experience": [
                {"index": i, **e.model_dump()} for i, e in enumerate(master.experience)
            ]},
            sort_keys=False, allow_unicode=True, width=100,
        )
        # Stable system prompt (instructions + master resume) is cached across jobs.
        system = [{
            "type": "text",
            "text": TAILOR_SYSTEM.format(resume_yaml=master_yaml),
            "cache_control": {"type": "ephemeral"},
        }]
        user = (
            f"<job_posting>\nTitle: {job.title}\nCompany: {job.company}\n"
            f"Location: {job.location}\nEmployment type: {job.employment_type}\n\n"
            f"{job.description[:30000]}\n</job_posting>"
        )
        out: TailorOutput = self._call(system, user, TailorOutput)
        return self._apply_guarded(master, out)

    @staticmethod
    def _apply_guarded(master: Resume, out: TailorOutput) -> TailorResult:
        notes: list[str] = []
        master_text = resume_text(master)
        master_lower = master_text.lower()
        master_nums = _numbers(master_text)

        # Skills: keep only items that exist somewhere in the master resume.
        skills: list[SkillGroup] = []
        for g in out.skills:
            kept = [s for s in g.items if s.strip() and s.strip().lower() in master_lower]
            dropped = [s for s in g.items if s not in kept]
            if dropped:
                notes.append(f"dropped unsupported skills: {', '.join(dropped)}")
            if kept:
                skills.append(SkillGroup(category=g.category, items=kept))
        if not skills:
            skills = master.skills

        # Experience: structure is fixed from the master; only bullets change.
        experience = [e.model_copy(deep=True) for e in master.experience]
        for rw in out.experience:
            if not (0 <= rw.index < len(experience)):
                continue
            orig = master.experience[rw.index]
            bullets = [b.strip() for b in rw.bullets if b.strip()]
            if not bullets:
                continue
            new_nums = _numbers(" ".join(bullets)) - _numbers(" ".join(orig.bullets))
            if new_nums:
                notes.append(
                    f"kept original bullets for {orig.company}: rewrite introduced "
                    f"numbers {sorted(new_nums)}"
                )
                continue
            experience[rw.index].bullets = bullets

        summary, headline = out.summary.strip(), out.headline.strip()
        if _numbers(summary) - master_nums or not summary:
            if summary:
                notes.append("kept original summary: rewrite introduced new numbers")
            summary = master.summary
        if _numbers(headline) - master_nums or not headline:
            headline = master.headline

        tailored = master.model_copy(deep=True, update={
            "headline": headline, "summary": summary,
            "skills": skills, "experience": experience,
        })
        return TailorResult(
            resume=tailored,
            match_score=max(0, min(100, out.match_score)),
            c2c_assessment=out.c2c_assessment,
            blockers=out.blockers,
            changes=out.changes,
            guard_notes=notes,
            recruiter_note=out.recruiter_note.strip(),
        )
