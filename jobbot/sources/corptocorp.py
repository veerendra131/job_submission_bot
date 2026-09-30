"""corptocorp.org: a C2C-only board. Applications go to the recruiter by email."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterator
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..c2c import find_contact_email
from ..models import Job
from . import Http

_AGE_RE = re.compile(r"Posted\s+(\d+)\s+(minute|hour|day|week|month)s?\s+ago", re.I)
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30}


class CorpToCorpSource:
    name = "corptocorp"
    needs_browser = False

    def __init__(self):
        self.http = Http()

    def search(self, query: str, location: str, cfg: dict, limit: int, browser=None,
               skip=None) -> Iterator[Job]:
        max_age = float(cfg["search"]["posted_within_days"])
        count, page, too_old_streak = 0, 1, 0
        while count < limit and page <= 10 and too_old_streak < 5:
            base = "https://corptocorp.org/" if page == 1 else f"https://corptocorp.org/page/{page}/"
            r = self.http.get(f"{base}?s={quote_plus(query)}")
            if r.status_code != 200:
                break
            links = []
            for art in BeautifulSoup(r.text, "html.parser").select("article"):
                a = art.select_one("h2 a, h3 a, .entry-title a")
                if a and a.get("href"):
                    links.append((a["href"], a.get_text(" ", strip=True)))
            if not links:
                break
            for href, title in links:
                if skip and skip(self.name, href.rstrip("/").rsplit("/", 1)[-1]):
                    continue
                job, age = self._detail(href, title)
                if job is None:
                    continue
                if age is not None and age > max_age:
                    too_old_streak += 1
                    continue
                too_old_streak = 0
                if location and not self._location_ok(job, location):
                    continue
                count += 1
                yield job
                if count >= limit:
                    break
            page += 1

    @staticmethod
    def _location_ok(job: Job, location: str) -> bool:
        loc = location.lower()
        if loc in ("united states", "usa", "us", ""):
            return True
        hay = f"{job.location} {job.title}".lower()
        return loc in hay or "remote" in hay

    def _detail(self, url: str, title: str) -> tuple[Job | None, float | None]:
        r = self.http.get(url)
        if r.status_code != 200:
            return None, None
        soup = BeautifulSoup(r.text, "html.parser")
        body = soup.select_one(".entry-content") or soup.body
        text = body.get_text("\n", strip=True) if body else ""
        lines = [ln for ln in text.split("\n") if ln.strip()]

        age = None
        m = _AGE_RE.search(text)
        if m:
            age = int(m.group(1)) * _UNIT_DAYS[m.group(2).lower()]
        def first(sel: str) -> str:
            for el in soup.select(sel):
                t = el.get_text(" ", strip=True)
                if t:
                    return t
            return ""

        company, location = first(".company"), first(".location")
        if not company:
            # Header block looks like: Contract / City, ST / Posted N days ago / Company / Title
            for i, ln in enumerate(lines[:8]):
                if _AGE_RE.search(ln):
                    company = lines[i + 1] if i + 1 < len(lines) else ""
                    location = location or (lines[i - 1] if i >= 1 else "")
                    break
        if not location:
            m = re.search(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s*([A-Z]{2})\b", title)
            location = f"{m.group(1)}, {m.group(2)}" if m else ("Remote" if "remote" in title.lower() else "")

        email = ""
        for a in body.select('a[href^="mailto:"]') if body else []:
            email = a["href"][7:].split("?")[0].strip().lower()
            break
        email = email or find_contact_email(text)
        if not company and email and not email.endswith(("@gmail.com", "@yahoo.com", "@outlook.com", "@hotmail.com")):
            company = email.split("@", 1)[1].rsplit(".", 1)[0]  # vendor domain, e.g. compunnel
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        posted = (datetime.now() - timedelta(days=age)).date().isoformat() if age is not None else ""
        h1 = soup.select_one("h1")
        return Job(
            source=self.name,
            source_id=slug,
            url=url,
            title=(h1.get_text(" ", strip=True) if h1 else title),
            company=company,
            location=location,
            description=text,
            posted=posted,
            employment_type="C2C",
            contact_email=email,
        ), age
