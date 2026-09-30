"""LinkedIn: public (logged-out) job search endpoints. Applying needs the logged-in browser."""
from __future__ import annotations

from typing import Iterator

from bs4 import BeautifulSoup

from ..c2c import find_contact_email
from ..models import Job
from . import Http

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"


class LinkedInSource:
    name = "linkedin"
    needs_browser = False

    def __init__(self):
        self.http = Http(1.5, 3.5)

    def search(self, query: str, location: str, cfg: dict, limit: int, browser=None,
               skip=None) -> Iterator[Job]:
        s = cfg["search"]
        count, start = 0, 0
        while count < limit and start < 500:
            params = {
                "keywords": query,
                "location": location or "United States",
                "f_TPR": f"r{int(s['posted_within_days']) * 86400}",
                "f_JT": "C",  # contract
                "start": start,
            }
            if s.get("remote_only"):
                params["f_WT"] = "2"
            r = self.http.get(SEARCH, params=params)
            if r.status_code != 200 or not r.text.strip():
                break
            cards = BeautifulSoup(r.text, "html.parser").select("div.base-card")
            if not cards:
                break
            for c in cards:
                urn = c.get("data-entity-urn", "")
                job_id = urn.rsplit(":", 1)[-1]
                if not job_id.isdigit() or (skip and skip(self.name, job_id)):
                    continue
                job = self._detail(job_id, c)
                if job:
                    count += 1
                    yield job
                if count >= limit:
                    break
            start += len(cards)

    def _detail(self, job_id: str, card) -> Job | None:
        def text(sel):
            el = card.select_one(sel)
            return el.get_text(" ", strip=True) if el else ""

        r = self.http.get(DETAIL.format(id=job_id))
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        desc_el = soup.select_one(".show-more-less-html__markup")
        desc = desc_el.get_text("\n", strip=True) if desc_el else ""
        criteria = " ".join(
            li.get_text(" ", strip=True) for li in soup.select(".description__job-criteria-item")
        )
        posted = card.select_one("time")
        return Job(
            source=self.name,
            source_id=job_id,
            url=f"https://www.linkedin.com/jobs/view/{job_id}/",
            title=text("h3.base-search-card__title") or text("h3"),
            company=text("h4.base-search-card__subtitle") or text("h4"),
            location=text(".job-search-card__location"),
            description=desc,
            posted=posted.get("datetime", "") if posted else "",
            employment_type=criteria,
            contact_email=find_contact_email(desc),
        )
