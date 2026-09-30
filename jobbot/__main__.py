"""Command line: python -m jobbot <command>"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import MASTER_RESUME, OUTPUT_DIR, load_config

console = Console()


def cmd_import_resume(args, cfg):
    from .resume import save_master
    from .tailor import Tailor

    src = Path(args.file)
    if not src.exists():
        raise SystemExit(f"File not found: {src}")
    if MASTER_RESUME.exists() and not args.force:
        raise SystemExit(f"{MASTER_RESUME} already exists. Use --force to overwrite it.")
    console.print(f"Reading {src.name} and structuring it with Claude...")
    resume = Tailor(cfg).import_resume(src)
    save_master(resume)
    console.print(f"[green]Saved {MASTER_RESUME}[/]")
    console.print("Open it and check every line - this is the only source the bot tailors from.")


def cmd_login(args, cfg):
    from .browser import LOGIN_PAGES, Browser

    with Browser(cfg) as b:
        first = True
        for name, url in LOGIN_PAGES.items():
            page = b.page() if first else b.ctx.new_page()
            first = False
            page.goto(url)
        input("\nSign in to LinkedIn, Indeed and Dice in the browser tabs that opened.\n"
              "Your logins are saved in the bot's own browser profile.\n"
              "Press Enter here when done... ")


def cmd_run(args, cfg):
    from .pipeline import Pipeline

    mode = args.mode or cfg["apply"]["mode"]
    sources = args.sources.split(",") if args.sources else cfg["search"]["sources"]
    if args.query:
        cfg["search"]["queries"] = [args.query]
    console.print(f"Mode: [bold]{mode}[/]   sources: {', '.join(sources)}")
    Pipeline(cfg, mode, args.max).run_search(sources)


def cmd_apply(args, cfg):
    from .pipeline import Pipeline

    mode = args.mode or cfg["apply"]["mode"]
    if mode == "dry-run":
        raise SystemExit("apply needs --mode review or --mode auto")
    Pipeline(cfg, mode, args.max).run_pending(args.status.split(","))


def cmd_status(args, cfg):
    from .db import Tracker

    t = Tracker()
    counts = t.counts()
    console.print("  ".join(f"{k}: [bold]{v}[/]" for k, v in sorted(counts.items())) or "No jobs yet.")
    console.print(f"Applied today: {t.applied_today()}")
    rows = t.rows(args.status, args.limit)
    if not rows:
        return
    table = Table(show_lines=False)
    for col in ("id", "status", "score", "source", "title", "company", "note"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(str(r["id"]), r["status"], str(r["match_score"] or ""), r["source"],
                      (r["title"] or "")[:45], (r["company"] or "")[:25], (r["reason"] or "")[:60])
    console.print(table)


def cmd_mark_applied(args, cfg):
    from .db import Tracker

    rid = Tracker().mark_applied_manually(args.url, args.title or "", args.company or "")
    console.print(f"Recorded as applied (#{rid}). The bot will not apply to it again.")


def cmd_reset(args, cfg):
    from .db import Tracker

    t = Tracker()
    n = t.conn.execute("UPDATE jobs SET status='new', reason='' WHERE status=?", (args.status,)).rowcount
    t.conn.commit()
    console.print(f"Reset {n} '{args.status}' job(s) to 'new'; they will be re-evaluated next run.")


def cmd_export(args, cfg):
    from .db import Tracker

    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / "applications.csv"
    rows = Tracker().rows(None, 100000)
    cols = ["id", "status", "match_score", "source", "title", "company", "location", "url",
            "applied_at", "applied_via", "resume_path", "reason", "first_seen"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    console.print(f"Wrote {len(rows)} rows to {path}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="jobbot", description="C2C job search + tailored resume + apply bot")
    p.add_argument("--config", help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import-resume", help="turn your resume (.pdf/.docx/.txt) into profile/resume.yaml")
    s.add_argument("file")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_import_resume)

    s = sub.add_parser("login", help="open the bot's browser so you can sign in to the job boards")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("run", help="search all boards, tailor resumes, and apply")
    s.add_argument("--mode", choices=["dry-run", "review", "auto"])
    s.add_argument("--sources", help="comma list, e.g. dice,linkedin")
    s.add_argument("--query", help="override search.queries with one query")
    s.add_argument("--max", type=int, help="max applications this run")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("apply", help="apply to jobs already found and tailored (no new search)")
    s.add_argument("--mode", choices=["review", "auto"])
    s.add_argument("--status", default="tailored,needs_manual")
    s.add_argument("--max", type=int)
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("status", help="show tracked jobs")
    s.add_argument("--status")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("mark-applied", help="record a job you applied to yourself")
    s.add_argument("url")
    s.add_argument("--title")
    s.add_argument("--company")
    s.set_defaults(func=cmd_mark_applied)

    s = sub.add_parser("reset", help="re-evaluate jobs with a given status (e.g. filtered)")
    s.add_argument("status", choices=["filtered", "low_match", "failed", "needs_manual", "skipped"])
    s.set_defaults(func=cmd_reset)

    s = sub.add_parser("export", help="write output/applications.csv")
    s.set_defaults(func=cmd_export)

    args = p.parse_args(argv)
    cfg = load_config(args.config)
    try:
        args.func(args, cfg)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/] Progress is saved; run again to continue.")
        sys.exit(130)


if __name__ == "__main__":
    main()
