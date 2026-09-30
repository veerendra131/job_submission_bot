"""Dice.com: search result pages + JSON-LD on each job page (no login needed)."""
from __future__ import annotations

import json
import re
from typing import Iterator

from bs4 import BeautifulSoup

from ..c2c import find_contact_email
from ..models import Job
from . import Http

_ID_RE = re.compile(r"/job-detail/([0-9a-f-]{36})")


def _posted_filter(days: int) -> str:
    return "ONE" if days <= 1 else "THREE" if days <= 3 else "SEVEN"


class DiceSource:
    name = "dice"
    needs_browser = False

    def __init__(self):
        self.http = Http()

    def search(self, query: str, location: str, cfg: dict, limit: int, browser=None,
               skip=None) -> Iterator[Job]:
        s = cfg["search"]
        seen: set[str] = set()
        page = 1
        while len(seen) < limit and page <= 10:
            params = {
                "q": query,
                "filters.postedDate": _posted_filter(s["posted_within_days"]),
                "page": page,
                "pageSize": 20,
            }
            if location and location.lower() not in ("united states", "usa", "us"):
                params["location"] = location
            if s.get("remote_only"):
                params["filters.workplaceTypes"] = "Remote"
            r = self.http.get("https://www.dice.com/jobs", params=params)
            if r.status_code != 200:
                break
            cards = self._cards(r.text)
            new = [c for c in cards if c[0] not in seen]
            if not new:
                break
            for job_id, card_text in new:
                seen.add(job_id)
                if skip and skip(self.name, job_id):
                    continue
                job = self._detail(job_id, card_text)
                if job:
                    yield job
                if len(seen) >= limit:
                    break
            page += 1

    @staticmethod
    def _cards(html: str) -> list[tuple[str, str]]:
        """Return [(job_id, card_text)] in page order."""
        soup = BeautifulSoup(html, "html.parser")
        out: dict[str, str] = {}
        for a in soup.select('a[href*="/job-detail/"]'):
            m = _ID_RE.search(a.get("href", ""))
            if not m or m.group(1) in out:
                continue
            # Climb to the largest ancestor that still only contains this one job.
            node, card = a, a
            while node.parent is not None:
                ids = {_ID_RE.search(x.get("href", "")).group(1)
                       for x in node.parent.select('a[href*="/job-detail/"]')
                       if _ID_RE.search(x.get("href", ""))}
                if len(ids) > 1:
                    break
                node = card = node.parent
            out[m.group(1)] = card.get_text(" | ", strip=True)
        return list(out.items())

    def _detail(self, job_id: str, card_text: str) -> Job | None:
        url = f"https://www.dice.com/job-detail/{job_id}"
        r = self.http.get(url)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        data = {}
        for sc in soup.select('script[type="application/ld+json"]'):
            try:
                d = json.loads(sc.string or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                data = d
                break
        if not data:
            return None
        desc = BeautifulSoup(data.get("description", ""), "html.parser").get_text("\n", strip=True)
        loc = ""
        jl = data.get("jobLocation")
        if isinstance(jl, list) and jl:
            jl = jl[0]
        if isinstance(jl, dict):
            addr = jl.get("address") or {}
            loc = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion")] if x)
        if not loc and "remote" in card_text.lower():
            loc = "Remote"
        emp = data.get("employmentType") or ""
        if isinstance(emp, list):
            emp = ", ".join(emp)
        if "third party" in card_text.lower():
            emp = f"{emp}, Third Party".strip(", ")
        return Job(
            source=self.name,
            source_id=job_id,
            url=url,
            title=data.get("title", "").strip(),
            company=((data.get("hiringOrganization") or {}).get("name") or "").strip(),
            location=loc,
            description=desc,
            posted=str(data.get("datePosted", ""))[:10],
            employment_type=emp,
            contact_email=find_contact_email(desc),
            extra={"easy_apply": "easy apply" in card_text.lower()},
        )
