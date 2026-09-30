"""Fills application form fields from config `answers` and uploads the tailored resume."""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Locator

from ..config import render_answer

_DESCRIBE_JS = """el => {
  const root = el.getRootNode();
  const txt = x => x ? (x.innerText || x.textContent || '').trim() : '';
  let label = '';
  if (el.id) { try { label = txt(root.querySelector(`label[for="${CSS.escape(el.id)}"]`)); } catch (e) {} }
  if (!label) label = el.getAttribute('aria-label') || '';
  if (!label && el.getAttribute('aria-labelledby'))
    label = el.getAttribute('aria-labelledby').split(/\\s+/).map(id => txt(root.getElementById ? root.getElementById(id) : document.getElementById(id))).join(' ');
  if (!label) label = txt(el.closest('label'));
  const fs = el.closest('fieldset');
  const legend = fs ? txt(fs.querySelector('legend')) || txt(fs.querySelector('span')) : '';
  if (!label) label = legend;
  if (!label) label = el.getAttribute('placeholder') || el.name || '';
  return {
    tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    name: el.name || '',
    label: label.replace(/\\s+/g, ' ').slice(0, 200),
    group: (legend || '').replace(/\\s+/g, ' ').slice(0, 200),
    value: el.value || '',
    checked: !!el.checked,
    accept: el.getAttribute('accept') || '',
    required: el.required || el.getAttribute('aria-required') === 'true',
  };
}"""


class FormFiller:
    def __init__(self, cfg: dict, resume_path: Path):
        self.cfg = cfg
        self.resume_path = resume_path
        self.rules = [
            (re.compile(a["pattern"], re.I), render_answer(a["answer"], cfg))
            for a in cfg.get("answers", [])
            if a.get("pattern")
        ]
        self.uploaded: set[str] = set()

    def answer_for(self, question: str) -> str | None:
        for rx, ans in self.rules:
            if rx.search(question) and ans != "":
                return ans
        return None

    def fill(self, scope: Locator) -> list[str]:
        """Fill every visible field in scope. Returns required questions left unanswered."""
        unanswered: list[str] = []
        radio_groups_done: set[str] = set()
        fields = scope.locator("input, select, textarea")
        for i in range(fields.count()):
            el = fields.nth(i)
            try:
                d = el.evaluate(_DESCRIBE_JS)
            except Exception:
                continue
            t, tag = d["type"], d["tag"]
            if t in ("hidden", "submit", "button", "image", "reset", "search"):
                continue
            if t == "file":
                self._upload(el, d)
                continue
            try:
                if not el.is_visible():
                    # Radios/checkboxes are often visually hidden behind styled labels.
                    if t not in ("radio", "checkbox"):
                        continue
            except Exception:
                continue
            question = d["group"] if t == "radio" and d["group"] else d["label"]
            try:
                if t == "radio":
                    key = d["name"] or question
                    if key in radio_groups_done:
                        continue
                    radio_groups_done.add(key)
                    if not self._radio(scope, el, d, question) and d["required"]:
                        unanswered.append(question)
                elif t == "checkbox":
                    continue  # consent/terms boxes are left for you
                elif tag == "select":
                    if not self._select(el, question) and d["required"]:
                        unanswered.append(question)
                else:
                    if d["value"].strip():
                        continue  # prefilled by the portal
                    ans = self.answer_for(question)
                    if ans is None:
                        if d["required"]:
                            unanswered.append(question)
                        continue
                    if t == "number":
                        ans = re.sub(r"[^\d.]", "", ans) or ans
                    el.fill(ans)
            except Exception:
                if d["required"]:
                    unanswered.append(question)
        return [q for q in unanswered if q]

    def _upload(self, el: Locator, d: dict) -> None:
        hint = f"{d['label']} {d['name']} {d['accept']}".lower()
        if "cover" in hint:
            return
        if not any(k in hint for k in ("resume", "cv", ".doc", ".pdf", "upload", "file")) and d["accept"]:
            return
        key = f"{d['name']}|{d['label']}"
        if key in self.uploaded:
            return
        try:
            el.set_input_files(str(self.resume_path))
            self.uploaded.add(key)
        except Exception:
            pass

    def _select(self, el: Locator, question: str) -> bool:
        current = el.evaluate("s => s.selectedIndex > 0 || (s.value && !/select/i.test(s.options[s.selectedIndex]?.text || ''))")
        if current:
            return True
        ans = self.answer_for(question)
        if ans is None:
            return False
        options = el.evaluate("s => [...s.options].map(o => o.text.trim())")
        choice = _best_option(options, ans)
        if choice is None:
            return False
        el.select_option(label=choice)
        return True

    def _radio(self, scope: Locator, el: Locator, d: dict, question: str) -> bool:
        group = scope.locator(f'input[type="radio"][name="{d["name"]}"]') if d["name"] else el
        n = group.count()
        labels, checked = [], False
        for j in range(n):
            info = group.nth(j).evaluate(_DESCRIBE_JS)
            labels.append(info["label"])
            checked = checked or info["checked"]
        if checked:
            return True
        ans = self.answer_for(question)
        if ans is None:
            return False
        choice = _best_option(labels, ans)
        if choice is None:
            return False
        radio = group.nth(labels.index(choice))
        try:
            radio.check(force=True)
        except Exception:
            radio.evaluate("r => r.click()")
        return True


def _best_option(options: list[str], answer: str) -> str | None:
    a = answer.strip().lower()
    for o in options:
        if o.strip().lower() == a:
            return o
    for o in options:
        if o.strip().lower().startswith(a) or (a and a in o.strip().lower()):
            return o
    return None
