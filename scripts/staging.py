"""Staging area for drafted 2027 rows: data/staging/<season>/<org_id>.csv.

Drafted rows wait here for human review. Only scripts/merge_staging.py moves them into
programs.csv and organizations.csv.

Usage:
  python3 scripts/staging.py add --org ORG_ID --json FILE        FILE: JSON list of row objects
  python3 scripts/staging.py add-org --org ORG_ID --json FILE    FILE: one JSON object of org facts
  python3 scripts/staging.py check [--org ORG_ID]
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

import ingest_config
import validate_data

ROOT = Path(__file__).resolve().parents[1]


def _read_header(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


PROGRAM_COLUMNS = _read_header(ROOT / "data" / "programs.csv")
EXTRA = ["review", "source_url", "source_quote", "extracted_on"]
STAGING_COLUMNS = PROGRAM_COLUMNS + EXTRA
REVIEW_VALUES = {"", "ok", "fixed", "delete"}
ORG_STAGING_COLUMNS = ["org_id", "registration_opens", "registration_opens_early", "registration_notes",
                       "financial_aid_available", "financial_aid_notes", "website"]
ORG_FACTS_FILE = "_organizations.csv"
MAX_SLUG = 50


def slugify(text: str) -> str:
    """Lowercase, runs of non-alphanumerics become one '-', trimmed, at most 50 characters."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:MAX_SLUG].strip("-")


def assign_program_id(org_id: str, name: str, season: int, taken: set[str]) -> str:
    """`<org_id>-<name-slug>-<season>`, with -2, -3... when already taken."""
    base = f"{org_id}-{slugify(name) or 'program'}-{season}"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _season_dir(season: int, root: Path) -> Path:
    return Path(root) / "data" / "staging" / str(season)


def staging_path(org_id: str, season: int, root: Path = ROOT) -> Path:
    return _season_dir(season, root) / f"{org_id}.csv"


def _read_rows(path: Path, columns: list[str]) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{c: row.get(c) or "" for c in columns} for row in csv.DictReader(f)]


def _write_rows(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(columns)
        for row in rows:
            w.writerow([row.get(c, "") for c in columns])


def load(org_id: str, season: int, root: Path = ROOT) -> list[dict]:
    """The organization's staged rows, or [] when it has no staging file."""
    return _read_rows(staging_path(org_id, season, root), STAGING_COLUMNS)


def save(org_id: str, season: int, rows: list[dict], root: Path = ROOT) -> None:
    _write_rows(staging_path(org_id, season, root), STAGING_COLUMNS, rows)


def staged_org_ids(season: int, root: Path = ROOT) -> list[str]:
    """Organizations that have a staging file, sorted."""
    d = _season_dir(season, root)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.csv") if not p.name.startswith("_"))


def prepare(rows: list[dict], org_id: str, season: int, taken: set[str], today: str) -> list[dict]:
    """Fill defaults and a permanent program_id. A row that already has an id keeps it."""
    taken = set(taken)
    out = []
    for raw in rows:
        row = {c: (raw.get(c) or "") for c in STAGING_COLUMNS}
        row["org_id"] = org_id
        row["program_year"] = row["program_year"] or str(season)
        for col, default in (("program_type", "camp"), ("session_type", "day"), ("schedule_type", "weekly"),
                             ("confidence", "likely"), ("extracted_on", today)):
            row[col] = row[col] or default
        if not row["program_id"]:
            row["program_id"] = assign_program_id(org_id, row["program_name"], season, taken)
        taken.add(row["program_id"])
        out.append(row)
    return out


def _parse_date(value: str):
    if not validate_data.DATE_RE.match(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def check_rows(rows: list[dict], org_ids: set[str], season: int | None = None) -> list[str]:
    """Every problem found in the staged rows, one message each."""
    season = season or ingest_config.load()["season"]
    problems, seen = [], {}
    for i, row in enumerate(rows, start=1):
        pid = (row.get("program_id") or "").strip()
        label = f"staging row {i} ({pid[:40] or 'NO_ID'})"
        problems += validate_data.check_program_row(row, org_ids, label)[0]
        if pid:
            if pid in seen:
                problems.append(f"{label}: duplicate program_id (also staging row {seen[pid]})")
            seen.setdefault(pid, i)
        for col in ("start_date", "end_date"):
            value = (row.get(col) or "").strip()
            if not value:
                continue
            parsed = _parse_date(value)
            if parsed is None:
                problems.append(f"{label}: {col} '{value}' is not YYYY-MM-DD")
            elif parsed.year != season:
                problems.append(f"{label}: {col} {value} is outside the {season} season")
        review = (row.get("review") or "").strip()
        if review not in REVIEW_VALUES:
            problems.append(f"{label}: review '{review}' must be blank, ok, fixed or delete")
        if review != "delete" and not (row.get("source_url") or "").strip():
            problems.append(f"{label}: source_url is blank")
    return problems


def load_org_facts(season: int, root: Path = ROOT) -> list[dict]:
    return _read_rows(_season_dir(season, root) / ORG_FACTS_FILE, ORG_STAGING_COLUMNS)


def upsert_org_facts(season: int, rows: list[dict], root: Path = ROOT) -> None:
    """One row per org_id: non-blank new values overwrite stored ones, blanks never erase."""
    stored = {r["org_id"]: r for r in load_org_facts(season, root)}
    for new in rows:
        base = stored.setdefault(new["org_id"], dict.fromkeys(ORG_STAGING_COLUMNS, ""))
        for col in ORG_STAGING_COLUMNS:
            if (new.get(col) or "").strip():
                base[col] = new[col]
    _write_rows(_season_dir(season, root) / ORG_FACTS_FILE, ORG_STAGING_COLUMNS, list(stored.values()))


def _csv_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _fail(problems: list[str]) -> int:
    print("\n".join(problems), file=sys.stderr)
    print("nothing written", file=sys.stderr)
    return 1


def _cmd_add(args, root: Path, season: int, today: str) -> int:
    org_ids = {o["org_id"] for o in _csv_rows(root / "data" / "organizations.csv")}
    if args.org not in org_ids:
        return _fail([f"unknown org_id {args.org!r}"])
    try:
        new = json.loads(Path(args.json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _fail([f"cannot read {args.json}: {exc}"])
    if not isinstance(new, list) or not all(isinstance(r, dict) for r in new):
        return _fail(["the JSON file must be a list of row objects"])
    problems = [f"row {i}: unknown column {k!r}" for i, r in enumerate(new, 1) for k in r if k not in STAGING_COLUMNS]
    problems += [f"row {i}: org_id {r['org_id']!r} is not {args.org!r}" for i, r in enumerate(new, 1)
                 if r.get("org_id") and r["org_id"] != args.org]
    if problems:
        return _fail(problems)
    existing = load(args.org, season, root)
    in_staging = {r["program_id"] for r in existing}
    taken = {r["program_id"] for r in _csv_rows(root / "data" / "programs.csv")} | in_staging
    rows = prepare(new, args.org, season, taken, today)
    problems = [f"{r['program_id']} is already in this organization's staging file" for r in rows if r["program_id"] in in_staging]
    problems += check_rows(rows, org_ids, season)
    if problems:
        return _fail(problems)
    save(args.org, season, existing + rows, root)
    for r in rows:
        print(r["program_id"])
    return 0


def _cmd_add_org(args, root: Path, season: int) -> int:
    org_ids = {o["org_id"] for o in _csv_rows(root / "data" / "organizations.csv")}
    if args.org not in org_ids:
        return _fail([f"unknown org_id {args.org!r}"])
    try:
        obj = json.loads(Path(args.json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _fail([f"cannot read {args.json}: {exc}"])
    if not isinstance(obj, dict):
        return _fail(["the JSON file must be one object"])
    problems = [f"unknown key {k!r}" for k in obj if k not in ORG_STAGING_COLUMNS]
    if obj.get("org_id") not in (None, "", args.org):
        problems.append(f"org_id {obj['org_id']!r} is not {args.org!r}")
    if problems:
        return _fail(problems)
    new = {**obj, "org_id": args.org}
    stored = next((r for r in load_org_facts(season, root) if r["org_id"] == args.org), {})
    combined = {**stored, **{k: v for k, v in new.items() if (v or "").strip()}}
    problems = validate_data.check_org_registration(combined, args.org)
    if problems:
        return _fail(problems)
    upsert_org_facts(season, [new], root)
    print(f"staged facts for {args.org}")
    return 0


def _cmd_check(args, root: Path, season: int) -> int:
    org_ids = {o["org_id"] for o in _csv_rows(root / "data" / "organizations.csv")}
    targets = [args.org] if args.org else staged_org_ids(season, root)
    problems = []
    for org in targets:
        problems += [f"{org}: {p}" for p in check_rows(load(org, season, root), org_ids, season)]
    for row in load_org_facts(season, root):
        if row["org_id"] not in org_ids:
            problems.append(f"{ORG_FACTS_FILE}: unknown org_id {row['org_id']!r}")
        problems += validate_data.check_org_registration(row, f"{ORG_FACTS_FILE} {row['org_id']}")
    print("\n".join(problems) if problems else "no problems")
    return 1 if problems else 0


def main(argv, root: Path = ROOT, today: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog="staging.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "add-org"):
        p = sub.add_parser(name)
        p.add_argument("--org", required=True)
        p.add_argument("--json", required=True)
    sub.add_parser("check").add_argument("--org")
    args = ap.parse_args(argv[1:])
    root = Path(root)
    season = ingest_config.load()["season"]
    today = today or dt.date.today().isoformat()
    if args.cmd == "add":
        return _cmd_add(args, root, season, today)
    if args.cmd == "add-org":
        return _cmd_add_org(args, root, season)
    return _cmd_check(args, root, season)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
