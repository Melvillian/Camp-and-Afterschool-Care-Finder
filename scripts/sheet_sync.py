"""Sync the volunteer Google Sheet with the staging area.

Usage: python3 scripts/sheet_sync.py pull

pull downloads the Tasks, Drafts and Orgs tabs through the Sheet's public CSV export, saves them as a
dated snapshot under data/staging/<season>/sheet/, prints what changed since the last snapshot, and turns
finished tasks into staging files. Nothing is written unless all three tabs download and parse.
"""
import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import ingest_config
import sources
import staging
import validate_data

ROOT = Path(__file__).resolve().parents[1]

TASK_COLUMNS = ["task_id", "org_id", "org_name", "kind", "minutes", "due", "source_url", "status",
                "claimed_by", "claimed_on", "volunteer_notes"]
TASK_KINDS = {"find_page", "check_draft", "enter_new", "check_changes"}
TASK_STATUSES = {"open", "claimed", "done", "needs_help"}
DRAFT_COLUMNS = (["task_id", "row_id", "review"] + [c for c in staging.PROGRAM_COLUMNS if c != "program_id"]
                 + ["source_url", "source_quote"])
ORG_COLUMNS = ["task_id", "org_id", "registration_opens", "registration_opens_early", "registration_notes",
               "financial_aid_available", "financial_aid_notes", "website"]
TABS = (("Tasks", TASK_COLUMNS), ("Drafts", DRAFT_COLUMNS), ("Orgs", ORG_COLUMNS))
SNAPSHOT_DIR = "sheet"
NEEDS_HELP_FILE = "_needs_help.csv"
URL_RE = re.compile(r"https?://\S+")


class SheetError(Exception):
    """The Sheet could not be read as expected; the message says what to fix."""


def tab_url(sheet_id: str, tab: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={quote(tab)}"


def download_tab(sheet_id: str, tab: str) -> str:
    """Network: the tab as CSV text. Raises SheetError for any failure."""
    req = urllib.request.Request(tab_url(sheet_id, tab), headers={"User-Agent": sources.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=sources.TIMEOUT_SECONDS) as resp:
            return resp.read().decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        raise SheetError(f"could not download the {tab} tab: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SheetError(f"could not download the {tab} tab: {getattr(exc, 'reason', exc)}") from exc


def parse_tab(text: str, required: list[str], tab: str) -> list[dict]:
    """Rows of a tab as dicts: header names and values stripped, blank rows dropped, extra columns kept."""
    if re.match(r"\s*<(!doctype|html)", text[:200], re.I):
        raise SheetError(f"{tab} tab came back as a web page, not CSV "
                         "(is the Sheet shared so anyone with the link can view it?)")
    try:
        table = [r for r in csv.reader(io.StringIO(text.lstrip("\ufeff"))) if any(c.strip() for c in r)]
    except csv.Error as exc:
        raise SheetError(f"{tab} tab could not be read as CSV: {exc}") from exc
    if not table:
        raise SheetError(f"{tab} tab is empty")
    header = [h.strip() for h in table[0]]
    missing = [c for c in required if c not in header]
    if missing:
        raise SheetError(f"{tab} tab is missing column(s): {', '.join(missing)}")
    rows = []
    for cells in table[1:]:
        row = {h: (cells[i].strip() if i < len(cells) else "") for i, h in enumerate(header) if h}
        rows.append(row)
    return rows


def diff_tasks(old: list[dict], new: list[dict]) -> list[str]:
    """One line per task that was added, removed, or changed status or claimer."""
    before = {t["task_id"]: t for t in old}
    after = {t["task_id"]: t for t in new}
    lines = []
    for tid, t in after.items():
        if tid not in before:
            lines.append(f"{tid}: new task ({t['status']})")
            continue
        was = before[tid]
        if was["status"] != t["status"] or was["claimed_by"] != t["claimed_by"]:
            who = f" by {t['claimed_by']}" if t["claimed_by"] else ""
            lines.append(f"{tid}: {was['status']} -> {t['status']}{who}")
    lines += [f"{tid}: removed from the Sheet" for tid in before if tid not in after]
    return lines


def _draft_keys(rows: list[dict]) -> dict:
    keyed, blanks = {}, {}
    for row in rows:
        key = row.get("row_id", "")
        if not key:
            n = blanks[row["task_id"]] = blanks.get(row["task_id"], 0) + 1
            key = f"{row['task_id']}#{n}"
        keyed[key] = {c: row.get(c, "") for c in DRAFT_COLUMNS}
    return keyed


def diff_drafts(old: list[dict], new: list[dict]) -> tuple[int, int, int]:
    """(added, removed, edited) Drafts rows."""
    before, after = _draft_keys(old), _draft_keys(new)
    return (len(after.keys() - before.keys()), len(before.keys() - after.keys()),
            sum(1 for k in before.keys() & after.keys() if before[k] != after[k]))


def check_tasks(tasks: list[dict]) -> tuple[list[dict], list[str], list[tuple[str, str]]]:
    """Usable tasks, a message for each one skipped, and (task_id, reason) for skipped tasks marked done.

    A skipped task is one with no id, a repeated id, or an odd status or kind.
    """
    good, messages, skipped_done, seen = [], [], [], set()
    for t in tasks:
        tid = t.get("task_id", "")
        if not tid:
            messages.append(f"Tasks row for {t.get('org_id') or 'no organization'} has no task_id: skipped")
            continue
        if tid in seen:
            reason = "task_id appears more than once: later row skipped"
        elif t.get("status", "") not in TASK_STATUSES:
            reason = f"status '{t.get('status', '')}' is not one of {', '.join(sorted(TASK_STATUSES))}: skipped"
        elif t.get("kind", "") not in TASK_KINDS:
            reason = f"kind '{t.get('kind', '')}' is not one of {', '.join(sorted(TASK_KINDS))}: skipped"
        else:
            seen.add(tid)
            good.append(t)
            continue
        messages.append(f"{tid}: {reason}")
        if t.get("status") == "done":
            skipped_done.append((tid, reason))
    return good, messages, skipped_done


def _new_done(tasks: list[dict], already_done: set[str], find_page: bool) -> list[dict]:
    return [t for t in tasks if t["status"] == "done" and t["task_id"] not in already_done
            and (t.get("kind") == "find_page") == find_page]


def _org_row(task: dict, orgs: list[dict]) -> dict | None:
    return next((o for o in orgs if o.get("task_id") == task["task_id"]), None)


def find_page_urls(tasks, orgs, already_done) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """org_id -> page address for each newly done find_page task, plus (task_id, reason) for those with none."""
    urls, rejected = {}, []
    for task in _new_done(tasks, already_done, True):
        match = URL_RE.search(task.get("volunteer_notes", ""))
        url = match.group(0).rstrip(".,;)") if match else ""
        if not url:
            org = _org_row(task, orgs)
            url = (org or {}).get("website", "")
        if URL_RE.fullmatch(url):
            urls[task["org_id"]] = url
        else:
            rejected.append((task["task_id"], "no web address: put the page in volunteer_notes or the Orgs website"))
    return urls, rejected


def collect_done(tasks, drafts, orgs, already_done, org_ids, season, today, taken_extra=()):
    """Turn newly done tasks into (staged rows by org, organization facts, rejected (task_id, reason))."""
    taken = {d["row_id"] for d in drafts if d.get("row_id")} | set(taken_extra)
    staged: dict[str, list[dict]] = {}
    facts, rejected = [], []
    for task in _new_done(tasks, already_done, False):
        tid, org = task["task_id"], task["org_id"]
        rows = [d for d in drafts if d.get("task_id") == tid]
        org_row = _org_row(task, orgs)
        if not rows and not org_row:
            rejected.append((tid, "no rows"))
            continue
        problems = []
        if org not in org_ids:
            problems.append(f"org_id '{org}' is not in organizations.csv")
        problems += [f"row {i}: org_id '{r['org_id']}' is not '{org}'" for i, r in enumerate(rows, 1)
                     if r.get("org_id") and r["org_id"] != org]
        blank = [i for i, r in enumerate(rows, 1) if not r.get("review")]
        if blank:
            problems.append(f"review is blank on row(s) {', '.join(map(str, blank))}: write ok, fixed or delete")
        new_rows = staging.prepare([{**r, "program_id": r.get("row_id", "")} for r in rows], org, season, taken, today)
        problems += staging.check_rows(new_rows, org_ids, season) if not problems else []
        existing = {r["program_id"] for r in staged.get(org, [])}
        problems += [f"program_id {r['program_id']} is already used by another task for {org}"
                     for r in new_rows if r["program_id"] in existing]
        fact = None
        if org_row:
            fact = {c: org_row.get(c, "") for c in staging.ORG_STAGING_COLUMNS}
            fact["org_id"] = fact["org_id"] or org
            if fact["org_id"] != org:
                problems.append(f"Orgs row org_id '{fact['org_id']}' is not '{org}'")
            problems += validate_data.check_org_registration(fact, f"Orgs row for {tid}")
        if problems:
            rejected.append((tid, "; ".join(problems)))
            continue
        taken.update(r["program_id"] for r in new_rows)
        if new_rows:
            staged.setdefault(org, []).extend(new_rows)
        if fact:
            facts.append(fact)
    return staged, facts, rejected


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(columns)
        for row in rows:
            w.writerow([row.get(c, "") for c in columns])


def _previous(folder: Path, name: str, columns: list[str], tab: str) -> list[dict]:
    path = folder / f"{name}.csv"
    if not path.exists():
        return []
    try:
        return parse_tab(path.read_text(encoding="utf-8"), columns, tab)
    except SheetError:
        return []


def _read_needs_help(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r.get("task_id", "") for r in csv.DictReader(f)}


def _apply_urls(urls: dict[str, str], path: Path) -> None:
    rows = sources.load(path)
    for org, url in urls.items():
        row = next((r for r in rows if r["org_id"] == org), None)
        if row is None:
            row = {**dict.fromkeys(sources.COLUMNS, ""), "org_id": org, "status_2027": "not_posted"}
            rows.append(row)
        row["listing_url"], row["kind"] = url, sources.guess_kind(url)
        row["notes"] = "; ".join(n for n in row["notes"].split("; ") if n and not n.startswith("fetch failed "))
    sources.save(rows, path)


def pull(download=download_tab, root: Path = ROOT, today: str | None = None, sleep=time.sleep) -> int:
    """Download the tabs, snapshot them, stage finished tasks. Returns 1 (and writes nothing) on a Sheet problem."""
    root = Path(root)
    today = today or dt.date.today().isoformat()
    config = ingest_config.load()
    season = config["season"]
    folder = root / "data" / "staging" / str(season) / SNAPSHOT_DIR

    tabs = {}
    try:
        for i, (tab, columns) in enumerate(TABS):
            if i:
                sleep(sources.DELAY_SECONDS)
            tabs[tab] = parse_tab(download(config["sheet_id"], tab), columns, tab)
    except SheetError as exc:
        print(str(exc))
        return 1
    except OSError as exc:
        print(f"could not download the Sheet: {exc}")
        return 1

    tasks, task_messages, skipped_done = check_tasks(tabs["Tasks"])
    drafts, orgs = tabs["Drafts"], tabs["Orgs"]
    old_tasks = _previous(folder, "tasks", TASK_COLUMNS, "Tasks")
    old_drafts = _previous(folder, "drafts", DRAFT_COLUMNS, "Drafts")
    already_done = ({t["task_id"] for t in old_tasks if t["status"] == "done"}
                    - _read_needs_help(folder / NEEDS_HELP_FILE))

    with open(root / "data" / "organizations.csv", newline="", encoding="utf-8") as f:
        org_ids = {r["org_id"] for r in csv.DictReader(f)}
    programs = root / "data" / "programs.csv"
    existing_ids = set()
    if programs.exists():
        with open(programs, newline="", encoding="utf-8") as f:
            existing_ids = {r["program_id"] for r in csv.DictReader(f)}
    staged, facts, rejected = collect_done(tasks, drafts, orgs, already_done, org_ids, season, today, existing_ids)
    rejected += skipped_done
    urls, find_rejected = find_page_urls(tasks, orgs, already_done)
    rejected += find_rejected
    for task in _new_done(tasks, already_done, True):
        if task["org_id"] not in org_ids and task["task_id"] not in dict(find_rejected):
            rejected.append((task["task_id"], f"org_id '{task['org_id']}' is not in organizations.csv"))
    urls = {o: u for o, u in urls.items() if o in org_ids}

    for line in task_messages:
        print(f"WARNING {line}")
    changes = diff_tasks(old_tasks, tasks)
    print("\n".join(changes) if changes else "no task changes")
    added, removed, edited = diff_drafts(old_drafts, drafts)
    print(f"Drafts rows: {added} added, {removed} removed, {edited} edited")

    for org, rows in staged.items():
        staging.save(org, season, rows, root)
        print(f"staged {len(rows)} row(s) for {org}")
    if facts:
        staging.upsert_org_facts(season, facts, root)
    if urls:
        _apply_urls(urls, root / "data" / "sources.csv")
        for org, url in urls.items():
            print(f"page for {org}: {url}")
    # The snapshot goes last: a crash above leaves the old one, so the next pull redoes the work.
    _write_csv(folder / NEEDS_HELP_FILE, ["task_id", "reason"],
               [{"task_id": t, "reason": r} for t, r in rejected])
    for tab_name, name, columns in (("Tasks", "tasks", TASK_COLUMNS), ("Drafts", "drafts", DRAFT_COLUMNS),
                                    ("Orgs", "orgs", ORG_COLUMNS)):
        _write_csv(folder / f"{name}.csv", columns, tabs[tab_name])
    (folder / "snapshot.json").write_text(json.dumps({"pulled_on": today}) + "\n", encoding="utf-8")
    for tid, reason in rejected:
        print(f"NEEDS HELP {tid}: {reason}")
    return 0


def main(argv, root: Path = ROOT, download=download_tab, today: str | None = None, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(prog="sheet_sync.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull", help="download the Sheet and stage finished tasks")
    ap.parse_args(argv[1:])
    return pull(download=download, root=root, today=today, sleep=sleep)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
