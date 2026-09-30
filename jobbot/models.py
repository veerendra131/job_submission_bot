from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    source: str            # dice | linkedin | indeed | corptocorp
    source_id: str         # the board's own job id
    url: str
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    posted: str = ""       # ISO date or board text
    employment_type: str = ""
    contact_email: str = ""  # recruiter email found in the posting, if any
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"[{self.source}] {self.title} @ {self.company or '?'}"
