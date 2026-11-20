"""Sync the volunteer Google Sheet with the staging area.

Usage: python3 scripts/sheet_sync.py pull | push | init

push works out which rows to add to the Sheet and which cells to change, and writes files for a maintainer to
paste in by hand (nothing can write to the Sheet yet). init writes the header rows for setting up a new Sheet.

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
from dataclasses import dataclass, field
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
STALE_CLAIM_DAYS = 3
HAND_ENTRY_KINDS = {"rec1", "gated", "manual"}
TASK_MINUTES = {"find_page": 2, "check_draft": 10, "check_changes": 10, "enter_new": 45}
REPEATABLE = ("check_changes", "find_page")
PASTE_FILES = (("Tasks", "_to_paste_tasks.csv", TASK_COLUMNS), ("Drafts", "_to_paste_drafts.csv", DRAFT_COLUMNS),
               ("Orgs", "_to_paste_orgs.csv", ORG_COLUMNS))
CHANGES_FILE = "_changes.txt"
INIT_DIR = "_init"
HOW_TO = ROOT / "docs" / "ingestion" / "volunteer-how-to.md"


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


# ---- push: work out what to put in the Sheet ------------------------------------------------------------

@dataclass
class PushPlan:
    new_tasks: list[dict] = field(default_factory=list)
    new_drafts: list[dict] = field(default_factory=list)
    new_orgs: list[dict] = field(default_factory=list)
    cell_changes: list[tuple[str, str, str, str]] = field(default_factory=list)  # task_id, column, value, reason


def task_for(source: dict, has_draft: bool) -> tuple[str, int] | None:
    """The kind of task a source row needs and how many minutes it takes, or None."""
    status = source.get("status_2027", "")
    if not source.get("listing_url", "").strip() or "fetch failed" in source.get("notes", ""):
        kind = "find_page"
    elif status == "changed":
        kind = "check_changes"
    elif status == "extracted" and has_draft:
        kind = "check_draft"
    elif status == "posted" and (source.get("kind") in HAND_ENTRY_KINDS
                                 or source.get("robots_ai_optout", "") != "FALSE"):
        # A blank robots value means the site was never checked: enter it by hand, never read it with Claude.
        kind = "enter_new"
    else:
        return None
    return kind, TASK_MINUTES[kind]


def task_id(org_id: str, kind: str, season: int, number: int = 1) -> str:
    """<season>-<org_id>-<kind>, with -2, -3... for a repeat of the same kind."""
    base = f"{season}-{org_id}-{kind}"
    return base if number <= 1 else f"{base}-{number}"


def _next_task_id(org_id: str, kind: str, season: int, snapshot: list[dict]) -> str | None:
    """The id for a new task, or None when one is already in the Sheet (or an earlier one is still open)."""
    base = task_id(org_id, kind, season)
    pattern = re.compile(re.escape(base) + r"(?:-(\d+))?")
    same = [(int(m.group(1) or 1), t) for t in snapshot if (m := pattern.fullmatch(t.get("task_id", "")))]
    if not same:
        return base
    if kind not in REPEATABLE or any(t.get("status") != "done" for _, t in same):
        return None
    return task_id(org_id, kind, season, max(n for n, _ in same) + 1)


def _release_stale(tasks: list[dict], today: str, skip: set[str]) -> list[tuple[str, str, str, str]]:
    try:
        today_date = dt.date.fromisoformat(today)
    except ValueError:
        return []
    changes = []
    for t in tasks:
        if t.get("status") != "claimed" or t.get("task_id") in skip:
            continue
        try:
            claimed = dt.date.fromisoformat(t.get("claimed_on", ""))
        except ValueError:
            continue  # blank or unreadable: leave it alone
        if (today_date - claimed).days > STALE_CLAIM_DAYS:
            reason = f"claim from {t['claimed_on']} expired"
            changes += [(t["task_id"], "status", "open", reason), (t["task_id"], "claimed_by", "", reason)]
    return changes


def plan_push(sources_rows, orgs: dict[str, dict], staged: dict[str, list[dict]], snapshot_tasks: list[dict],
              season: int, today: str, needs_help=()) -> PushPlan:
    """What to add to the Sheet and which cells to change. Reads nothing and writes nothing."""
    plan = PushPlan()
    for src in sources_rows:
        org = src["org_id"]
        rows = staged.get(org, [])
        need = task_for(src, bool(rows))
        if not need:
            continue
        kind, minutes = need
        tid = _next_task_id(org, kind, season, snapshot_tasks)
        if tid is None:
            continue
        org_row = orgs.get(org, {})
        plan.new_tasks.append({
            "task_id": tid, "org_id": org, "org_name": org_row.get("org_name", ""), "kind": kind,
            "minutes": str(minutes), "due": "" if kind == "find_page" else src.get("expected_open", ""),
            "source_url": src.get("listing_url", ""), "status": "open", "claimed_by": "", "claimed_on": "",
            "volunteer_notes": ""})
        if kind in ("check_draft", "check_changes"):
            for row in rows:
                draft = {c: row.get(c, "") for c in DRAFT_COLUMNS}
                draft.update(task_id=tid, row_id=row.get("program_id", ""), review="",
                             source_url=row.get("source_url") or src.get("listing_url", ""),
                             source_quote=row.get("source_quote", ""))
                plan.new_drafts.append(draft)
        if kind != "find_page":
            plan.new_orgs.append({**{c: org_row.get(c, "") for c in ORG_COLUMNS}, "task_id": tid, "org_id": org})

    flagged = {}
    present = {t.get("task_id"): t for t in snapshot_tasks}
    for tid, reason in needs_help:
        task = present.get(tid)
        if task and task.get("status") != "needs_help" and tid not in flagged:
            flagged[tid] = reason
    for tid, reason in flagged.items():
        plan.cell_changes += [(tid, "status", "needs_help", "its rows did not pass the checks on the last pull"),
                              (tid, "volunteer_notes", reason, "tells the volunteer what to fix")]
    plan.cell_changes = _release_stale(snapshot_tasks, today, set(flagged)) + plan.cell_changes
    return plan


def paste_cell(value: str) -> str:
    """A cell as it should be pasted: one a spreadsheet would read as a formula gets a leading apostrophe."""
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def write_paste_file(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Rows only, no header, in the tab's column order. Written even when there are no rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        for row in rows:
            w.writerow([paste_cell(row.get(c, "")) for c in columns])


def change_lines(changes, snapshot_tasks: list[dict]) -> list[str]:
    position = {t.get("task_id"): i for i, t in enumerate(snapshot_tasks)}
    lines = []
    for tid, column, value, reason in changes:
        what = f"clear {column}" if value == "" else f"set {column} to {' '.join(value.split())}"
        lines.append(f"Tasks row {position[tid] + 2} ({tid}): {what} \u2014 {reason}")
    return lines


def _read_needs_help_pairs(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [(r.get("task_id", ""), r.get("reason", "")) for r in csv.DictReader(f)]


def _read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def push(root: Path = ROOT, today: str | None = None) -> int:
    """Write the rows to paste into the Sheet and the list of cells to change. Touches nothing else."""
    root = Path(root)
    today = today or dt.date.today().isoformat()
    season = ingest_config.load()["season"]
    folder = root / "data" / "staging" / str(season) / SNAPSHOT_DIR
    data = root / "data"

    org_rows = {r["org_id"]: r for r in _read_csv_rows(data / "organizations.csv")}
    orgs = {k: dict(v) for k, v in org_rows.items()}
    for fact in staging.load_org_facts(season, root):
        for col, val in fact.items():
            if val.strip() and fact["org_id"] in orgs:
                orgs[fact["org_id"]][col] = val
    source_rows = sources.load(data / "sources.csv")
    staged = {org: staging.load(org, season, root) for org in staging.staged_org_ids(season, root)}
    live = {}
    for r in _read_csv_rows(data / "programs.csv"):
        if r.get("program_year") == str(season) and r.get("confidence") != "inactive":
            live.setdefault(r["org_id"], []).append(r)
    for src in source_rows:
        if src["status_2027"] == "changed" and live.get(src["org_id"]):
            staged[src["org_id"]] = live[src["org_id"]]

    snapshot = _previous(folder, "tasks", TASK_COLUMNS, "Tasks")
    plan = plan_push(source_rows, orgs, staged, snapshot, season, today,
                     needs_help=_read_needs_help_pairs(folder / NEEDS_HELP_FILE))

    counts = {}
    for tab, name, columns in PASTE_FILES:
        rows = {"Tasks": plan.new_tasks, "Drafts": plan.new_drafts, "Orgs": plan.new_orgs}[tab]
        write_paste_file(folder / name, columns, rows)
        counts[tab] = len(rows)
    lines = change_lines(plan.cell_changes, snapshot)
    (folder / CHANGES_FILE).write_text("".join(f"{l}\n" for l in lines), encoding="utf-8")

    by_kind = {}
    for t in plan.new_tasks:
        by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
    kinds = f" ({', '.join(f'{n} {k}' for k, n in sorted(by_kind.items()))})" if by_kind else ""
    print(f"Tasks: {counts['Tasks']} row(s) to paste{kinds}")
    print(f"Drafts: {counts['Drafts']} row(s) to paste")
    print(f"Orgs: {counts['Orgs']} row(s) to paste")
    print(f"Cell changes: {len(lines)} (see {CHANGES_FILE})")
    print(f"Files are in {folder}. For each tab: File > Import > Upload the _to_paste file, choose "
          "'Append to current sheet', separator 'Comma', and turn OFF 'Convert text to numbers, dates and formulas'.")
    return 0


def init(root: Path = ROOT) -> int:
    """Write the header rows and the How-to text for setting up the Sheet the first time."""
    root = Path(root)
    season = ingest_config.load()["season"]
    folder = root / "data" / "staging" / str(season) / SNAPSHOT_DIR / INIT_DIR
    for (tab, columns), name in zip(TABS, ("tasks_header.csv", "drafts_header.csv", "orgs_header.csv")):
        _write_csv(folder / name, columns, [])
    (folder / "how_to.txt").write_text(HOW_TO.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote the header rows and how_to.txt to {folder}")
    print("First-time setup of the Google Sheet:")
    print("  1. Name the four tabs exactly: Tasks, Drafts, Orgs, How to")
    print("  2. In each tab, select all cells and set Format > Number > Plain text BEFORE pasting anything "
          "(Google's CSV export otherwise guesses column types and can blank cells or reformat dates)")
    print("  3. Paste each header file into row 1 of its tab: tasks_header.csv into Tasks, drafts_header.csv "
          "into Drafts, orgs_header.csv into Orgs")
    print("  4. Paste the text of how_to.txt into cell A1 of the How to tab")
    print("  5. Run `python3 scripts/sheet_sync.py push`, then import the _to_paste files as it explains")
    return 0


def main(argv, root: Path = ROOT, download=download_tab, today: str | None = None, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(prog="sheet_sync.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull", help="download the Sheet and stage finished tasks")
    sub.add_parser("push", help="write the rows to paste into the Sheet and the cells to change")
    sub.add_parser("init", help="write the header rows for setting up a new Sheet")
    args = ap.parse_args(argv[1:])
    if args.cmd == "push":
        return push(root=root, today=today)
    if args.cmd == "init":
        return init(root=root)
    return pull(download=download, root=root, today=today, sleep=sleep)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
