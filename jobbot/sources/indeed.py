"""Indeed: blocks plain HTTP clients, so search runs in the bot's visible browser."""
from __future__ import annotations

import random
import time
from typing import Iterator
from urllib.parse import urlencode

from ..browser import has_captcha, wait_for_human
from ..c2c import find_contact_email
from ..models import Job


class IndeedSource:
    name = "indeed"
    needs_browser = True

    def search(self, query: str, location: str, cfg: dict, limit: int, browser=None,
               skip=None) -> Iterator[Job]:
        if browser is None:
            raise RuntimeError("Indeed search needs the browser")
        s = cfg["search"]
        # Own tab, so searching never navigates away from an application in progress.
        if getattr(self, "_page", None) is None or self._page.is_closed():
            self._page = browser.ctx.new_page()
        page = self._page
        count, start = 0, 0
        while count < limit and start < 300:
            params = {
                "q": query,
                "l": "Remote" if s.get("remote_only") else (location or "United States"),
                "fromage": max(1, int(s["posted_within_days"])),
                "sc": "0kf:jt(contract);",
                "start": start,
            }
            page.goto(f"https://www.indeed.com/jobs?{urlencode(params)}", wait_until="domcontentloaded")
            self._captcha_check(page)
            try:
                page.wait_for_selector("[data-jk]", timeout=10000)
            except Exception:
                break
            cards = page.evaluate(
                """() => [...document.querySelectorAll('[data-jk]')].map(el => {
                    const card = el.closest('.job_seen_beacon, .cardOutline, li') || el;
                    const q = s => (card.querySelector(s)?.innerText || '').trim();
                    return {jk: el.getAttribute('data-jk'),
                            title: q('h2.jobTitle span[title]') || q('h2.jobTitle') || el.innerText.trim(),
                            company: q('[data-testid="company-name"]'),
                            location: q('[data-testid="text-location"]')};
                })"""
            )
            uniq = {c["jk"]: c for c in cards if c.get("jk")}
            if not uniq:
                break
            for jk, c in uniq.items():
                if skip and skip(self.name, jk):
                    continue
                job = self._detail(page, jk, c)
                if job:
                    count += 1
                    yield job
                if count >= limit:
                    break
            start += 10

    def _captcha_check(self, page):
        if has_captcha(page):
            wait_for_human(page, "Indeed is showing a human check. Please complete it.")

    def _detail(self, page, jk: str, card: dict) -> Job | None:
        url = f"https://www.indeed.com/viewjob?jk={jk}"
        time.sleep(random.uniform(1.5, 4))
        page.goto(url, wait_until="domcontentloaded")
        if "secure.indeed.com/auth" in page.url:
            # Indeed only shows job pages to signed-in users.
            wait_for_human(page, "Indeed requires you to be signed in. Please sign in "
                                 "(tip: run `python -m jobbot login` once beforehand).")
            page.goto(url, wait_until="domcontentloaded")
            if "secure.indeed.com/auth" in page.url:
                raise RuntimeError("not signed in to Indeed - run: python -m jobbot login")
        self._captcha_check(page)
        try:
            desc = page.locator("#jobDescriptionText").inner_text(timeout=10000)
        except Exception:
            return None
        return Job(
            source=self.name,
            source_id=jk,
            url=url,
            title=card.get("title", "").replace("\n", " ").strip(),
            company=card.get("company", "").strip(),
            location=card.get("location", "").strip(),
            description=desc,
            contact_email=find_contact_email(desc),
        )
