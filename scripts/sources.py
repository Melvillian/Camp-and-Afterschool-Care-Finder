"""The list of organization pages watched for new-season camp listings (data/sources.csv)."""
import csv
import datetime as dt
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SOURCES_CSV = ROOT / "data" / "sources.csv"
ORGS_CSV = ROOT / "data" / "organizations.csv"
PROGRAMS_CSV = ROOT / "data" / "programs.csv"

COLUMNS = ["org_id", "listing_url", "kind", "robots_ai_optout", "expected_open",
           "last_checked", "last_hash", "status_2027", "notes"]
KINDS = {"html", "pdf", "rec1", "gated", "manual"}
STATUSES = {"not_posted", "posted", "extracted", "merged", "changed", "not_running"}
ROBOTS_VALUES = {"", "TRUE", "FALSE"}
AI_CRAWLERS = ("gptbot", "claudebot", "anthropic-ai", "ccbot", "google-extended", "perplexitybot")

USER_AGENT = "CampFinderVT/1.0 (+https://github.com/kefortney/Camp-and-Afterschool-Care-Finder)"
DELAY_SECONDS = 2
TIMEOUT_SECONDS = 20


def load(path=SOURCES_CSV) -> list[dict]:
    """Return the source rows, or an empty list when the file does not exist yet."""
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{c: row.get(c) or "" for c in COLUMNS} for row in csv.DictReader(f)]


def save(rows: list[dict], path=SOURCES_CSV) -> None:
    """Write the rows with the columns in COLUMNS order."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLUMNS)
        for row in rows:
            w.writerow([row.get(c, "") for c in COLUMNS])


def check(rows: list[dict], org_ids: set[str]) -> list[str]:
    """Return a message for every problem found in the rows."""
    problems, seen = [], set()
    for i, row in enumerate(rows, start=2):
        org = row.get("org_id", "")
        where = f"row {i} ({org})"
        if org not in org_ids:
            problems.append(f"{where}: unknown org_id")
        if org in seen:
            problems.append(f"{where}: duplicate org_id")
        seen.add(org)
        if row.get("kind") not in KINDS:
            problems.append(f"{where}: bad kind {row.get('kind')!r}")
        if row.get("status_2027") not in STATUSES:
            problems.append(f"{where}: bad status_2027 {row.get('status_2027')!r}")
        if row.get("robots_ai_optout", "") not in ROBOTS_VALUES:
            problems.append(f"{where}: bad robots_ai_optout {row.get('robots_ai_optout')!r}")
        opens = row.get("expected_open", "")
        if opens:
            try:
                dt.date.fromisoformat(opens)
            except ValueError:
                problems.append(f"{where}: bad expected_open {opens!r}")
        url = row.get("listing_url", "")
        if url and urlparse(url).scheme not in ("http", "https"):
            problems.append(f"{where}: listing_url is not http(s)")
    return problems


def pick_listing_url(org: dict, program_urls: list[str]) -> str:
    """Most common non-blank program URL (first seen wins ties), else the website, else ''."""
    urls = [u for u in program_urls if u]
    if urls:
        counts = Counter(urls)
        best = max(counts.values())
        return next(u for u in urls if counts[u] == best)
    return org.get("website", "") or ""


def guess_kind(url: str) -> str:
    """Guess how a listing page should be read."""
    if not url:
        return "manual"
    parsed = urlparse(url)
    if parsed.path.lower().endswith(".pdf"):
        return "pdf"
    if (parsed.hostname or "") in ("secure.rec1.com", "summerbtvparks.com"):
        return "rec1"
    return "html"


def names_ai_crawler(robots_txt: str) -> bool:
    """True when any User-agent line names a known AI crawler."""
    for line in robots_txt.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "user-agent" and value.strip().lower() in AI_CRAWLERS:
            return True
    return False


def _read_csv(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def seed(path=SOURCES_CSV) -> int:
    """Add a row for every organization not yet listed; return how many were added."""
    rows = load(path)
    have = {r["org_id"] for r in rows}
    urls: dict[str, list[str]] = {}
    for p in _read_csv(PROGRAMS_CSV):
        urls.setdefault(p["org_id"], []).append(p["registration_url"])
    added = 0
    for org in _read_csv(ORGS_CSV):
        if org["confidence"] == "inactive" or org["org_id"] in have:
            continue
        url = pick_listing_url(org, urls.get(org["org_id"], []))
        rows.append({**dict.fromkeys(COLUMNS, ""), "org_id": org["org_id"], "listing_url": url,
                     "kind": guess_kind(url), "status_2027": "not_posted"})
        added += 1
    save(rows, path)
    return added


def fetch_robots(host: str) -> str:
    """Network: return the host's robots.txt text (raises on failure). Tests replace this."""
    req = urllib.request.Request(f"https://{host}/robots.txt", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8", errors="replace")


def robots(path=SOURCES_CSV, fetch=fetch_robots, sleep=time.sleep) -> dict[str, str]:
    """Fill robots_ai_optout per distinct host; return host -> value ('' on failure)."""
    rows = load(path)
    results: dict[str, str] = {}
    for row in rows:
        host = urlparse(row["listing_url"]).hostname
        if not host or host in results:
            continue
        if results:
            sleep(DELAY_SECONDS)
        try:
            results[host] = "TRUE" if names_ai_crawler(fetch(host)) else "FALSE"
        except Exception as exc:  # any failure leaves the flag blank
            print(f"robots.txt failed for {host}: {exc}", file=sys.stderr)
            results[host] = ""
    for row in rows:
        host = urlparse(row["listing_url"]).hostname
        if host:
            row["robots_ai_optout"] = results[host]
    save(rows, path)
    return results


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "seed":
        print(f"added {seed()} rows; {len(load())} total")
    elif cmd == "robots":
        res = robots()
        print(f"{len(res)} hosts: {sum(v == 'TRUE' for v in res.values())} name AI crawlers, "
              f"{sum(v == '' for v in res.values())} failed")
    elif cmd == "check":
        problems = check(load(), {o["org_id"] for o in _read_csv(ORGS_CSV)})
        print("\n".join(problems) if problems else "no problems")
        return 1 if problems else 0
    else:
        print("usage: sources.py seed|robots|check", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
