"""Submits one application: portal wizard (Dice / LinkedIn / Indeed) or recruiter email.

Modes
  review : the bot fills everything it can and stops at the Submit button; you finish
           in the browser and confirm in the terminal.
  auto   : on portals listed in apply.auto_submit_portals the bot clicks Submit itself.
           Other portals are left as 'tailored' for your next review session.
Captchas and login pages are never bypassed: in review mode the bot waits for you,
in auto mode the job is marked needs_manual.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PWTimeout

from ..browser import Browser, has_captcha, reject_cookies, wait_for_human
from ..models import Job
from . import email_apply
from .forms import FormFiller
from .portals import PORTALS, PortalSpec


@dataclass
class Outcome:
    status: str        # applied | needs_manual | failed | skipped | deferred
    message: str = ""
    via: str = ""


def ask_user(job: Job, message: str) -> Outcome:
    print(f"\n  >> {job.label}\n     {message}")
    while True:
        a = input("     Did you submit it?  [a] applied  [s] skip job  [l] later : ").strip().lower()[:1]
        if a == "a":
            return Outcome("applied", "confirmed by you", via=f"{job.source}-review")
        if a == "s":
            return Outcome("skipped", "skipped by you")
        if a == "l":
            return Outcome("needs_manual", "left for later")


def apply_job(job: Job, resume_path: Path, note: str, cfg: dict, mode: str,
              browser: Browser | None) -> Outcome:
    auto = mode == "auto" and job.source in cfg["apply"].get("auto_submit_portals", [])
    interactive = mode == "review"
    email_cfg = cfg["apply"].get("email", {})

    if job.source not in PORTALS:
        if job.contact_email and email_cfg.get("enabled", True):
            return _email(job, resume_path, note, cfg, mode)
        if interactive and browser:
            browser.page().goto(job.url)
            return ask_user(job, "No automated apply for this board - apply in the browser.")
        return Outcome("needs_manual", "no automated apply for this board and no recruiter email")

    if not auto and not interactive:
        return Outcome("deferred", f"{job.source} is review-only; run `apply --mode review`")

    filler = FormFiller(cfg, resume_path)
    result = _portal_apply(browser.page(), job, PORTALS[job.source], filler, auto, interactive)

    # Unattended runs: if the portal flow can't finish but the recruiter left an email, use it.
    if (not interactive and result.status in ("needs_manual", "failed") and job.contact_email
            and email_cfg.get("enabled", True) and email_cfg.get("fallback_for_portals", True)):
        fallback = _email(job, resume_path, note, cfg, mode)
        fallback.message = f"{result.message}; {fallback.message}"
        return fallback
    return result


# ---------------------------------------------------------------------------
def _email(job: Job, resume_path: Path, note: str, cfg: dict, mode: str) -> Outcome:
    msg = email_apply.build_email(job, cfg, resume_path, note)
    if mode == "auto" and cfg["apply"]["email"].get("send"):
        try:
            email_apply.send(msg)
            return Outcome("applied", f"emailed {job.contact_email}", via="email")
        except Exception as e:
            return Outcome("failed", f"email send failed: {e}")
    draft = email_apply.save_draft(msg, job)
    if mode == "review":
        try:
            os.startfile(draft)  # opens in Outlook / Mail as an unsent draft (Windows)
        except (AttributeError, OSError):
            pass
        out = ask_user(job, f"Email draft to {job.contact_email} opened: {draft}")
        if out.status == "applied":
            out.via = "email"
        return out
    return Outcome("needs_manual", f"email draft saved: {draft}")


def _clickable(scope: Locator | Page, rx) -> Locator | None:
    for role in ("button", "link"):
        loc = scope.get_by_role(role, name=rx)
        try:
            for i in range(min(loc.count(), 8)):
                el = loc.nth(i)
                if el.is_visible() and el.is_enabled():
                    return el
        except Exception:
            continue
    return None


def _text(loc: Locator) -> str:
    try:
        return loc.inner_text(timeout=5000)
    except Exception:
        return ""


def _guard(page: Page, spec: PortalSpec, job: Job, interactive: bool) -> Outcome | None:
    """Handle login walls and captchas. Returns an Outcome if we must stop."""
    if spec.login_wall.search(page.url):
        if not interactive:
            return Outcome("needs_manual", f"not logged in to {spec.name} (run: python -m jobbot login)")
        wait_for_human(page, f"Please log in to {spec.name}.")
        page.goto(job.url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    if has_captcha(page):
        if not interactive:
            return Outcome("needs_manual", "captcha shown - not bypassed")
        wait_for_human(page, "A captcha / human check is showing. Please complete it.")
    return None


def _portal_apply(page: Page, job: Job, spec: PortalSpec, filler: FormFiller,
                  auto: bool, interactive: bool) -> Outcome:
    def manual(reason: str) -> Outcome:
        if interactive:
            return ask_user(job, f"{reason}. Please finish in the browser.")
        return Outcome("needs_manual", reason)

    try:
        page.goto(job.url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        reject_cookies(page)
        if (stop := _guard(page, spec, job, interactive)):
            return stop
        if spec.already_applied.search(_text(page.locator("body"))[:20000]):
            return Outcome("applied", f"{spec.name} shows you already applied", via=f"{spec.name}-earlier")

        start = _clickable(page, spec.start)
        if start is None:
            if _clickable(page, spec.external):
                return manual("This job applies on the company's own site")
            return manual("Could not find the apply button")
        try:
            with page.context.expect_page(timeout=5000) as new_page:
                start.click()
            page = new_page.value
            page.wait_for_load_state("domcontentloaded")
        except PWTimeout:
            pass  # application opened in the same tab / a modal

        same_count = 0
        last_text = ""
        for _ in range(15):
            page.wait_for_timeout(2000)
            if (stop := _guard(page, spec, job, interactive)):
                return stop
            scope_loc = page.locator(spec.scope).last if spec.scope else None
            scope = scope_loc if scope_loc is not None and scope_loc.count() else page.locator("body")
            text = _text(scope)
            if spec.success.search(text):
                return Outcome("applied", "confirmation shown", via=spec.name)

            unanswered = filler.fill(scope)
            submit = _clickable(scope, spec.submit)
            if submit is not None:
                if not auto:
                    extra = f" Unanswered: {'; '.join(unanswered)}." if unanswered else ""
                    return ask_user(job, f"Form is filled and waiting at Submit - review and click it.{extra}")
                if unanswered:
                    return Outcome("needs_manual", f"unanswered required questions: {'; '.join(unanswered)}")
                submit.click()
                for _ in range(4):
                    page.wait_for_timeout(2500)
                    if spec.success.search(_text(page.locator("body"))):
                        return Outcome("applied", "submitted and confirmed", via=spec.name)
                return Outcome("failed", "clicked Submit but saw no confirmation - please check")

            nxt = _clickable(scope, spec.next)
            if nxt is None:
                return manual("No Next/Submit button found" + (f" (unanswered: {'; '.join(unanswered)})" if unanswered else ""))
            nxt.click()
            page.wait_for_timeout(1500)
            new_text = _text(scope)
            if new_text == text or new_text == last_text:
                same_count += 1
                if same_count >= 2:
                    why = "; ".join(unanswered) if unanswered else "form shows errors"
                    return manual(f"Stuck on a step ({why})")
            else:
                same_count = 0
            last_text = text
        return manual("Too many steps")
    except Exception as e:  # never let one bad page kill the run
        if interactive:
            return ask_user(job, f"Automation error ({type(e).__name__}: {str(e)[:150]}).")
        return Outcome("failed", f"{type(e).__name__}: {str(e)[:200]}")
