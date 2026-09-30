# job_submission_bot

A bot for **C2C (Corp-to-Corp) IT contract jobs**. It searches Dice, LinkedIn, Indeed, and
corptocorp.org, keeps only postings that accept C2C, tailors your resume to each job description
with Claude, and applies. It never applies to the same job twice.

```
search boards ─► C2C + keyword filter ─► already applied? ─► Claude: score + tailor resume ─► apply
   (4 boards)       (free, local)          (SQLite check)       (.docx per job, fact-checked)   (review / auto / email)
```

## What it does

| Step | Details |
|---|---|
| **Search** | Dice, LinkedIn (public job search), Indeed (in the bot's browser), and corptocorp.org (a C2C-only board). Uses your job titles, locations, and posting age. |
| **C2C filter** | Skips postings that say *W2 only*, *no C2C*, *no third party*, *full-time only*, and so on. Keeps postings that mention C2C, corp-to-corp, or third party (including Dice's "Third Party" tag). Postings that never mention it are skipped unless `include_unclear_c2c: true`. Also skips your `exclude_keywords` (e.g. clearance). |
| **No repeat applications** | Every job is logged in `data/jobs.db`. A posting counts as already applied if it matches an earlier application by **board + job id**, **URL**, or **company + title + city** within `duplicate_window_days`. The last check catches the same role reposted or cross-posted on another board. Jobs you apply to outside the bot can be recorded with `mark-applied`. |
| **Resume tailoring** | Claude rewrites your headline and summary, reorders and rewords bullets, and reorders skills to match the job description. It also scores the fit (0-100) and flags hard blockers. The output is **checked in code**: employers, titles, dates, and education are copied from your master resume. Skills not in your master resume are removed. Any rewrite that introduces a number not in your original (e.g. "40%", "12 years") is reverted. Output: `output/resumes/<you>_<company>_<title>.docx`. |
| **Apply** | Dice / LinkedIn / Indeed: fills the application form (your answers from `config.yaml`, tailored resume uploaded). corptocorp.org and postings that list a recruiter email: writes an email with the tailored resume and C2C details (rate, visa, employer). |

## Modes (safe by default)

| Mode | What happens |
|---|---|
| `dry-run` | Search, filter, and tailor resumes only. No browser, nothing submitted. **Start here.** |
| `review` (default) | The bot opens the job, fills everything, and **stops at Submit**. You check and click Submit, then press `a` (applied) / `s` (skip) / `l` (later) in the terminal. Email applications open as a draft in Outlook. |
| `auto` | The bot clicks Submit itself **only on the boards in `auto_submit_portals`** (default: Dice). Other boards stay queued for your next `review` session. Emails are sent only if `apply.email.send: true` and SMTP is set in `.env`; otherwise a draft is saved. |

The bot never types passwords and never solves captchas. You sign in yourself once, and when a
captcha appears it waits for you (review) or marks the job `needs_manual` (auto).
Daily caps (`daily_limit`, `per_portal_daily_limit`) and random pauses between applications are on by default.

> **Note:** LinkedIn's and Indeed's terms prohibit automated applying, and accounts that apply
> too fast get restricted. That's why those two are review-only by default with low daily caps.
> Keep it that way unless you accept that risk.

## Setup (Windows, Python 3.11+)

```bash
git clone https://github.com/veerendra131/job_submission_bot.git
cd job_submission_bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
copy config.example.yaml config.yaml
```

1. **API key:** put your Anthropic API key in `.env` (`ANTHROPIC_API_KEY=...`).
2. **Config:** edit `config.yaml` with your job titles, locations, rate, visa status, employer (C2C)
   details, and screening-question answers.
3. **Resume:** import it once, then **read the result carefully**. `profile/resume.yaml` is the only source of truth the bot tailors from:
   ```bash
   python -m jobbot import-resume "C:\path\to\My Resume.docx"
   ```
4. **Sign in** to the job boards in the bot's own Chrome window (logins are saved in `~/.jobbot/browser_profile`):
   ```bash
   python -m jobbot login
   ```
   Indeed only shows job pages to signed-in users, so sign in there before searching Indeed.

## Daily use

```bash
python -m jobbot run --mode dry-run        # search + tailor only; check output/resumes/
python -m jobbot run --mode review         # search, tailor, fill forms; you click Submit
python -m jobbot apply --mode review       # apply to jobs already tailored (no new search)
python -m jobbot run --mode auto --max 10  # unattended on auto_submit_portals (Dice)
python -m jobbot status                    # what was applied, skipped, and why
python -m jobbot status --status needs_manual
python -m jobbot mark-applied <job url>    # record a job you applied to yourself
python -m jobbot export                    # output/applications.csv
python -m jobbot reset filtered            # re-evaluate filtered jobs after changing filters
```

Useful options: `--sources dice,corptocorp`, `--query "Java Developer"`.

Job statuses: `tailored` (ready to apply) · `applied` · `needs_manual` (you need to finish it)
· `filtered` (not C2C / excluded) · `low_match` (score below `min_match_score` or a hard blocker)
· `duplicate` (already applied elsewhere) · `skipped` · `failed`.

## Cost

Each new job that passes the free local filters costs one Claude call. On `claude-opus-5-5` that's
roughly $0.05–0.10 per job (your resume is prompt-cached across jobs). Set
`claude.model: claude-sonnet-5-5` in `config.yaml` to cut that roughly in half. Re-runs reuse earlier tailoring, so
they cost nothing.

## Project layout

```
jobbot/
  __main__.py      command line
  pipeline.py      search -> filter -> dedupe -> tailor -> apply
  db.py            SQLite tracker and duplicate detection
  c2c.py           C2C / exclusion rules, recruiter email extraction
  tailor.py        Claude resume import and tailoring, plus the anti-fabrication checks
  resume.py        master resume model and .docx rendering
  browser.py       persistent Chrome, captcha detection, cookie banners
  sources/         dice.py  linkedin.py  indeed.py  corptocorp.py
  apply/           __init__.py (flow) forms.py (field filling) portals.py (per-board buttons) email_apply.py
tests/test_core.py offline tests:  python -m unittest discover tests
```

Job-board pages change. If a board stops applying, the button labels live in `jobbot/apply/portals.py`.
To add a board, add a file in `jobbot/sources/` with a `search()` that yields `Job` objects, and register
it in `jobbot/sources/__init__.py`.

Personal files (`.env`, `config.yaml`, `profile/`, `data/`, `output/`) are git-ignored. Keep them out of the repo.
