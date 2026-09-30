"""Job-board search connectors. Each one yields `Job` objects with a full description."""
from __future__ import annotations

import random
import time

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


class Http:
    """requests.Session with a browser UA, polite pacing, and 429 backoff."""

    def __init__(self, min_delay: float = 1.0, max_delay: float = 2.5):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        self.min_delay, self.max_delay = min_delay, max_delay

    def get(self, url: str, **kw) -> requests.Response:
        for attempt in range(4):
            time.sleep(random.uniform(self.min_delay, self.max_delay))
            r = self.s.get(url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            return r
        return r


def get_source(name: str):
    if name == "dice":
        from .dice import DiceSource
        return DiceSource()
    if name == "linkedin":
        from .linkedin import LinkedInSource
        return LinkedInSource()
    if name == "indeed":
        from .indeed import IndeedSource
        return IndeedSource()
    if name == "corptocorp":
        from .corptocorp import CorpToCorpSource
        return CorpToCorpSource()
    raise ValueError(f"Unknown source '{name}'. Use: dice, linkedin, indeed, corptocorp")
