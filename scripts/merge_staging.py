"""Merge reviewed staging files into data/programs.csv and data/organizations.csv.

The only script allowed to write 2027 rows into those two files.

Usage: python3 scripts/merge_staging.py --org ORG_ID [--org ...] | --all-ready
Then runs validate_data.py and build_data_js.py, and marks the organizations `merged` in sources.csv.
"""
import argparse
import csv
import datetime as dt
import io
import subprocess
import sys
from pathlib import Path

import ingest_config
import sources
import staging as st

ROOT = Path(__file__).resolve().parents[1]


def merge_programs(existing: list[dict], staged: list[dict], today: str) -> tuple[list[dict], dict]:
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
        new["confidence"], new["verified_date"] = "confirmed", today
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


def _refusals(org: str, rows: list[dict], org_ids: set[str], season: int, existing_owner: dict) -> list[str]:
    if not rows:
        return [f"{org}: no staging file or it is empty"]
    problems = [f"{org}: {p}" for p in st.check_rows(rows, org_ids, season)]
    problems += [f"{org}: staging row {i} ({r['program_id']}) has a blank review" for i, r in enumerate(rows, 1)
                 if not r["review"].strip()]
    problems += [f"{org}: staging row {i} has org_id {r['org_id']!r}" for i, r in enumerate(rows, 1) if r["org_id"] != org]
    problems += [f"{org}: {r['program_id']} already belongs to {existing_owner[r['program_id']]!r}"
                 for r in rows if existing_owner.get(r["program_id"], org) != org]
    return problems


def main(argv, root: Path = ROOT, validate=None, build=None, today: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog="merge_staging.py", description=__doc__.split("\n")[0])
    ap.add_argument("--org", action="append", default=[], help="merge this organization (repeatable)")
    ap.add_argument("--all-ready", action="store_true",
                    help="merge every reviewed organization not already marked merged")
    args = ap.parse_args(argv[1:])
    if bool(args.org) == args.all_ready:
        ap.error("give --org ORG_ID (one or more) or --all-ready, not both")
    root, validate, build = Path(root), validate or run_validate, build or run_build
    today = today or dt.date.today().isoformat()
    season = ingest_config.load()["season"]
    data = root / "data"
    sources_path = data / "sources.csv"

    prog_raw, prog_cols, programs, prog_eol = _read_table(data / "programs.csv")
    org_raw, org_cols, orgs, org_eol = _read_table(data / "organizations.csv")
    org_ids = {o["org_id"] for o in orgs}
    owner = {p["program_id"]: p["org_id"] for p in programs}
    source_rows = sources.load(sources_path)
    status = {r["org_id"]: r["status_2027"] for r in source_rows}

    chosen, refused = [], []
    if args.all_ready:
        for org in st.staged_org_ids(season, root):
            if status.get(org) == "merged":
                continue
            problems = _refusals(org, st.load(org, season, root), org_ids, season, owner)
            if problems:
                print(f"skipped {org}: not ready ({problems[0].split(': ', 1)[1]})")
            else:
                chosen.append(org)
    else:
        for org in dict.fromkeys(args.org):
            problems = _refusals(org, st.load(org, season, root), org_ids, season, owner)
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

    staged_orgs = {r["org_id"]: r for r in st.load_org_facts(season, root)}
    totals = {"added": 0, "updated": 0, "retired": 0}
    new_programs, new_orgs = programs, orgs
    ids_by_org = {}
    for org in chosen:
        rows = st.load(org, season, root)
        ids_by_org[org] = {r["program_id"] for r in rows}
        new_programs, counts = merge_programs(new_programs, rows, today)
        for k in totals:
            totals[k] += counts[k]
        if org in staged_orgs:
            new_orgs, changed = merge_orgs(new_orgs, [staged_orgs[org]], today)
            if not changed:
                print(f"note: organization facts for {org} not applied (org_id not in organizations.csv)")

    try:
        (data / "programs.csv").write_bytes(_render(prog_cols, new_programs, prog_eol))
        (data / "organizations.csv").write_bytes(_render(org_cols, new_orgs, org_eol))
        ok, output = validate(root)
        if ok:
            ok, output = build(root)
            stage = "build"
        else:
            stage = "validation"
    except BaseException:
        (data / "programs.csv").write_bytes(prog_raw)
        (data / "organizations.csv").write_bytes(org_raw)
        raise
    if not ok:
        (data / "programs.csv").write_bytes(prog_raw)
        (data / "organizations.csv").write_bytes(org_raw)
        blamed = [org for org in chosen if org in output or any(pid in output for pid in ids_by_org[org])]
        print(f"{stage} failed; programs.csv and organizations.csv restored, sources.csv untouched", file=sys.stderr)
        print(f"organizations whose rows may have caused it: {', '.join(blamed) if blamed else 'unknown'}", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    for row in source_rows:
        if row["org_id"] in chosen:
            row["status_2027"] = "merged"
    sources.save(source_rows, sources_path)
    print(f"merged {', '.join(chosen)}: {totals['added']} added, {totals['updated']} updated, {totals['retired']} retired")
    print("Review `git diff data/` and commit when happy.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
