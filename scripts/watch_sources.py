"""Watch the organization pages in data/sources.csv for new-season listings and later changes.

Usage: python3 scripts/watch_sources.py [--org ORG_ID] [--limit N] [--dry-run] [--force]
"""
import argparse
import datetime as dt
import hashlib
import re
import sys
import time
import urllib.error
import urllib.request
from collections import namedtuple
from html.parser import HTMLParser
from urllib.parse import urlparse

import ingest_config
import sources

USER_AGENT = sources.USER_AGENT
DELAY_SECONDS = sources.DELAY_SECONDS
TIMEOUT_SECONDS = sources.TIMEOUT_SECONDS
MAX_BYTES = 10_000_000
RECHECK_DAYS = 6
SAVE_EVERY = 10

FetchResult = namedtuple("FetchResult", "ok status final_url content_type body error")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def visible_text(html: str) -> str:
    """The text a reader sees: tags, scripts and styles removed, whitespace collapsed."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def content_hash(data) -> str:
    """First 16 hex characters of the SHA-256 of the bytes (or UTF-8 text)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def fetch(url: str) -> FetchResult:
    """Network: GET the url. Never raises for HTTP or network trouble; returns ok=False instead."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return FetchResult(True, resp.status, resp.geturl(), resp.headers.get("Content-Type", ""),
                               resp.read(MAX_BYTES), "")
    except urllib.error.HTTPError as exc:
        return FetchResult(False, exc.code, url, "", b"", f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return FetchResult(False, 0, url, "", b"", str(getattr(exc, "reason", exc)) or type(exc).__name__)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _mentions(text, season: int) -> bool:
    """True when the season year appears as a whole number (not inside a longer digit run)."""
    pattern = rf"(?<!\d){season}(?!\d)"
    return bool(re.search(pattern.encode() if isinstance(text, bytes) else pattern, text))


def _note_failure(notes: str, today: str, reason: str) -> str:
    kept = [n for n in notes.split("; ") if n and not n.startswith("fetch failed ")]
    kept.append(f"fetch failed {today}: {reason.replace(';', ',')}")
    return "; ".join(kept)


def assess(row: dict, result: FetchResult, season: int, today: str):
    """Return (updated row, message or None). The input row is not modified."""
    row = dict(row)
    org = row["org_id"]
    reason = None
    if not result.ok:
        reason = result.error or f"HTTP {result.status}"
    elif _host(result.final_url) != _host(row["listing_url"]):
        reason = f"redirected to {urlparse(result.final_url).hostname}"
    if reason:
        row["notes"] = _note_failure(row.get("notes", ""), today, reason)
        return row, f"NEEDS A PERSON: {org} ({row['listing_url']}) {reason}"

    body = result.body.encode("utf-8") if isinstance(result.body, str) else result.body
    if row["kind"] == "pdf":
        digest = content_hash(body)
        mentions = _mentions(result.final_url, season) or _mentions(body, season)
    else:
        text = visible_text(body.decode("utf-8", errors="replace"))
        digest = content_hash(text)
        mentions = _mentions(text, season)
    previous = row.get("last_hash", "")
    row["last_checked"], row["last_hash"] = today, digest
    if row["status_2027"] == "not_posted" and mentions:
        row["status_2027"] = "posted"
        return row, f"POSTED: {org}"
    if row["status_2027"] == "merged" and previous and digest != previous:
        row["status_2027"] = "changed"
        return row, f"CHANGED: {org}"
    return row, None


def _due(row: dict, today: dt.date, force: bool) -> bool:
    if force or not row["last_checked"]:
        return True
    try:
        return (today - dt.date.fromisoformat(row["last_checked"])).days > RECHECK_DAYS
    except ValueError:
        return True


def main(argv, fetch=fetch, sleep=time.sleep, today=None, path=sources.SOURCES_CSV) -> int:
    ap = argparse.ArgumentParser(prog="watch_sources.py", description=__doc__.split("\n")[0])
    ap.add_argument("--org", help="check only this org_id")
    ap.add_argument("--limit", type=int, help="check at most this many pages")
    ap.add_argument("--dry-run", action="store_true", help="write nothing")
    ap.add_argument("--force", action="store_true", help="also check pages checked in the last 6 days")
    args = ap.parse_args(argv[1:])
    today = today or dt.date.today().isoformat()
    season = ingest_config.load()["season"]
    rows = sources.load(path)
    checked = skipped = recent = failed = 0
    messages = []

    def save():
        if not args.dry_run:
            sources.save(rows, path)

    try:
        for i, row in enumerate(rows):
            if args.org and row["org_id"] != args.org:
                continue
            if not row["listing_url"] or row["kind"] not in ("html", "pdf"):
                skipped += 1
                continue
            if not _due(row, dt.date.fromisoformat(today), args.force):
                recent += 1
                continue
            if args.limit is not None and checked >= args.limit:
                break
            if checked:
                sleep(DELAY_SECONDS)
            checked += 1
            try:
                result = fetch(row["listing_url"])
            except Exception as exc:  # one page must never stop the run
                result = FetchResult(False, 0, row["listing_url"], "", b"", f"{type(exc).__name__}: {exc}")
            try:
                rows[i], msg = assess(row, result, season, today)
            except Exception as exc:
                rows[i], msg = assess(row, FetchResult(False, 0, row["listing_url"], "", b"", f"{type(exc).__name__}: {exc}"), season, today)
            if msg:
                failed += msg.startswith("NEEDS A PERSON")
                messages.append(msg)
                print(msg, flush=True)
            if checked % SAVE_EVERY == 0:
                save()
    finally:
        save()
    print(f"checked {checked}, {len(messages) - failed} news, {failed} need a person, "
          f"{skipped} skipped (no checkable page), {recent} checked recently"
          f"{' (dry run: nothing written)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
