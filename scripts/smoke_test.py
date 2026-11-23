#!/usr/bin/env python3
"""Headless-Chrome smoke test: serves the site with a fixture data.js and
checks the rendered DOM. Exit 0 = all pass, 1 = a check failed, 2 = no Chrome.

Set CHROME to override the browser path.
"""
import functools
import http.server
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "data.fixture.js"
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

TASKS_HEADER = "task_id,org_id,org_name,kind,minutes,due,source_url,status,claimed_by,claimed_on,volunteer_notes\n"
TASKS_FIXTURE = (
    TASKS_HEADER +
    "t1,fixture-org,Typed Alpha,find_page,2,,,open,,,\n"
    ",,,,,,,,,,\n"
    "t2,reg-org,<i>Evil</i> Org,check_draft,10,2027-02-04,https://example.org/b,open,,,\n"
    "t3,fixture-org,Gamma Camp,enter_new,45,,,open,,,\n"
    "t4,reg-org,Delta Camp,check_draft,10,,,claimed,Zed Volunteer,2027-01-12,\n"
    "t5,not-an-org,Spam Org,find_page,2,,,open,,,\n"
)

failures = []
_state = {"port": None, "chrome": None, "site": None}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def find_chrome():
    env = os.environ.get("CHROME")
    if env:
        return env
    if os.path.exists(MAC_CHROME):
        return MAC_CHROME
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def check(name, condition):
    """Record a failure and keep going."""
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)


def dump_dom(path, drop_uids=(), replace=()):
    """Rendered HTML of http://127.0.0.1:<port>/<path>.

    drop_uids: programs to remove from the served data.js before loading.
    replace: (old, new) text swaps applied to the served data.js.
    """
    data_js = Path(_state["site"]) / "data.js"
    text = FIXTURE.read_text(encoding="utf-8")
    for uid in drop_uids:
        text = re.sub(r'  \{\n(?:(?!\n  \}).)*?"uid": "%s".*?\n  \},?\n' % re.escape(uid), "", text, flags=re.S)
    for old, new in replace:
        text = text.replace(old, new)
    data_js.write_text(text, encoding="utf-8")
    with tempfile.TemporaryDirectory() as profile:
        result = subprocess.run(
            [
                _state["chrome"], "--headless=new", "--disable-gpu",
                "--virtual-time-budget=5000", "--dump-dom",
                f"--user-data-dir={profile}",
                f"http://127.0.0.1:{_state['port']}/{path}",
            ],
            capture_output=True, text=True, timeout=120,
        )
    return result.stdout


def stage_site(dest):
    for pattern in ("*.html", "*.js", "*.css", "favicon.svg"):
        for f in ROOT.glob(pattern):
            shutil.copy(f, dest)
    shutil.copy(FIXTURE, Path(dest) / "data.js")
    (Path(dest) / "data").mkdir(exist_ok=True)
    shutil.copy(ROOT / "data" / "ingest_config.json", Path(dest) / "data" / "ingest_config.json")
    sheet = Path(dest) / "data" / "staging" / "2027" / "sheet"
    sheet.mkdir(parents=True)
    (sheet / "tasks.csv").write_text(TASKS_FIXTURE, encoding="utf-8")
    (sheet / "snapshot.json").write_text('{"pulled_on": "2027-01-12"}', encoding="utf-8")


def main():
    for page in ["index", "afterschool", "about", "data", "hackathon", "admin", "volunteer"]:
        html = (ROOT / f"{page}.html").read_text(encoding="utf-8")
        check(f"favicon link: {page}", 'rel="icon" href="favicon.svg"' in html)
    check("org csv has registration columns", "registration_opens,registration_opens_early,registration_notes," in (ROOT / "data/organizations.csv").read_text(encoding="utf-8").splitlines()[0])
    check("favicon file exists", (ROOT / "favicon.svg").is_file())
    check("selector uses CSS.escape", "CSS.escape(uid)" in (ROOT / "app.js").read_text(encoding="utf-8"))
    chrome = find_chrome()
    if not chrome:
        print("Chrome not found; set CHROME to its path", file=sys.stderr)
        return 2
    _state["chrome"] = chrome

    with tempfile.TemporaryDirectory() as site:
        stage_site(site)
        _state["site"] = site
        handler = functools.partial(QuietHandler, directory=site)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        _state["port"] = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            dom = dump_dom("afterschool.html")
            check("afterschool: count line", "of <strong>2</strong> afterschool programs" in dom)
            check("afterschool: two cards", dom.count('class="program-card') == 2)
            check("reg list: camps page only", 'id="regSoon"' not in dom)
            dom = dump_dom("index.html")
            check("camps: count line", "of <strong>5</strong> camps" in dom)
            check("reg list: shown", 'id="regSoon" class="reg-soon"' in dom and "Registration opening soon" in dom)
            check("reg list: org and dates", "Reg Org" in dom and "Jan 28" in dom and "Feb 4" in dom)
            check("reg list: note escaped", "&lt;b&gt;Residents&lt;/b&gt; first" in dom)
            check("xss: no injected img element", '<img src="x"' not in dom)
            check("xss: title untouched", "<title>Summer Camps" in dom)
            check("xss: name shown as text", "&lt;img src=x" in dom)
            check("xss: description shown as text", "&lt;b&gt;bold&lt;/b&gt;" in dom)
            check("unsafe url: no javascript link", 'href="javascript:' not in dom)
            check("safe url: link kept", 'href="https://example.org/camp"' in dom)
            check("season: superseded row hidden, last-season row shown", "Showing <strong>4</strong> of <strong>5</strong> camps" in dom)
            check("season: notice text", "Listings marked 2001 are last season's, shown for reference until that camp posts new dates." in dom)
            check("season: card chip agrees with notice", '<span class="year-chip">2001</span>' in dom)
            check("season: notice visible", 'id="seasonNotice" class="season-notice"' in dom)
            dom = dump_dom("index.html", drop_uids=["camp-past"])
            check("season: no last-season rows, notice hidden", 'id="seasonNotice" class="season-notice hidden"' in dom)
            dom = dump_dom("index.html#map")
            check("map: note counts programs with no usable town", "2 results have no mapped town and are not shown on the map." in dom)
            dom = dump_dom("admin.html")
            check("admin: table renders", dom.count("<tr") >= 6)
            check("admin: no banner without local edits", 'id="localEditsBanner"' not in dom)
            check("admin: not indexed", '<meta name="robots" content="noindex"' in dom)
            check("admin: editing is off without the dev server", "Editing is off: this page can save only through the local dev server." in dom)
            row_buttons = [b for b in re.findall(r'<button class="btn-icon (?:edit|del)"[^>]*>', dom) if "${" not in b]
            check("admin: buttons disabled without the dev server", 'id="btnSaveAll" disabled' in dom and 'id="btnAdd" disabled' in dom
                  and len(row_buttons) >= 10 and all("disabled" in b for b in row_buttons))
            check("nav: admin link on this machine", 'href="admin.html"' in dom)
            check("nav: admin link only for localhost", "['localhost', '127.0.0.1'].includes(location.hostname)" in (ROOT / "nav.js").read_text(encoding="utf-8"))
            dom = dump_dom("about.html")
            check("about: counts come from the data", '<span data-count="programs">7</span>' in dom and '<span data-count="afterschool">2</span>' in dom)
            dom = dump_dom("data.html")
            check("data: counts come from the data", '<span data-count="camps">5</span>' in dom and 'data-count="orgs">…' not in dom)
            dom = dump_dom("afterschool.html")
            check("afterschool: dead filters removed", "STARS" not in dom and 'id="filterStatus"' not in dom)
            dom = dump_dom("afterschool.html")
            check("season: undated category, notice hidden", 'id="seasonNotice" class="season-notice hidden"' in dom)
            dom = dump_dom("volunteer.html?source=snapshot")
            check("board: groups", all(h in dom for h in ("5 minutes or less", "6 to 15 minutes", "More than 15 minutes")))
            check("board: progress", "0 of 4 tasks done · 1 in progress" in dom)
            check("board: snapshot note", "Showing the last saved copy. Saved Jan 12." in dom)
            check("board: How to link goes to the How to tab", 'id="howToLink" href="https://docs.google.com/spreadsheets/d/' in dom and "/edit#gid=80728566" in dom)
            check("nav: no Hackathon link", 'href="hackathon.html"' not in dom)
            check("board: sheet link", "edit#gid=0&amp;range=A2" in dom or "edit#gid=0&range=A2" in dom)
            check("board: link keeps its row after a blank row", "range=A4" in dom.replace("&amp;", "&") and "range=A3" not in dom.replace("&amp;", "&"))
            board = dom.split('id="board"', 1)[-1].split('id="howItWorks"', 1)[0]
            check("board: names not shown", "Zed Volunteer" not in dom)
            check("board: claimed task not listed", board.count('class="task-item"') == 3)
            check("board: names come from the site's data", "Reg Org" in board and "Fixture Org" in board)
            check("board: Sheet-typed names not shown", not any(t in dom for t in ("Evil", "Typed Alpha", "Gamma Camp", "Delta Camp", "Spam Org")))
            check("board: unknown organization dropped", "range=A7" not in dom.replace("&amp;", "&"))
            check("board: due date", "Expected around Feb 4" in dom and "Registration opens" not in board)
            check("board: link opens new tab safely", 'target="_blank"' in dom and 'rel="noopener"' in dom)
            check("board: season programs sentence", "No 2027 programs are live on the site yet." in dom)
            check("board: how this works", "How this works" in dom and "Art Camp, July 5-9" in dom)
            dom = dump_dom("volunteer.html?source=snapshot", replace=(('"programYear": "2099"', '"programYear": "2027"'),))
            check("board: season programs counted", "1 2027 program is live on the site." in dom)
            tasks_csv = Path(site) / "data" / "staging" / "2027" / "sheet" / "tasks.csv"
            tasks_csv.write_text(TASKS_HEADER + "".join(f"t{i},fixture-org,,find_page,2,,,open,,,\n" for i in range(53)), encoding="utf-8")
            dom = dump_dom("volunteer.html?source=snapshot")
            check("board: a group shows at most 50 tasks", dom.count('class="task-item"') == 50 and "and 3 more" in dom)
            tasks_csv.write_text(TASKS_FIXTURE, encoding="utf-8")
            dom = dump_dom("index.html")
            check("nav: volunteer link", 'href="volunteer.html"' in dom)
            (Path(site) / "data" / "staging" / "2027" / "sheet" / "snapshot.json").unlink()
            dom = dump_dom("volunteer.html?source=snapshot")
            check("board: note without date", "Showing the last saved copy." in dom and "Saved " not in dom)
            tasks_csv.write_text(TASKS_HEADER, encoding="utf-8")
            dom = dump_dom("volunteer.html?source=snapshot")
            check("board: no tasks yet", "No tasks yet. New ones appear here as camps post their 2027 listings." in dom)
            check("board: no error text", "rror" not in dom.split('id="board"', 1)[-1] and "could not be loaded" not in dom)
            tasks_csv.unlink()
            dom = dump_dom("volunteer.html?source=snapshot")
            check("board: a failed load is not an empty list", "The task list could not be loaded right now." in dom and "No tasks yet" not in dom)
            check("board: failed load shows no groups", 'class="task-group"' not in dom)
            (Path(site) / "data" / "ingest_config.json").unlink()
            dom = dump_dom("volunteer.html")
            check("board: no config message", "could not be loaded" in dom)
        finally:
            server.shutdown()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
