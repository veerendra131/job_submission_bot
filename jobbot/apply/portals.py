"""Per-portal knowledge: how to start an application and which buttons move it along.

Job-board markup changes often. If a portal stops working, this is the file to adjust.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class PortalSpec:
    name: str
    start: re.Pattern          # button/link that opens the in-portal application
    external: re.Pattern       # button/link meaning "apply on the company's own site"
    already_applied: re.Pattern
    login_wall: re.Pattern     # matched against the URL
    scope: str                 # CSS for the application container ("" = whole page)
    next: re.Pattern
    submit: re.Pattern
    success: re.Pattern
    error: re.Pattern


PORTALS = {
    "dice": PortalSpec(
        name="dice",
        start=re.compile(r"^\s*easy apply\s*$|^\s*apply now\s*$", re.I),
        external=re.compile(r"apply (on|at) (company|employer)|^\s*apply\s*$", re.I),
        already_applied=re.compile(r"\byou applied\b|\bapplication submitted\b|\balready applied\b", re.I),
        login_wall=re.compile(r"/login|/register|auth\.", re.I),
        scope="",
        next=re.compile(r"^\s*(next|continue)\s*$", re.I),
        submit=re.compile(r"^\s*submit( application)?\s*$", re.I),
        success=re.compile(r"application (was |has been )?(submitted|sent)|you('ve| have)? applied|applied successfully", re.I),
        error=re.compile(r"(is|are) required|please (enter|select|complete)|invalid", re.I),
    ),
    "linkedin": PortalSpec(
        name="linkedin",
        start=re.compile(r"easy apply", re.I),
        external=re.compile(r"^\s*apply\s*$|apply on company", re.I),
        already_applied=re.compile(r"\bapplied \d+ \w+ ago\b|\bapplication submitted\b|\bsee application\b", re.I),
        login_wall=re.compile(r"authwall|/login|/checkpoint|/uas/", re.I),
        scope='.jobs-easy-apply-modal, [data-test-modal][role="dialog"], [role="dialog"]',
        next=re.compile(r"continue to next step|^\s*next\s*$|review your application|^\s*review\s*$", re.I),
        submit=re.compile(r"submit application", re.I),
        success=re.compile(r"your application was sent|application (was )?sent to", re.I),
        error=re.compile(r"please (enter|make a selection|select)|(is|are) required|enter a (valid|whole)", re.I),
    ),
    "indeed": PortalSpec(
        name="indeed",
        start=re.compile(r"^\s*apply now\s*$|easily apply", re.I),
        external=re.compile(r"apply on company site", re.I),
        already_applied=re.compile(r"\byou applied\b|\bapplied on\b|\bapplication submitted\b", re.I),
        login_wall=re.compile(r"secure\.indeed\.com/auth|/account/login", re.I),
        scope="",
        next=re.compile(r"^\s*continue\s*$|^\s*next\s*$|review your application", re.I),
        submit=re.compile(r"submit (your )?application", re.I),
        success=re.compile(r"application (has been )?submitted|your application has been submitted", re.I),
        error=re.compile(r"(is|are) required|please (answer|enter|select)|invalid", re.I),
    ),
}
