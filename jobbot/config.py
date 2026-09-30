"""Loads config.yaml (+ .env) into a plain dict with defaults filled in."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
PROFILE_DIR = ROOT / "profile"
MASTER_RESUME = PROFILE_DIR / "resume.yaml"

DEFAULTS: dict = {
    "search": {
        "queries": [],
        "c2c_query_suffix": "C2C",
        "locations": ["United States"],
        "remote_only": False,
        "posted_within_days": 3,
        "max_results_per_query": 40,
        "sources": ["dice", "linkedin", "indeed", "corptocorp"],
    },
    "filters": {
        "include_unclear_c2c": False,
        "exclude_keywords": [],
        "exclude_title_keywords": ["hotlist", "bench"],
        "min_match_score": 65,
        "duplicate_window_days": 45,
    },
    "claude": {"model": "claude-opus-5-5", "effort": "medium"},
    "apply": {
        "mode": "review",
        "auto_submit_portals": ["dice"],
        "daily_limit": 30,
        "per_portal_daily_limit": {},
        "delay_seconds": [25, 70],
        "email": {"enabled": True, "send": False, "fallback_for_portals": True},
    },
    "browser": {"channel": "chrome", "profile_dir": "~/.jobbot/browser_profile"},
    "profile": {},
    "answers": [],
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    path = Path(path) if path else ROOT / "config.yaml"
    if not path.exists():
        raise SystemExit(
            f"Config not found: {path}\nCopy config.example.yaml to config.yaml and edit it."
        )
    with open(path, encoding="utf-8") as f:
        cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})
    if not cfg["search"]["queries"]:
        raise SystemExit("config.yaml: search.queries is empty - add at least one job title.")
    return cfg


def render_answer(template: str, cfg: dict) -> str:
    """Replace {profile.xxx} placeholders with values from cfg['profile']."""
    profile = cfg.get("profile", {})
    return re.sub(
        r"\{profile\.(\w+)\}", lambda m: str(profile.get(m.group(1), "") or ""), str(template)
    )
