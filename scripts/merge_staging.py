"""Merge reviewed staging files into data/programs.csv and data/organizations.csv.

The only script allowed to write 2027 rows into those two files.

Usage: python3 scripts/merge_staging.py --org ORG_ID [--org ...] | --all-ready | --org-facts ORG_ID [--org-facts ...]
Then runs validate_data.py and build_data_js.py. A program merge marks each staging file's rows `merged`
and the organizations `merged` in sources.csv. --org-facts applies only those organizations' rows of
_organizations.csv (a registration date known before any sessions are posted). Merged facts rows are removed
from _organizations.csv so a later merge never re-applies them.
"""
import argparse
import csv
import datetime as dt
import io
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import ingest_config
import sources
import staging as st

ROOT = Path(__file__).resolve().parents[1]


def merge_programs(existing: list[dict], staged: list[dict], today: str, season: int) -> tuple[list[dict], dict]:
    """Upsert staged rows into the programs by program_id; existing rows keep their order."""
    out = [dict(r) for r in existing]
    index = {r["program_id"]: i for i, r in enumerate(out)}
    counts = {"added": 0, "updated": 0, "retired": 0}
    for row in staged:
        pid = row["program_id"]
        if (row.get("review") or "").strip() == "delete":
            if pid in index and out[index[pid]].get("confidence") != "inactive":
                out[index[pid]]["confidence"] = "inactive"
                counts["retired"] += 1
            continue
        new = {c: row.get(c, "") for c in st.PROGRAM_COLUMNS}
        new["confidence"], new["verified_date"], new["program_year"] = "confirmed", today, str(season)
        if pid in index:
            out[index[pid]] = new
            counts["updated"] += 1
        else:
            index[pid] = len(out)
            out.append(new)
            counts["added"] += 1
    return out, counts


def merge_orgs(existing: list[dict], staged: list[dict], today: str) -> tuple[list[dict], int]:
    """Copy staged non-blank values over the organization's own columns; return (rows, orgs changed).

    Keys that are not columns of the organization row, and org_id, are ignored. A staged
    org_id missing from `existing` is skipped (the caller reports it), never added.
    """
    out = [dict(r) for r in existing]
    by_id = {r["org_id"]: r for r in out}
    changed = 0
    for row in staged:
        org = by_id.get(row.get("org_id"))
        if org is None:
            continue
        for col, value in row.items():
            if col != "org_id" and col in org and (value or "").strip():
                org[col] = value
        org["verified_date"] = today
        changed += 1
    return out, changed


def org_changes(before: list[dict], after: list[dict]) -> list[str]:
    """One line per organization field that changed (old -> new), plus a warning when a website changes host."""
    old = {r["org_id"]: r for r in before}
    lines = []
    for row in after:
        was = old.get(row["org_id"], {})
        for col, value in row.items():
            if col != "verified_date" and was.get(col, "") != value:
                lines.append(f"  {row['org_id']} {col}: {was.get(col, '')!r} -> {value!r}")
        hosts = [(urlparse(r.get("website") or "").hostname or "").lower().removeprefix("www.") for r in (was, row)]
        if hosts[0] != hosts[1] and all(hosts):
            lines.append(f"  WARNING {row['org_id']} website moves from {hosts[0]} to {hosts[1]}: check it is the organization's own site")
    return lines


def _read_table(path: Path) -> tuple[bytes, list[str], list[dict], str]:
    raw = Path(path).read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    rows = list(reader)
    return raw, list(reader.fieldnames), rows, "\r\n" if b"\r\n" in raw else "\n"


def _render(columns: list[str], rows: list[dict], eol: str) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator=eol)
    w.writerow(columns)
    for row in rows:
        w.writerow([row.get(c, "") for c in columns])
    return buf.getvalue().encode("utf-8")


def run_validate(root: Path) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, str(Path(root) / "scripts" / "validate_data.py")], capture_output=True, text=True)
    return r.returncode == 0, r.stdout + r.stderr


def run_build(root: Path) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, str(Path(root) / "scripts" / "build_data_js.py")], capture_output=True, text=True)
    return r.returncode == 0, r.stdout + r.stderr


def _refusals(org: str, rows: list[dict], org_ids: set[str], season: int, existing: dict, facts: dict) -> list[str]:
    """Why this organization's staging file (and its facts row, if any) cannot be merged; [] when it can."""
    if not rows:
        return [f"{org}: no staging file or it is empty"]
    if any(r["review"].strip() == "merged" for r in rows):
        return [f"{org}: already merged (a finished task on the next pull makes it ready again)"]
    problems = [f"{org}: {p}" for p in st.check_rows(rows, org_ids, season)]
    problems += [f"{org}: staging row {i} ({r['program_id']}) has a blank review" for i, r in enumerate(rows, 1)
                 if not r["review"].strip()]
    problems += [f"{org}: staging row {i} has org_id {r['org_id']!r}" for i, r in enumerate(rows, 1) if r["org_id"] != org]
    for r in rows:
        live = existing.get(r["program_id"])
        if live and live["org_id"] != org:
            problems.append(f"{org}: {r['program_id']} already belongs to {live['org_id']!r}")
        elif live and live.get("program_year") != str(season):
            problems.append(f"{org}: {r['program_id']} is a {live.get('program_year') or 'blank-year'} row; "
                            f"only {season} rows can be changed")
    if org in facts:
        problems += [f"{org}: {p}" for p in st.check_org_facts(facts[org], st.ORG_FACTS_FILE, season)]
    return problems


def _write_checked(root: Path, files: dict, validate, build, blame) -> bool:
    """Write {path: (new bytes, old bytes)}, then validate and build. On any failure put every file back."""
    try:
        for path, (new, _) in files.items():
            path.write_bytes(new)
        ok, output = validate(root)
        stage = "validation"
        if ok:
            ok, output = build(root)
            stage = "build"
    except BaseException:
        for path, (_, old) in files.items():
            path.write_bytes(old)
        raise
    if ok:
        return True
    for path, (_, old) in files.items():
        path.write_bytes(old)
    print(f"{stage} failed; {' and '.join(p.name for p in files)} restored, sources.csv and staging untouched",
          file=sys.stderr)
    blamed = blame(output)
    print(f"organizations whose rows may have caused it: {', '.join(blamed) if blamed else 'unknown'}", file=sys.stderr)
    print(output, file=sys.stderr)
    return False


def merge_org_facts(org_list: list[str], root: Path, validate, build, today: str, season: int) -> int:
    """--org-facts: apply these organizations' _organizations.csv rows on their own, with the same checks."""
    data = root / "data"
    org_raw, org_cols, orgs, org_eol = _read_table(data / "organizations.csv")
    org_ids = {o["org_id"] for o in orgs}
    facts = {r["org_id"]: r for r in st.load_org_facts(season, root)}
    problems = []
    for org in org_list:
        if org not in org_ids:
            problems.append(f"{org}: not in organizations.csv")
        elif org not in facts:
            problems.append(f"{org}: no row in {st.ORG_FACTS_FILE}")
        else:
            problems += [f"{org}: {p}" for p in st.check_org_facts(facts[org], st.ORG_FACTS_FILE, season)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        print("refused; nothing written", file=sys.stderr)
        return 1
    new_orgs, _ = merge_orgs(orgs, [facts[o] for o in org_list], today)
    files = {data / "organizations.csv": (_render(org_cols, new_orgs, org_eol), org_raw)}
    if not _write_checked(root, files, validate, build, lambda out: [o for o in org_list if f"org_id '{o}'" in out or o in out]):
        return 1
    lines = org_changes(orgs, new_orgs)
    print("\n".join(lines) if lines else "no organization fields changed")
    st.remove_org_facts(season, org_list, root)
    print(f"applied organization facts for {', '.join(org_list)}")
    print("Review `git diff data/` and commit when happy.")
    return 0


def main(argv, root: Path = ROOT, validate=None, build=None, today: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog="merge_staging.py", description=__doc__.split("\n")[0])
    ap.add_argument("--org", action="append", default=[], help="merge this organization (repeatable)")
    ap.add_argument("--all-ready", action="store_true",
                    help="merge every reviewed organization not already marked merged")
    ap.add_argument("--org-facts", action="append", default=[], metavar="ORG_ID",
                    help="apply only this organization's _organizations.csv row (repeatable)")
    args = ap.parse_args(argv[1:])
    if [bool(args.org), args.all_ready, bool(args.org_facts)].count(True) != 1:
        ap.error("give exactly one of --org ORG_ID (one or more), --all-ready, or --org-facts ORG_ID (one or more)")
    root, validate, build = Path(root), validate or run_validate, build or run_build
    today = today or dt.date.today().isoformat()
    season = ingest_config.load()["season"]
    if args.org_facts:
        return merge_org_facts(list(dict.fromkeys(args.org_facts)), root, validate, build, today, season)
    data = root / "data"
    sources_path = data / "sources.csv"

    prog_raw, prog_cols, programs, prog_eol = _read_table(data / "programs.csv")
    org_raw, org_cols, orgs, org_eol = _read_table(data / "organizations.csv")
    org_ids = {o["org_id"] for o in orgs}
    existing = {p["program_id"]: p for p in programs}
    facts = {r["org_id"]: r for r in st.load_org_facts(season, root)}
    source_rows = sources.load(sources_path)
    status = {r["org_id"]: r["status_2027"] for r in source_rows}

    chosen, refused = [], []
    if args.all_ready:
        for org in st.staged_org_ids(season, root):
            if status.get(org) == "merged":
                continue
            problems = _refusals(org, st.load(org, season, root), org_ids, season, existing, facts)
            if problems and "already merged" in problems[0]:
                continue
            if problems:
                print(f"skipped {org}: not ready ({problems[0].split(': ', 1)[1]})")
            else:
                chosen.append(org)
    else:
        for org in dict.fromkeys(args.org):
            problems = _refusals(org, st.load(org, season, root), org_ids, season, existing, facts)
            if problems:
                refused += problems
            else:
                chosen.append(org)
    if refused:
        print("\n".join(refused), file=sys.stderr)
        print("refused; nothing written", file=sys.stderr)
        return 1
    if not chosen:
        print("nothing ready to merge")
        return 0

    claimed: dict[str, str] = {}
    clashes = []
    for org in chosen:
        for r in st.load(org, season, root):
            other = claimed.setdefault(r["program_id"], org)
            if other != org:
                clashes.append(f"program_id {r['program_id']} is staged by both {other} and {org}")
    if clashes:
        print("\n".join(clashes), file=sys.stderr)
        print("refused; nothing written", file=sys.stderr)
        return 1

    totals = {"added": 0, "updated": 0, "retired": 0}
    new_programs, new_orgs = programs, orgs
    ids_by_org = {}
    for org in chosen:
        rows = st.load(org, season, root)
        ids_by_org[org] = {r["program_id"] for r in rows}
        new_programs, counts = merge_programs(new_programs, rows, today, season)
        for k in totals:
            totals[k] += counts[k]
        if org in facts:
            new_orgs, _ = merge_orgs(new_orgs, [facts[org]], today)

    files = {data / "programs.csv": (_render(prog_cols, new_programs, prog_eol), prog_raw),
             data / "organizations.csv": (_render(org_cols, new_orgs, org_eol), org_raw)}
    blame = lambda output: [org for org in chosen if f"org_id '{org}'" in output
                            or any(f"({pid[:40]}" in output for pid in ids_by_org[org])]
    if not _write_checked(root, files, validate, build, blame):
        return 1

    for row in source_rows:
        if row["org_id"] in chosen:
            row["status_2027"] = "merged"
    sources.save(source_rows, sources_path)
    for org in chosen:
        st.mark_merged(org, season, root)
    st.remove_org_facts(season, chosen, root)
    for line in org_changes(orgs, new_orgs):
        print(line)
    print(f"merged {', '.join(chosen)}: {totals['added']} added, {totals['updated']} updated, {totals['retired']} retired")
    print("Review `git diff data/` and commit when happy.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
