"""One persistent, visible Chrome/Edge window shared by search (Indeed) and applying.

Logins are stored in the bot's own browser profile folder (default
~/.jobbot/browser_profile, not your normal Chrome profile). You sign in yourself once with `python -m jobbot login`; the bot never
types passwords.
"""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .config import ROOT

LOGIN_PAGES = {
    "linkedin": "https://www.linkedin.com/login",
    "indeed": "https://secure.indeed.com/auth",
    "dice": "https://www.dice.com/dashboard/login",
}

_CAPTCHA_SELECTORS = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[title*="challenge" i]',
    "#challenge-form",
]
_CAPTCHA_TEXT = re.compile(
    r"verify you are human|are you a robot|security check|unusual activity|press (and|&) hold",
    re.I,
)


class Browser:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._pw = None
        self.ctx: BrowserContext | None = None

    def __enter__(self) -> "Browser":
        b = self.cfg["browser"]
        # Short absolute default: Chrome fails on very long profile paths on Windows.
        profile = Path(b.get("profile_dir") or "~/.jobbot/browser_profile").expanduser()
        if not profile.is_absolute():
            profile = ROOT / profile
        profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel=b.get("channel", "chrome"),
            headless=False,
            no_viewport=True,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
        self.ctx.set_default_timeout(15000)
        return self

    def __exit__(self, *exc):
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def page(self) -> Page:
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        return pages[0] if pages else self.ctx.new_page()


def has_captcha(page: Page) -> bool:
    try:
        for sel in _CAPTCHA_SELECTORS:
            if page.locator(sel).first.is_visible(timeout=300):
                return True
        return bool(_CAPTCHA_TEXT.search(page.locator("body").inner_text(timeout=2000)[:3000]))
    except Exception:
        return False


def reject_cookies(page: Page) -> None:
    """Dismiss cookie banners choosing the privacy-preserving option."""
    for rx in (r"^\s*reject all\s*$", r"^\s*(decline|reject)( all| optional| non-essential)?( cookies)?\s*$",
               r"^\s*necessary (cookies )?only\s*$"):
        try:
            btn = page.get_by_role("button", name=re.compile(rx, re.I)).first
            if btn.is_visible(timeout=500):
                btn.click()
                return
        except Exception:
            continue


def wait_for_human(page: Page, reason: str) -> None:
    """Pause until the user has dealt with something in the browser (captcha, login)."""
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        input(f"\n  >> {reason}\n     Do it in the browser window, then press Enter here to continue... ")
    except EOFError:  # running unattended (scheduled task): nobody to ask
        raise RuntimeError(f"needs a person: {reason}") from None
